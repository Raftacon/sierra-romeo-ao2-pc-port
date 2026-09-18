// Hold real GPU copies behind a CPU-signaled queue gate while the production
// eviction method removes the sole cache reference to their destination.
#include <windows.h>
#include <d3d12.h>
#include <wrl/client.h>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <unordered_map>
#include <utility>

using Microsoft::WRL::ComPtr;
void require(bool value, const char* why) {
  if (!value) throw std::runtime_error(why);
}
struct TrackedResource {
  ID3D12Resource* resource = nullptr;
  unsigned releases = 0, unmaps = 0;
  ~TrackedResource() { if (resource) resource->Release(); }
  void Release() { ++releases; resource->Release(); resource = nullptr; }
  void Unmap(unsigned subresource, const D3D12_RANGE* range) {
    ++unmaps; resource->Unmap(subresource, range);
  }
};
#define REXGPU_INFO(...) ((void)0)
struct D3D12CommandProcessor {
#define ID3D12Resource TrackedResource
#include "readback_retirement_members.inc"
#undef ID3D12Resource
  uint64_t frame_current_ = 100, submission_current_ = 1, submission_completed_ = 0;
  ID3D12Fence* submission_fence_ = nullptr;
  std::deque<std::pair<uint64_t, TrackedResource*>> resources_for_deletion_;
  uint64_t GetCurrentSubmission() { return submission_current_; }
  void Drain() {
#include "readback_retirement_drain.inc"
  }
};
#include "readback_retirement_fixed.inc"

struct Event {
  HANDLE value = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  ~Event() { if (value) CloseHandle(value); }
};
void Wait(ID3D12Fence* fence, uint64_t value, HANDLE event) {
  require(SUCCEEDED(fence->SetEventOnCompletion(value, event)), "register fence completion");
  require(WaitForSingleObject(event, 5000) == WAIT_OBJECT_0, "GPU completion timeout");
  const auto completed = fence->GetCompletedValue();
  require(completed != UINT64_MAX && completed >= value, "invalid GPU completion");
}
struct QueueCleanup {
  ID3D12CommandQueue* queue;
  ID3D12Fence* gate;
  ID3D12Fence* done;
  HANDLE event;
  ~QueueCleanup() {
    // Even an assertion failure must unblock and drain this owned queue before
    // any upload/readback buffer, allocator or command list is destroyed.
    if (FAILED(gate->Signal(1)) || FAILED(queue->Signal(done, 2))) std::terminate();
    try { Wait(done, 2, event); } catch (...) { std::terminate(); }
  }
};
int main() {
  try {
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> commands;
    ComPtr<ID3D12Fence> gate, done;
    ComPtr<ID3D12Resource> upload, readback;
    require(SUCCEEDED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device))), "create device");
    D3D12_COMMAND_QUEUE_DESC queue_desc{};
    queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    require(SUCCEEDED(device->CreateCommandQueue(&queue_desc, IID_PPV_ARGS(&queue))), "create queue");
    require(SUCCEEDED(device->CreateCommandAllocator(queue_desc.Type, IID_PPV_ARGS(&allocator))), "create allocator");
    require(SUCCEEDED(device->CreateCommandList(0, queue_desc.Type, allocator.Get(), nullptr, IID_PPV_ARGS(&commands))), "create list");
    require(SUCCEEDED(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&gate))), "create gate");
    require(SUCCEEDED(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&done))), "create fence");
    constexpr size_t bytes = 4096;
    D3D12_RESOURCE_DESC desc{};
    desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width = bytes; desc.Height = 1; desc.DepthOrArraySize = 1;
    desc.MipLevels = 1; desc.SampleDesc.Count = 1; desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_UPLOAD;
    heap.CreationNodeMask = heap.VisibleNodeMask = 1;
    require(SUCCEEDED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
        D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&upload))), "create upload");
    heap.Type = D3D12_HEAP_TYPE_READBACK;
    require(SUCCEEDED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
        D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback))), "create readback");
    void* data = nullptr;
    D3D12_RANGE no_read{0, 0}, full{0, bytes};
    require(SUCCEEDED(upload->Map(0, &no_read, &data)), "map upload");
    for (size_t i = 0; i < bytes; ++i) static_cast<uint8_t*>(data)[i] = uint8_t(i * 37 + 19);
    upload->Unmap(0, &full);
    require(SUCCEEDED(readback->Map(0, &full, &data)), "map readback");
    TrackedResource destination;
    destination.resource = readback.Detach(); // sole application owning reference
    D3D12CommandProcessor p;
    p.submission_fence_ = done.Get();
    std::unordered_map<uint64_t, D3D12CommandProcessor::ReadbackBuffer> map;
    for (unsigned i = 0; i <= p.kMaxReadbackBuffers; ++i) map[i].last_used_frame = 100;
    auto& slot = map.begin()->second;
    slot.buffers[0] = &destination; slot.mapped_data[0] = data;
    slot.sizes[0] = bytes; slot.submission_written[0] = 1;
    commands->CopyBufferRegion(destination.resource, 0, upload.Get(), 0, bytes);
    require(SUCCEEDED(commands->Close()), "close list");
    Event event; require(event.value != nullptr, "create event");
    QueueCleanup cleanup{queue.Get(), gate.Get(), done.Get(), event.value};
    require(SUCCEEDED(queue->Wait(gate.Get(), 1)), "gate queue");
    ID3D12CommandList* lists[] = {commands.Get()};
    queue->ExecuteCommandLists(1, lists);
    require(SUCCEEDED(queue->Signal(done.Get(), 1)), "signal copy completion");
    require(done->GetCompletedValue() == 0, "copy was not pending");
    p.EvictOldReadbackBuffers(map); p.Drain();
    require(map.size() == p.kMaxReadbackBuffers && destination.unmaps == 1 &&
        destination.releases == 0 && p.resources_for_deletion_.size() == 1,
        "eviction did not retain pending destination");
    require(SUCCEEDED(gate->Signal(1)), "release gate");
    Wait(done.Get(), 1, event.value);
    require(SUCCEEDED(destination.resource->Map(0, &full, &data)), "map completed destination");
    bool matches = true;
    for (size_t i = 0; i < bytes; ++i) matches &= static_cast<uint8_t*>(data)[i] == uint8_t(i * 37 + 19);
    destination.resource->Unmap(0, &no_read);
    require(matches, "copy data damaged after cache eviction");
    p.submission_completed_ = done->GetCompletedValue(); p.Drain(); p.Drain();
    require(destination.releases == 1 && p.resources_for_deletion_.empty(), "destination not released exactly once");
    require(SUCCEEDED(device->GetDeviceRemovedReason()), "device removed");
    std::cout << "Pending D3D12 copy survived cache eviction; 4096 bytes match; reference released after fence.\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n'; return 1;
  }
}
