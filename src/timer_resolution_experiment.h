#pragma once
#include <cstdlib>
#include <string_view>
#include <rex/logging.h>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
// Attribution experiment only. The normal launcher removes this environment
// variable. No guest deadlines, GPU conditions or presentation flags change.
class TimerResolutionExperiment {
 public:
  TimerResolutionExperiment() = default;
  TimerResolutionExperiment(const TimerResolutionExperiment&) = delete;
  TimerResolutionExperiment& operator=(const TimerResolutionExperiment&) = delete;
  ~TimerResolutionExperiment() { Reset(); }
  void StartFromEnvironment() {
#ifdef _WIN32
    const char* value = std::getenv("AOT_TIMER_RESOLUTION_MS");
    if (!value || std::string_view(value) != "1" || active_) return;
    library_ = LoadLibraryExW(L"winmm.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library_) { REXLOG_WARN("Timer resolution experiment: winmm unavailable"); return; }
    auto begin = reinterpret_cast<PeriodFunction>(GetProcAddress(library_, "timeBeginPeriod"));
    end_ = reinterpret_cast<PeriodFunction>(GetProcAddress(library_, "timeEndPeriod"));
    if (!begin || !end_) { Reset(); return; }
    const auto result = begin(1);
    active_ = result == 0;
    REXLOG_INFO("Timer resolution experiment: request=1ms, result={}, active={}", result, active_);
    if (!active_) Reset();
#endif
  }
  void Reset() {
#ifdef _WIN32
    if (active_) {
      const auto result = end_(1);
      active_ = false;
      REXLOG_INFO("Timer resolution experiment: release=1ms, result={}", result);
    }
    if (library_) { FreeLibrary(library_); library_ = nullptr; }
    end_ = nullptr;
#endif
  }
 private:
#ifdef _WIN32
  using PeriodFunction = UINT (WINAPI*)(UINT);
  HMODULE library_ = nullptr;
  PeriodFunction end_ = nullptr;
  bool active_ = false;
#endif
};
}  // namespace aot
