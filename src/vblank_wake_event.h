#pragma once
#include <cstdint>
#ifdef _WIN32
#include <windows.h>

namespace aot {
// A notification to retry the original guest condition, never a completion
// indication. Snapshot before reading that condition to avoid a lost wake.
class VblankWakeEvent {
 public:
  uint64_t Snapshot() {
    return static_cast<uint64_t>(InterlockedCompareExchange64(&sequence_,0,0));
  }
  void Signal() {
    InterlockedIncrement64(&sequence_);
    WakeByAddressAll(const_cast<LONG64*>(&sequence_));
  }
  bool Wait(uint64_t observed,uint32_t milliseconds) {
    LONG64 expected=static_cast<LONG64>(observed);
    if (WaitOnAddress(&sequence_,&expected,sizeof(expected),milliseconds)) return true;
    // A timeout has already provided the original backoff. Other failures ask
    // the caller to use its original Sleep path.
    return GetLastError()==ERROR_TIMEOUT;
  }
 private:
  alignas(8) volatile LONG64 sequence_=0;
};
}
#endif
