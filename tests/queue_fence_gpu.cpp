// Exercise the corrected production block on a real D3D12 queue and Win32 event.
// Bound the OS wait in this test so a regression fails instead of hanging CTest.
#include <windows.h>
#include <d3d12.h>
#include <wrl/client.h>
#include <iostream>
#include <stdexcept>

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
DWORD BoundedWait(HANDLE event, DWORD requested_timeout) {
  require(requested_timeout == INFINITE, "unexpected production wait mode");
  const DWORD result = ::WaitForSingleObject(event, 2000);
  require(result == WAIT_OBJECT_0, "real fence event did not complete within two seconds");
  return result;
}
#define PROFILE_CMD_BUFFER_STALL() ((void)0)
#define REXGPU_ERROR(...) throw std::runtime_error("real queue/event setup failed")
struct Processor {
  Microsoft::WRL::ComPtr<ID3D12Device> device;
  Microsoft::WRL::ComPtr<ID3D12CommandQueue> queue;
  Microsoft::WRL::ComPtr<ID3D12Fence> fence;
  HANDLE fence_completion_event_ = nullptr;
  ID3D12Fence* queue_operations_since_submission_fence_ = nullptr;
  UINT64 queue_operations_since_submission_fence_last_ = 0;
  bool queue_operations_done_since_submission_signal_ = true;
  ~Processor() { if (fence_completion_event_) CloseHandle(fence_completion_event_); }
  Processor& GetD3D12Provider() { return *this; }
  ID3D12CommandQueue* GetDirectQueue() { return queue.Get(); }
  bool Check() {
#define WaitForSingleObject BoundedWait
#include "queue_wait_fixed.inc"
#undef WaitForSingleObject
    return true;
  }
};
int main() {
  try {
    Processor p;
    require(SUCCEEDED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0,
                                      IID_PPV_ARGS(&p.device))), "D3D12 device creation failed");
    D3D12_COMMAND_QUEUE_DESC desc{};
    desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    require(SUCCEEDED(p.device->CreateCommandQueue(&desc, IID_PPV_ARGS(&p.queue))),
            "D3D12 queue creation failed");
    require(SUCCEEDED(p.device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&p.fence))),
            "D3D12 fence creation failed");
    p.queue_operations_since_submission_fence_ = p.fence.Get();
    p.fence_completion_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    require(p.fence_completion_event_ != nullptr, "event creation failed");
    for (UINT64 value = 1; value <= 2; ++value) {
      p.queue_operations_done_since_submission_signal_ = true;
      require(p.Check(), "production queue wait rejected completion");
      require(!p.queue_operations_done_since_submission_signal_, "queue remains pending");
      require(p.queue_operations_since_submission_fence_last_ == value &&
              p.fence->GetCompletedValue() == value, "wrong completed fence value");
      require(WaitForSingleObject(p.fence_completion_event_, 0) == WAIT_TIMEOUT,
              "auto-reset event remained signaled after the production wait");
    }
    require(SUCCEEDED(p.device->GetDeviceRemovedReason()), "device removed during queue test");
    std::cout << "Two real D3D12 queue signals registered, completed and consumed their events.\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
