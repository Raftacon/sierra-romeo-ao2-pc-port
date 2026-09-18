// Exact production queue-wait block, with bounded queue/event test doubles.
// No actual infinite wait or GPU work is submitted by this regression test.
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

using HRESULT = int32_t;
using UINT64 = uint64_t;
constexpr HRESULT S_OK = 0, S_FALSE = 1, E_FAIL = -2147467259;
constexpr uint32_t INFINITE = 0xFFFFFFFF;
constexpr uint32_t WAIT_OBJECT_0 = 0;
#define SUCCEEDED(hr) (static_cast<HRESULT>(hr) >= 0)
#define PROFILE_CMD_BUFFER_STALL() ((void)0)
#define REXGPU_ERROR(...) (++errors)

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
struct Event {
  bool registered = false;
  unsigned waits = 0, unregistered_waits = 0;
};
uint32_t WaitForSingleObject(Event* event, uint32_t timeout) {
  require(timeout == INFINITE, "unexpected production wait mode");
  ++event->waits;
  if (!event->registered) ++event->unregistered_waits;
  return WAIT_OBJECT_0;
}
struct Fence {
  HRESULT registration_result = S_OK;
  std::vector<uint64_t> registrations;
  uint64_t GetCompletedValue() { return registrations.empty() ? 0 : registrations.back(); }
  HRESULT SetEventOnCompletion(uint64_t value, Event* event) {
    registrations.push_back(value);
    if (SUCCEEDED(registration_result)) event->registered = true;
    return registration_result;
  }
};
struct ID3D12CommandQueue {
  HRESULT signal_result = S_OK;
  std::vector<uint64_t> signals;
  HRESULT Signal(Fence*, uint64_t value) {
    signals.push_back(value);
    return signal_result;
  }
};
struct Processor {
  bool queue_operations_done_since_submission_signal_ = true;
  uint64_t queue_operations_since_submission_fence_last_ = 0;
  Fence fence;
  Event event;
  ID3D12CommandQueue queue;
  Fence* queue_operations_since_submission_fence_ = &fence;
  Event* fence_completion_event_ = &event;
  unsigned errors = 0;
  Processor& GetD3D12Provider() { return *this; }
  ID3D12CommandQueue* GetDirectQueue() { return &queue; }
  bool Check() {
#ifdef AOT_QUEUE_ORIGINAL
#include "queue_wait_original.inc"
#else
#include "queue_wait_fixed.inc"
#endif
    return true;
  }
};

int main() {
  try {
#ifdef AOT_QUEUE_ORIGINAL
    Processor success;
    success.Check();
    require(success.queue.signals.size() == 1 && success.fence.registrations.empty(),
            "original did not skip event registration after S_OK");
    require(success.event.unregistered_waits == 1,
            "original did not enter the unregistered wait");
    Processor failure;
    failure.queue.signal_result = E_FAIL;
    failure.Check();
    require(failure.event.waits == 1 && failure.errors == 0,
            "original did not incorrectly wait after failed signal");
    std::cout << "Original defect reproduced: S_OK skips registration; failure enters wait.\n";
#else
    for (HRESULT signal : {S_OK, S_FALSE, E_FAIL}) {
      for (HRESULT registration : {S_OK, S_FALSE, E_FAIL}) {
        Processor p;
        p.queue.signal_result = signal;
        p.fence.registration_result = registration;
        p.Check();
        const bool signaled = SUCCEEDED(signal);
        const bool ready = signaled && SUCCEEDED(registration);
        require(p.queue.signals == std::vector<uint64_t>{1}, "wrong signal value/count");
        require(p.fence.registrations.size() == unsigned(signaled), "wrong registration count");
        if (signaled) require(p.fence.registrations[0] == 1, "event targets a different fence value");
        require(p.event.waits == unsigned(ready), "wait requires both successful HRESULTs");
        require(p.event.unregistered_waits == 0, "wait occurred without event registration");
        require(p.errors == unsigned(!ready), "failed setup was not reported");
        require(p.queue_operations_done_since_submission_signal_ == !ready,
                "pending operations cleared without successful setup and wait");
      }
    }
    Processor idle;
    idle.queue_operations_done_since_submission_signal_ = false;
    idle.Check();
    require(idle.queue.signals.empty() && idle.fence.registrations.empty() && idle.event.waits == 0,
            "idle queue performed synchronization");
    Processor retry;
    retry.queue.signal_result = E_FAIL;
    retry.Check();
    retry.queue.signal_result = S_OK;
    retry.Check();
    require(retry.queue.signals == std::vector<uint64_t>({1, 2}) &&
            retry.fence.registrations == std::vector<uint64_t>{2}, "retry reused a stale fence value");
    require(retry.event.waits == 1 && !retry.queue_operations_done_since_submission_signal_,
            "retry did not complete the pending wait");
    std::cout << "Queue-wait HRESULT combinations, idle queue and retry passed.\n";
#endif
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
