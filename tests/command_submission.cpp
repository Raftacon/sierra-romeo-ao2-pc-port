// Compile the actual production submission block against deterministic API
// doubles. FatalError throws only in this fixture, so failure boundaries can be
// inspected without terminating the test process or submitting invalid GPU work.
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "../src/swap_timing.h"

using HRESULT = int32_t;
constexpr HRESULT S_OK = 0, S_FALSE = 1, E_FAIL = -2147467259;
constexpr HRESULT DEVICE_HUNG = -2005270522, DEVICE_REMOVED = -2005270523;
#define SUCCEEDED(x) (static_cast<HRESULT>(x) >= 0)
#define FAILED(x) (static_cast<HRESULT>(x) < 0)
#define REXGPU_ERROR(...) RecordError(__VA_ARGS__)

void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
struct FatalSubmission {};
namespace rex {
unsigned flushes = 0;
void FlushLogging() { ++flushes; }
[[noreturn]] void FatalError(const char* message) {
  require(std::string(message).find("command submission failed") != std::string::npos,
          "unexpected fatal diagnostic");
  throw FatalSubmission{};
}
}
struct ID3D12CommandAllocator {
  HRESULT result = S_OK;
  unsigned resets = 0;
  HRESULT Reset() { ++resets; return result; }
};
struct ID3D12CommandList {};
struct CommandList : ID3D12CommandList {
  HRESULT reset_result = S_OK, close_result = S_OK;
  unsigned resets = 0, closes = 0;
  ID3D12CommandAllocator* allocator = nullptr;
  HRESULT Reset(ID3D12CommandAllocator* value, void* state) {
    require(state == nullptr, "unexpected initial pipeline");
    allocator = value;
    ++resets;
    return reset_result;
  }
  HRESULT Close() { ++closes; return close_result; }
};
struct Deferred {
  unsigned executions = 0;
  void Execute(CommandList*, void*) { ++executions; }
};
struct Fence {};
struct ID3D12CommandQueue {
  HRESULT signal_result = S_OK;
  unsigned executions = 0, signals = 0;
  ID3D12CommandList* executed = nullptr;
  Fence* signaled = nullptr;
  uint64_t signal_value = 0;
  void ExecuteCommandLists(unsigned count, ID3D12CommandList* const* lists) {
    require(count == 1, "submission boundary changed");
    ++executions;
    executed = lists[0];
  }
  HRESULT Signal(Fence* fence, uint64_t value) {
    ++signals;
    signaled = fence;
    signal_value = value;
    return signal_result;
  }
};
struct Device {
  HRESULT reason = S_OK;
  unsigned queries = 0;
  HRESULT GetDeviceRemovedReason() { ++queries; return reason; }
};
struct Provider {
  Device* device;
  Device* GetDevice() const { return device; }
};
struct Allocator {
  ID3D12CommandAllocator* command_allocator;
  uint64_t last_usage_submission;
  Allocator* next;
};
struct Processor {
  ID3D12CommandAllocator gpu_allocator;
  Allocator spare{nullptr, 0, nullptr};
  Allocator writable{&gpu_allocator, 0, &spare};
  Allocator previous{nullptr, 5, nullptr};
  Allocator* command_allocator_writable_first_ = &writable;
  Allocator* command_allocator_writable_last_ = &spare;
  Allocator* command_allocator_submitted_first_ = &previous;
  Allocator* command_allocator_submitted_last_ = &previous;
  CommandList list;
  CommandList* command_list_ = &list;
  void* command_list_1_ = nullptr;
  Deferred deferred_command_list_;
  Fence fence;
  Fence* submission_fence_ = &fence;
  uint64_t submission_current_ = 7;
  bool submission_open_ = true;
  bool queue_operations_done_since_submission_signal_ = true;
  ID3D12CommandQueue queue;
  Device device;
  Provider source_provider{&device};
  unsigned errors = 0, diagnostics = 0;
  std::string failed_operation;
  HRESULT failed_result = S_OK, diagnostic_reason = S_OK;
  uint64_t failed_submission = 0;
  void RecordError(const char*, const char* operation, uint64_t submission, uint32_t result) {
    ++errors;
    failed_operation = operation;
    failed_submission = submission;
    failed_result = static_cast<HRESULT>(result);
  }
  void LogDeviceRemovalDiagnostics(Device* value, HRESULT reason) {
    require(value == &device, "wrong diagnostic device");
    ++diagnostics;
    diagnostic_reason = reason;
  }
  void Submit() {
    // The extracted block starts after the production method's local trace
    // selection. Exercise its ordinary diagnostic-disabled configuration.
    const bool aot_trace_submission = false;
    const Provider& provider = source_provider;
    ID3D12CommandQueue* direct_queue = &queue;
#ifdef AOT_COMMAND_PREVIOUS
#include "command_submission_previous.inc"
#else
#include "command_submission_fixed.inc"
#endif
  }
  bool SubmitCaught() {
    const unsigned flushes_before = rex::flushes;
    try {
      Submit();
      require(rex::flushes == flushes_before, "healthy submission flushed logs");
      return true;
    } catch (const FatalSubmission&) {
      require(rex::flushes == flushes_before + 1, "fatal diagnostic was not flushed");
      return false;
    }
  }
  void SetResult(unsigned operation, HRESULT result) {
    switch (operation) {
      case 0: gpu_allocator.result = result; break;
      case 1: list.reset_result = result; break;
      case 2: list.close_result = result; break;
      case 3: queue.signal_result = result; break;
      default: throw std::runtime_error("invalid test operation");
    }
  }
  void RequireSuccessful() {
    require(gpu_allocator.resets == 1 && list.resets == 1 && list.closes == 1 &&
            deferred_command_list_.executions == 1 && queue.executions == 1 && queue.signals == 1,
            "successful path skipped or repeated work");
    require(list.allocator == &gpu_allocator && queue.executed == &list &&
            queue.signaled == &fence && queue.signal_value == 7, "incorrect submission arguments");
    require(submission_current_ == 8 && !submission_open_ &&
            !queue_operations_done_since_submission_signal_, "successful bookkeeping incorrect");
    require(command_allocator_submitted_last_ == &writable && writable.last_usage_submission == 7 &&
            !writable.next, "submitted allocator bookkeeping incorrect");
    require(errors == 0 && device.queries == 0 && diagnostics == 0,
            "healthy path invoked failure diagnostics");
  }
};

int main() {
  try {
#ifdef AOT_COMMAND_PREVIOUS
    for (unsigned operation = 0; operation < 4; ++operation) {
      Processor p;
      p.SetResult(operation, E_FAIL);
      require(p.SubmitCaught(), "previous path unexpectedly stopped on failure");
      p.RequireSuccessful();
    }
    std::cout << "Previous path ignored all four API failures and advanced submission state.\n";
#else
    const std::vector<std::string> operation_names{
        "command allocator Reset", "command list Reset", "command list Close", "submission fence Signal"};
    for (HRESULT reason : {S_OK, DEVICE_HUNG, DEVICE_REMOVED}) {
      for (unsigned operation = 0; operation < 4; ++operation) {
        Processor p;
        p.SetResult(operation, E_FAIL);
        p.device.reason = reason;
        require(!p.SubmitCaught(), "failed API did not stop submission");
        require(p.errors == 1 && p.failed_operation == operation_names[operation] &&
                p.failed_result == E_FAIL && p.failed_submission == 7, "failure diagnostic lost context");
        require(p.device.queries == 1 && p.diagnostics == unsigned(FAILED(reason)),
                "device diagnostics were omitted or falsely reported");
        if (FAILED(reason)) require(p.diagnostic_reason == reason, "device removal reason changed");
        require(p.gpu_allocator.resets == 1 && p.list.resets == unsigned(operation >= 1) &&
                p.deferred_command_list_.executions == unsigned(operation >= 2) &&
                p.list.closes == unsigned(operation >= 2) &&
                p.queue.executions == unsigned(operation >= 3) &&
                p.queue.signals == unsigned(operation >= 3), "work continued beyond the failed API");
        require(p.submission_current_ == 7 && p.submission_open_ &&
                p.queue_operations_done_since_submission_signal_, "failure published successful completion");
        if (operation < 3) {
          require(p.command_allocator_writable_first_ == &p.writable &&
                  p.command_allocator_submitted_last_ == &p.previous && !p.previous.next &&
                  p.writable.last_usage_submission == 0, "unexecuted allocator marked submitted");
        } else {
          require(p.command_allocator_submitted_last_ == &p.writable &&
                  p.previous.next == &p.writable && p.writable.last_usage_submission == 7 &&
                  p.command_allocator_writable_first_ == &p.spare,
                  "executed allocator lost tracking after Signal failure");
        }
      }
    }
    // Every combination of zero / positive HRESULT success must preserve the
    // exact submission boundary and avoid querying device status on the hot path.
    for (unsigned mask = 0; mask < 16; ++mask) {
      Processor p;
      for (unsigned operation = 0; operation < 4; ++operation)
        p.SetResult(operation, (mask & (1 << operation)) ? S_FALSE : S_OK);
      require(p.SubmitCaught(), "positive HRESULT success rejected");
      p.RequireSuccessful();
      require(p.command_allocator_submitted_first_ == &p.previous && p.previous.next == &p.writable &&
              p.command_allocator_writable_first_ == &p.spare && p.command_allocator_writable_last_ == &p.spare,
              "existing allocator chains damaged");
    }
    Processor only;
    only.writable.next = nullptr;
    only.command_allocator_writable_last_ = &only.writable;
    only.command_allocator_submitted_first_ = only.command_allocator_submitted_last_ = nullptr;
    require(only.SubmitCaught(), "single allocator submission failed");
    only.RequireSuccessful();
    require(!only.command_allocator_writable_first_ && !only.command_allocator_writable_last_ &&
            only.command_allocator_submitted_first_ == &only.writable, "empty allocator chain handling failed");
    std::cout << "Four failure boundaries, device diagnostics, 16 success combinations and allocator chains passed.\n";
#endif
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
