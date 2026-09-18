#pragma once

#include <chrono>
#include <cstdint>
#include <fstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
// Opt-in attribution at the existing game-frame hook. CPU time is coarse OS
// accounting, not a high-resolution measurement or an identified wait reason.
class FrameDiagnostics {
  using Clock = std::chrono::steady_clock;
  struct History {
    Clock::time_point entry{}, exit{};
    double cpu_exit = -1, write_ms = 0;
  };
  static History& Previous() { static thread_local History previous; return previous; }
  static double Ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(a - b).count();
  }
  static double CpuMs() {
#ifdef _WIN32
    FILETIME created{}, exited{}, kernel{}, user{};
    if (GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user)) {
      const auto ticks = [](FILETIME t) { return (uint64_t(t.dwHighDateTime) << 32) | t.dwLowDateTime; };
      return (ticks(kernel) + ticks(user)) / 10000.0;
    }
#endif
    return -1;
  }
 public:
  explicit FrameDiagnostics(std::ofstream* output) : output_(output) {
    if (!output_) return;
    entry_ = Clock::now();
    cpu_ = CpuMs();
  }
  void CommandsDone() { if (output_) commands_ = Clock::now(); }
  void PacingDone() { if (output_) pacing_ = Clock::now(); }
  void Frame(uint64_t frame) { frame_ = frame; }
  ~FrameDiagnostics() {
    if (!output_) return;
    const auto finish = Clock::now();
    auto& previous = Previous();
    const bool known = previous.entry != Clock::time_point{};
    *output_ << frame_ << ',' << (known ? Ms(entry_, previous.entry) : 0) << ','
        << (known ? Ms(entry_, previous.exit) : 0) << ','
        << (known && cpu_ >= 0 && previous.cpu_exit >= 0 ? cpu_ - previous.cpu_exit : -1) << ','
        << Ms(commands_, entry_) << ',' << Ms(pacing_, commands_) << ','
        << Ms(finish, pacing_) << ',' << previous.write_ms << '\n';
    if (frame_ % 60 == 0) output_->flush();
    previous.write_ms = Ms(Clock::now(), finish);
    previous.cpu_exit = CpuMs();
    previous.exit = Clock::now();
    previous.entry = entry_;
  }
 private:
  std::ofstream* output_;
  Clock::time_point entry_{}, commands_{}, pacing_{};
  double cpu_ = -1;
  uint64_t frame_ = 0;
};
}  // namespace aot
