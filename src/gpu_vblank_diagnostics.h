#pragma once

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
class GpuVblankDiagnostics {
  using Clock = std::chrono::steady_clock;
  struct Output {
    std::ofstream file;
    Clock::time_point previous{};
    uint64_t count = 0;
    uint32_t watch = UINT32_MAX;
    Output() {
      if (const auto* path = std::getenv("AOT_GPU_VBLANK_LOG")) {
        file.open(path); file.precision(16);
        if (const auto* value = std::getenv("AOT_GPU_VBLANK_WATCH")) {
          char* end = nullptr;
          const auto address = std::strtoull(value, &end, 0);
          if (end != value && !*end && address <= 0x1FFFFFFC && !(address & 3)) watch = uint32_t(address);
        }
        file << "vblank,start_clock_ms,end_clock_ms,interval_ms,callback_ms,watch_address,before_ok,before_raw,after_ok,after_raw\n";
      }
    }
  };
  static double Ms(Clock::time_point value) {
    return std::chrono::duration<double, std::milli>(value.time_since_epoch()).count();
  }
  bool Read(uint32_t& value) const {
#ifdef _WIN32
    if (output_->watch != UINT32_MAX) {
      SIZE_T size = 0;
      return ReadProcessMemory(GetCurrentProcess(), physical_base_ + output_->watch,
          &value, sizeof(value), &size) && size == sizeof(value);
    }
#endif
    return false;
  }
 public:
  explicit GpuVblankDiagnostics(uint8_t* physical_base) : physical_base_(physical_base) {
    static thread_local Output output;
    if (!output.file.is_open()) return;
    output_ = &output; start_ = Clock::now();
    if (output.previous != Clock::time_point{})
      interval_ms_ = std::chrono::duration<double, std::milli>(start_ - output.previous).count();
    output.previous = start_;
  }
  void BeforeCallback() {
    if (!output_) return;
    before_ok_ = Read(before_); callback_start_ = Clock::now();
  }
  void AfterCallback() {
    if (!output_) return;
    callback_end_ = Clock::now(); after_ok_ = Read(after_);
  }
  ~GpuVblankDiagnostics() {
    if (!output_) return;
    output_->file << ++output_->count << ',' << Ms(start_) << ',' << Ms(callback_end_) << ','
        << interval_ms_ << ',' << std::chrono::duration<double, std::milli>(callback_end_ - callback_start_).count() << ','
        << output_->watch << ',' << before_ok_ << ',' << before_ << ',' << after_ok_ << ',' << after_ << '\n';
    if (output_->count % 60 == 0) output_->file.flush();
  }
 private:
  Output* output_ = nullptr;
  uint8_t* physical_base_;
  Clock::time_point start_{}, callback_start_{}, callback_end_{};
  double interval_ms_ = 0;
  uint32_t before_ = 0, after_ = 0;
  bool before_ok_ = false, after_ok_ = false;
};
}  // namespace aot
