#pragma once

#include <cstdint>
#include <rex/cvar.h>
#include <rex/logging.h>
#ifdef _WIN32
#include <windows.h>
#endif

REXCVAR_DEFINE_BOOL(aot_precise_gpu_sleep, false, "GPU/Sierra Romeo",
    "Experimental high-resolution timer for the original GPU packet sleep duration");

namespace aot {
// False means the caller must execute the original sleep. This helper never
// checks or modifies the guest memory condition being waited on.
inline bool TryPreciseGpuPacketSleep(uint32_t milliseconds) {
#ifdef _WIN32
  if (!REXCVAR_GET(aot_precise_gpu_sleep) || !milliseconds) return false;
  struct Timer {
    HANDLE handle = CreateWaitableTimerExW(nullptr, nullptr,
        CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE);
    Timer() { REXLOG_INFO("Precise GPU packet timer: {}", handle ? "available" : "unavailable; using original sleep"); }
    ~Timer() { if (handle) CloseHandle(handle); }
  };
  static thread_local Timer timer;
  if (!timer.handle) return false;
  LARGE_INTEGER due;
  due.QuadPart = -static_cast<LONGLONG>(milliseconds) * 10000;
  if (!SetWaitableTimer(timer.handle, &due, 0, nullptr, nullptr, FALSE)) return false;
  // The packet operand is uint32 / 256, so adding 100 cannot overflow DWORD.
  if (WaitForSingleObject(timer.handle, milliseconds + 100) == WAIT_OBJECT_0) return true;
  CancelWaitableTimer(timer.handle);
#endif
  return false;
}
}  // namespace aot
