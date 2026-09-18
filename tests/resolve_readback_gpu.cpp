#include "src/resolve_readback_schedule.h"
#include <windows.h>
#include <d3d12.h>
#include <wrl/client.h>
#include <array>
#include <cstring>
#include <iostream>
#include <stdexcept>
using Microsoft::WRL::ComPtr;
void require(bool value, const char* why) { if (!value) throw std::runtime_error(why); }
struct Event {
  HANDLE value = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  ~Event() { if (value) CloseHandle(value); }
};
void Wait(ID3D12Fence* fence, uint64_t value, HANDLE event) {
  require(SUCCEEDED(fence->SetEventOnCompletion(value, event)), "register completion");
  require(WaitForSingleObject(event, 5000) == WAIT_OBJECT_0, "GPU completion timeout");
  require(fence->GetCompletedValue() != UINT64_MAX && fence->GetCompletedValue() >= value, "invalid completion");
}
struct Cleanup {
  ID3D12CommandQueue* queue; ID3D12Fence *gate, *done; HANDLE event;
  ~Cleanup() {
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
    ComPtr<ID3D12Resource> upload, readback[2];
    require(SUCCEEDED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device))), "device");
    D3D12_COMMAND_QUEUE_DESC q{}; q.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    require(SUCCEEDED(device->CreateCommandQueue(&q, IID_PPV_ARGS(&queue))), "queue");
    require(SUCCEEDED(device->CreateCommandAllocator(q.Type, IID_PPV_ARGS(&allocator))), "allocator");
    require(SUCCEEDED(device->CreateCommandList(0, q.Type, allocator.Get(), nullptr, IID_PPV_ARGS(&commands))), "list");
    require(SUCCEEDED(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&gate))), "gate");
    require(SUCCEEDED(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&done))), "fence");
    constexpr size_t bytes = 4096;
    D3D12_RESOURCE_DESC d{};
    d.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; d.Width = bytes * 2;
    d.Height = 1; d.DepthOrArraySize = 1; d.MipLevels = 1;
    d.SampleDesc.Count = 1; d.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    D3D12_HEAP_PROPERTIES h{}; h.CreationNodeMask = h.VisibleNodeMask = 1;
    h.Type = D3D12_HEAP_TYPE_UPLOAD;
    require(SUCCEEDED(device->CreateCommittedResource(&h, D3D12_HEAP_FLAG_NONE, &d,
        D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_PPV_ARGS(&upload))), "upload");
    void* source = nullptr; D3D12_RANGE empty{0, 0}, all{0, bytes * 2};
    require(SUCCEEDED(upload->Map(0, &empty, &source)), "map upload");
    for (size_t i = 0; i < bytes * 2; ++i) static_cast<uint8_t*>(source)[i] = uint8_t(i * 37 + (i / bytes) * 91);
    upload->Unmap(0, &all);
    h.Type = D3D12_HEAP_TYPE_READBACK; d.Width = bytes;
    void* mapped[2]{}; D3D12_RANGE readable{0, bytes};
    for (unsigned i = 0; i < 2; ++i) {
      require(SUCCEEDED(device->CreateCommittedResource(&h, D3D12_HEAP_FLAG_NONE, &d,
          D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback[i]))), "readback");
      require(SUCCEEDED(readback[i]->Map(0, &readable, &mapped[i])), "map readback");
    }
    aot::ResolveReadbackState state;
    aot::ResolveReadbackSlot slots[2]{{1, true}, {1, true}};
    // Put the newer snapshot in slot 0, with both writes sharing one fence.
    commands->CopyBufferRegion(readback[1].Get(), 0, upload.Get(), 0, bytes); state.Written(1, 10);
    commands->CopyBufferRegion(readback[0].Get(), 0, upload.Get(), bytes, bytes); state.Written(0, 10);
    require(SUCCEEDED(commands->Close()), "close list");
    Event event; require(event.value != nullptr, "event");
    Cleanup cleanup{queue.Get(), gate.Get(), done.Get(), event.value};
    require(SUCCEEDED(queue->Wait(gate.Get(), 1)), "hold queue");
    ID3D12CommandList* list[]{commands.Get()}; queue->ExecuteCommandLists(1, list);
    require(SUCCEEDED(queue->Signal(done.Get(), 1)), "signal submission");
    require(done->GetCompletedValue() == 0, "queue gate ineffective");
    for (unsigned i = 0; i < 1000; ++i) {
      const auto plan = aot::PlanResolveReadback(state, slots, done->GetCompletedValue(), i & 1);
      require(plan.read == -1 && plan.write == -1, "pending GPU slot selected");
    }
    require(SUCCEEDED(gate->Signal(1)), "release queue"); Wait(done.Get(), 1, event.value);
    auto plan = aot::PlanResolveReadback(state, slots, done->GetCompletedValue(), 1);
    require(plan.read == 0 && plan.write == 1, "incorrect completed snapshot selected");
    std::array<uint8_t, bytes> guest{};
    std::memcpy(guest.data(), mapped[plan.read], bytes);
    state.Copied(unsigned(plan.read), 11);
    for (size_t i = 0; i < bytes; ++i) require(guest[i] == uint8_t(i * 37 + 91), "snapshot contents differ");
    plan = aot::PlanResolveReadback(state, slots, done->GetCompletedValue(), 1);
    require(plan.read == -1, "completed data copied twice");
    for (auto& buffer : readback) buffer->Unmap(0, &empty);
    require(SUCCEEDED(device->GetDeviceRemovedReason()), "device removal");
    std::cout << "1000 pending checks avoided GPU-owned slots; newest same-fence snapshot copied exactly once; 4096 bytes match.\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
