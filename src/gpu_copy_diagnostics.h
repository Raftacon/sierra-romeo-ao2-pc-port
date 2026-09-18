#pragma once

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>

namespace aot {
// Timings cover the legacy resolve memcpy only, aggregated by GPU frame.
class GpuCopyDiagnostics {
  using Clock = std::chrono::steady_clock;
  struct Output {
    std::ofstream file;
    uint64_t frame = 0, copies = 0, bytes = 0, rows = 0;
    double start_ms = 0, end_ms = 0, total_ms = 0, max_ms = 0;
    uint32_t worst_address = 0, worst_bytes = 0;
    Output() {
      if (const auto* path = std::getenv("AOT_GPU_COPY_LOG")) {
        file.open(path); file.precision(16);
        file << "gpu_frame,start_clock_ms,end_clock_ms,copies,bytes,total_copy_ms,max_copy_ms,worst_address,worst_bytes\n";
      }
    }
    void Write() {
      if (!copies) return;
      file << frame << ',' << start_ms << ',' << end_ms << ',' << copies << ',' << bytes << ','
           << total_ms << ',' << max_ms << ',' << worst_address << ',' << worst_bytes << '\n';
      if (++rows % 60 == 0) file.flush();
    }
    ~Output() { Write(); }
  };
  static double Ms(Clock::time_point value) {
    return std::chrono::duration<double, std::milli>(value.time_since_epoch()).count();
  }
 public:
  GpuCopyDiagnostics(uint64_t frame, uint32_t address, uint32_t bytes)
      : address_(address), bytes_(bytes) {
    static thread_local Output output;
    if (!output.file.is_open()) return;
    output_ = &output;
    if (output.frame != frame) {
      output.Write();
      output.frame = frame; output.copies = output.bytes = 0;
      output.total_ms = output.max_ms = 0;
      output.worst_address = output.worst_bytes = 0;
    }
    start_ = Clock::now();
    if (!output.copies) output.start_ms = Ms(start_);
  }
  ~GpuCopyDiagnostics() {
    if (!output_) return;
    const auto end = Clock::now();
    const double elapsed = std::chrono::duration<double, std::milli>(end - start_).count();
    ++output_->copies; output_->bytes += bytes_; output_->total_ms += elapsed;
    output_->end_ms = Ms(end);
    if (elapsed > output_->max_ms) {
      output_->max_ms = elapsed; output_->worst_address = address_; output_->worst_bytes = bytes_;
    }
  }
 private:
  Output* output_ = nullptr;
  Clock::time_point start_{};
  uint32_t address_, bytes_;
};
}  // namespace aot
