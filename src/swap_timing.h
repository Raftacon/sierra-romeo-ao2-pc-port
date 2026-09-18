#pragma once
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>

namespace aot {
// Opt-in wall-clock attribution, not GPU execution timing. The presenter
// invokes its refresh callback inline on the command processor thread.
class SwapTiming {
 public:
  enum MarkId { kPrepared, kCallback, kCommands, kSubmitted, kSpatial,
                kPublished, kFinished, kAllocator, kEndFrame, kPipelines,
                kBarriers, kReset, kDeferred, kClosed, kQueue, kSignal,
                kRetired, kMarkCount };
  explicit SwapTiming(uint64_t frame) : writer_(GetWriter()), frame_(frame) {
    stamps_.fill(-1);
    if (writer_.stream.is_open() && writer_.rows < 100000) {
      start_ = Now();
      previous_ = Active();
      Active() = this;
    }
  }
  ~SwapTiming() {
    if (start_ < 0) return;
    Active() = previous_;
    writer_.stream << frame_ << ',' << start_ << ',' << Now() << ',' << presented_;
    for (double stamp : stamps_) writer_.stream << ',' << stamp;
    writer_.stream << '\n';
    // Title termination hard-exits. Flush each complete row to retain the tail.
    writer_.stream.flush();
    ++writer_.rows;
  }
  void Mark(MarkId id) { if (start_ >= 0) stamps_[id] = Now(); }
  void Finish(bool presented) { presented_ = presented; Mark(kFinished); }
  static void SubmissionMark(MarkId id) { if (Active()) Active()->Mark(id); }

 private:
  struct Writer {
    std::ofstream stream;
    unsigned rows = 0;
    Writer() {
      if (const char* path = std::getenv("AOT_SWAP_LOG")) {
        stream.open(path, std::ios::out | std::ios::trunc);
        if (stream.is_open()) {
          stream << "gpu_frame,start_clock_ms,end_clock_ms,presented,prepared_ms,callback_ms,commands_ms,submitted_ms,spatial_ms,published_ms,finished_ms,allocator_ms,end_frame_ms,pipelines_ms,barriers_ms,reset_ms,deferred_ms,closed_ms,queue_ms,signal_ms,retired_ms\n";
          stream << std::setprecision(17);
          stream.flush();
        }
      }
    }
  };
  static Writer& GetWriter() { static thread_local Writer writer; return writer; }
  static SwapTiming*& Active() { static thread_local SwapTiming* active = nullptr; return active; }
  static double Now() {
    return std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
  }
  Writer& writer_;
  uint64_t frame_;
  double start_ = -1;
  bool presented_ = false;
  SwapTiming* previous_ = nullptr;
  std::array<double, kMarkCount> stamps_;
};
}  // namespace aot
