// Compile the full production completion/reclamation method. GPU and OS calls
// are deterministic doubles; resources record release rather than freeing memory.
#include <cstdint>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <utility>
#include <vector>

using HRESULT = int32_t;
using UINT64 = uint64_t;
constexpr HRESULT S_OK = 0, E_FAIL = -2147467259;
constexpr uint32_t INFINITE = 0xFFFFFFFF, WAIT_OBJECT_0 = 0, WAIT_FAILED = 0xFFFFFFFF;
#define SUCCEEDED(x) (static_cast<HRESULT>(x) >= 0)
#define FAILED(x) (static_cast<HRESULT>(x) < 0)
#define PROFILE_CMD_BUFFER_STALL() ((void)0)
#define REXGPU_ERROR(...) (++errors)
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
struct Event { uint32_t result = WAIT_OBJECT_0; unsigned waits = 0; };
uint32_t WaitForSingleObject(Event* event, uint32_t timeout) {
  require(timeout == INFINITE, "unexpected wait mode");
  ++event->waits;
  return event->result;
}
struct Fence {
  std::deque<uint64_t> values{2};
  HRESULT registration_result = S_OK;
  unsigned reads = 0, registrations = 0;
  uint64_t GetCompletedValue() {
    ++reads;
    const auto value = values.front();
    if (values.size() > 1) values.pop_front();
    return value;
  }
  HRESULT SetEventOnCompletion(uint64_t, Event*) { ++registrations; return registration_result; }
};
struct ID3D12CommandQueue {
  HRESULT result = S_OK;
  bool complete = true;
  unsigned signals = 0;
  HRESULT Signal(Fence* fence, uint64_t value) {
    ++signals;
    if (SUCCEEDED(result) && complete) fence->values = {value};
    return result;
  }
};
struct Allocator { uint64_t last_usage_submission; Allocator* next; };
struct Resource { unsigned releases = 0; void Release() { ++releases; } };
struct Cache {
  unsigned notifications = 0;
  void CompletedSubmissionUpdated() { ++notifications; }
  void CompletedSubmissionUpdated(uint64_t) { ++notifications; }
};
struct D3D12CommandProcessor {
  bool device_removed_ = false, submission_open_ = false, end_result = true;
  bool queue_operations_done_since_submission_signal_ = false;
  uint64_t submission_current_ = 3, submission_completed_ = 1;
  uint64_t queue_operations_since_submission_fence_last_ = 0;
  unsigned errors = 0, ends = 0;
  Fence submission_fence, queue_fence;
  Fence* submission_fence_ = &submission_fence;
  Fence* queue_operations_since_submission_fence_ = &queue_fence;
  Event event;
  Event* fence_completion_event_ = &event;
  ID3D12CommandQueue queue;
  Allocator future{9, nullptr}, ready{2, &future};
  Allocator* command_allocator_submitted_first_ = &ready;
  Allocator* command_allocator_submitted_last_ = &future;
  Allocator* command_allocator_writable_first_ = nullptr;
  Allocator* command_allocator_writable_last_ = nullptr;
  Resource ready_resource, future_resource;
  std::deque<std::pair<uint64_t, Resource*>> resources_for_deletion_{{2, &ready_resource}, {9, &future_resource}};
  std::deque<std::pair<int, uint64_t>> view_bindless_one_use_descriptors_{{10, 2}, {20, 9}};
  std::vector<int> released_descriptors;
  Cache shared, render, primitive, texture;
  Cache* shared_memory_ = &shared;
  Cache* render_target_cache_ = &render;
  Cache* primitive_processor_ = &primitive;
  Cache* texture_cache_ = &texture;
  void ReleaseViewBindlessDescriptorImmediately(int value) { released_descriptors.push_back(value); }
  D3D12CommandProcessor& GetD3D12Provider() { return *this; }
  ID3D12CommandQueue* GetDirectQueue() { return &queue; }
  bool EndSubmission(bool) {
    ++ends;
    if (end_result) { submission_open_ = false; ++submission_current_; }
    return end_result;
  }
#ifdef AOT_SUBMISSION_PREVIOUS
  void CheckSubmissionFence(uint64_t);
#include "submission_await_previous.inc"
#else
  bool CheckSubmissionFence(uint64_t);
#include "submission_await_fixed.inc"
#endif
  void RequirePreserved() {
    require(submission_completed_ == 1, "failure advanced completed submission");
    require(command_allocator_submitted_first_ == &ready && !command_allocator_writable_first_,
            "failure recycled an allocator");
    require(ready_resource.releases == 0 && future_resource.releases == 0 && resources_for_deletion_.size() == 2,
            "failure released a transient resource");
    require(released_descriptors.empty() && view_bindless_one_use_descriptors_.size() == 2,
            "failure recycled a descriptor");
    require(shared.notifications + render.notifications + primitive.notifications + texture.notifications == 0,
            "failure notified caches of completion");
  }
};
#ifdef AOT_SUBMISSION_PREVIOUS
#include "submission_fence_previous.inc"
#else
#include "submission_fence_fixed.inc"
#endif

int main() {
  try {
#ifdef AOT_SUBMISSION_PREVIOUS
    D3D12CommandProcessor failed_setup;
    failed_setup.queue_operations_done_since_submission_signal_ = true;
    failed_setup.queue_fence.registration_result = E_FAIL;
    require(failed_setup.AwaitAllQueueOperationsCompletion(), "previous false-success was not reproduced");
    require(failed_setup.queue_operations_done_since_submission_signal_ && failed_setup.errors == 1,
            "previous setup-failure precondition missing");
    D3D12CommandProcessor lost;
    lost.submission_fence.values = {UINT64_MAX};
    lost.CheckSubmissionFence(0);
    require(lost.submission_completed_ == UINT64_MAX && lost.future_resource.releases == 1 &&
            lost.released_descriptors.size() == 2, "previous device-loss reclamation was not reproduced");
    D3D12CommandProcessor failed_wait;
    failed_wait.queue_operations_done_since_submission_signal_ = true;
    failed_wait.event.result = WAIT_FAILED;
    require(failed_wait.AwaitAllQueueOperationsCompletion() && !failed_wait.queue_operations_done_since_submission_signal_,
            "previous failed-wait success was not reproduced");
    std::cout << "Previous false-success and device-removal reclamation defects reproduced.\n";
#else
    for (int scenario = 0; scenario < 11; ++scenario) {
      D3D12CommandProcessor p;
      uint64_t target = 2;
      switch (scenario) {
        case 0: p.submission_open_ = true; p.end_result = false; target = 3; break;
        case 1: p.queue_operations_done_since_submission_signal_ = true; p.queue.result = E_FAIL; target = 3; break;
        case 2: p.queue_operations_done_since_submission_signal_ = true; p.queue_fence.registration_result = E_FAIL; target = 3; break;
        case 3: p.queue_operations_done_since_submission_signal_ = true; p.event.result = WAIT_FAILED; target = 3; break;
        case 4: p.queue_operations_done_since_submission_signal_ = true; p.queue.complete = false; p.queue_fence.values = {0}; target = 3; break;
        case 5: p.submission_fence.values = {UINT64_MAX}; target = 0; break;
        case 6: p.submission_fence.values = {1}; p.submission_fence.registration_result = E_FAIL; break;
        case 7: p.submission_fence.values = {1, 2}; p.event.result = WAIT_FAILED; break;
        case 8: p.submission_fence.values = {1, UINT64_MAX}; break;
        case 9: p.submission_fence.values = {1}; break;
        case 10: p.queue_operations_done_since_submission_signal_ = true; p.queue.complete = false; p.queue_fence.values = {UINT64_MAX}; target = 3; break;
      }
      require(!p.CheckSubmissionFence(target), "failure reported completion");
      p.RequirePreserved();
      if ((scenario >= 1 && scenario <= 4) || scenario == 10) require(p.queue_operations_done_since_submission_signal_, "failed queue wait cleared pending flag");
    }
    D3D12CommandProcessor retry;
    retry.queue_operations_done_since_submission_signal_ = true;
    retry.queue_fence.registration_result = E_FAIL;
    require(!retry.AwaitAllQueueOperationsCompletion(), "wrapper swallowed setup failure");
    retry.RequirePreserved();
    retry.queue_fence.registration_result = S_OK;
    require(retry.AwaitAllQueueOperationsCompletion() && retry.queue.signals == 2 &&
            retry.queue_operations_since_submission_fence_last_ == 2 &&
            retry.submission_completed_ == 2, "retry did not complete pending work");
    D3D12CommandProcessor open;
    open.submission_open_ = true;
    open.submission_fence.values = {3};
    require(open.AwaitAllQueueOperationsCompletion() && open.ends == 1 &&
            !open.submission_open_ && open.submission_current_ == 4 &&
            open.submission_completed_ == 3, "open submission was not closed and awaited");
    D3D12CommandProcessor blocked;
    blocked.device_removed_ = true;
    require(!blocked.AwaitAllQueueOperationsCompletion(), "removed device accepted work");
    blocked.RequirePreserved();
    require(blocked.submission_fence.reads == 0, "removed device queried its fence");
    D3D12CommandProcessor healthy;
    healthy.queue_operations_done_since_submission_signal_ = true;
    require(healthy.AwaitAllQueueOperationsCompletion(), "healthy queue rejected completion");
    require(healthy.submission_completed_ == 2 && !healthy.queue_operations_done_since_submission_signal_, "healthy completion state wrong");
    require(healthy.ready_resource.releases == 1 && healthy.future_resource.releases == 0 &&
            healthy.released_descriptors == std::vector<int>{10}, "healthy reclamation crossed the completion boundary");
    require(healthy.command_allocator_writable_first_ == &healthy.ready &&
            healthy.command_allocator_submitted_first_ == &healthy.future, "allocator boundary incorrect");
    require(healthy.shared.notifications == 1 && healthy.texture.notifications == 1, "healthy caches not notified");
    require(healthy.CheckSubmissionFence(0) && healthy.ready_resource.releases == 1 && healthy.shared.notifications == 1,
            "unchanged completion reclaimed work twice");
    D3D12CommandProcessor wake;
    wake.submission_fence.values = {1, 2};
    require(wake.CheckSubmissionFence(2) && wake.event.waits == 1 && wake.submission_completed_ == 2,
            "successful submission wait did not advance completion");
    std::cout << "Failure propagation, resource retention, healthy completion and wait recovery passed.\n";
#endif
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
