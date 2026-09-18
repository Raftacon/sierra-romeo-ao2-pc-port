#pragma once

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>

namespace aot {
// Used only in the optional GPU build. Capture packet and actual sleep timing
// without changing operands, the match condition, or the original wait call.
class GpuWaitDiagnostics {
  using Clock = std::chrono::steady_clock;
  struct Output {
    std::ofstream file;
    uint64_t packets = 0, rows = 0;
    Output() {
      if (const auto* path = std::getenv("AOT_GPU_WAIT_LOG")) {
        file.open(path);
        file.precision(16);
        file << "packet,start_clock_ms,end_clock_ms,packet_ms,wait_info,poll_address,reference,mask,wait_operand,polls,last_value,matched,sleep_calls,requested_sleep_ms,total_sleep_ms,max_sleep_ms\n";
      }
    }
  };
  static double Ms(Clock::time_point value) {
    return std::chrono::duration<double, std::milli>(value.time_since_epoch()).count();
  }
 public:
  GpuWaitDiagnostics(uint32_t info, uint32_t address, uint32_t reference,
                     uint32_t mask, uint32_t wait)
      : info_(info), address_(address), reference_(reference), mask_(mask), wait_(wait) {
    static thread_local Output output;
    if (!output.file.is_open()) return;
    output_ = &output;
    start_ = Clock::now();
  }
  void Poll(uint32_t value, bool matched) {
    if (!output_) return;
    ++polls_; value_ = value; matched_ = matched;
  }
  Clock::time_point BeforeSleep() const { return output_ ? Clock::now() : Clock::time_point{}; }
  void AfterSleep(Clock::time_point start, uint32_t requested_ms) {
    if (!output_) return;
    const double elapsed = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    ++sleeps_; requested_ms_ += requested_ms; sleep_ms_ += elapsed;
    if (elapsed > max_sleep_ms_) max_sleep_ms_ = elapsed;
  }
  ~GpuWaitDiagnostics() {
    if (!output_) return;
    const auto end = Clock::now();
    const double elapsed = std::chrono::duration<double, std::milli>(end - start_).count();
    const auto packet = ++output_->packets;
    // Immediate coherency checks vastly outnumber waits. Keep actual polling
    // and anomalously slow first checks without logging every register read.
    if (polls_ == 1 && matched_ && elapsed < 0.1) return;
    auto& file = output_->file;
    file << packet << ',' << Ms(start_) << ',' << Ms(end) << ',' << elapsed << ','
         << info_ << ',' << address_ << ',' << reference_ << ',' << mask_ << ',' << wait_ << ','
         << polls_ << ',' << value_ << ',' << matched_ << ',' << sleeps_ << ',' << requested_ms_ << ','
         << sleep_ms_ << ',' << max_sleep_ms_ << '\n';
    if (++output_->rows % 60 == 0) file.flush();
  }
 private:
  Output* output_ = nullptr;
  Clock::time_point start_{};
  uint32_t info_, address_, reference_, mask_, wait_, value_ = 0;
  uint64_t polls_ = 0, sleeps_ = 0, requested_ms_ = 0;
  bool matched_ = false;
  double sleep_ms_ = 0, max_sleep_ms_ = 0;
};
}  // namespace aot
