#pragma once

#include <cmath>
#include <cstdint>
#include <rex/chrono/clock.h>
#include <rex/cvar.h>
#include <rex/logging.h>
#ifdef _WIN32
#include <windows.h>
#endif

REXCVAR_DEFINE_BOOL(aot_precise_vblank_sleep, false, "GPU/Sierra Romeo",
    "Experimental deadline timer for guest VBlank; preserves original callback cadence");

namespace aot {
// False asks the caller to use its original one-millisecond polling sleep.
// The outer guest-clock loop remains responsible for all callback dispatch.
inline bool TryPreciseVblankSleep(uint64_t last_frame_ticks, uint64_t interval_ticks,
                                  uint64_t guest_frequency) {
#ifdef _WIN32
  if (!REXCVAR_GET(aot_precise_vblank_sleep) || !guest_frequency ||
      !interval_ticks || interval_ticks > guest_frequency / 30 ||
      rex::chrono::Clock::guest_time_scalar() != 1.0) return false;
  struct Timer {
    HANDLE handle = CreateWaitableTimerExW(nullptr, nullptr,
        CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE);
    Timer() { REXLOG_INFO("Precise VBlank deadline timer: {}",
        handle ? "available" : "unavailable; using original sleep"); }
    ~Timer() { if (handle) CloseHandle(handle); }
  };
  static thread_local Timer timer;
  if (!timer.handle) return false;
  const uint64_t now = rex::chrono::Clock::QueryGuestTickCount();
  if (now < last_frame_ticks) return false;
  const uint64_t elapsed = now - last_frame_ticks;
  if (elapsed >= interval_ticks) return true;  // Recheck the original loop now.
  const auto remaining_100ns = static_cast<int64_t>(std::ceil(
      double(interval_ticks - elapsed) * 10000000.0 / double(guest_frequency)));
  LARGE_INTEGER due;
  due.QuadPart = -remaining_100ns;
  if (!SetWaitableTimer(timer.handle, &due, 0, nullptr, nullptr, FALSE)) return false;
  const DWORD timeout = static_cast<DWORD>((remaining_100ns + 9999) / 10000) + 100;
  if (WaitForSingleObject(timer.handle, timeout) == WAIT_OBJECT_0) return true;
  CancelWaitableTimer(timer.handle);
#endif
  return false;
}
}  // namespace aot
