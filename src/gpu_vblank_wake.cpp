#include "gpu_vblank_wake.h"
#include "vblank_wake_event.h"
#include <rex/cvar.h>

REXCVAR_DEFINE_BOOL(aot_gpu_vblank_wake,true,"GPU/Sierra Romeo",
    "Notify after VBlank to recheck pending GPU packet conditions")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

namespace aot {
#ifdef _WIN32
namespace { VblankWakeEvent wake; }
#endif
uint64_t SnapshotGpuVblankWake() {
#ifdef _WIN32
  if (REXCVAR_GET(aot_gpu_vblank_wake)) return wake.Snapshot();
#endif
  return 0;
}
bool TryGpuVblankWake(uint64_t observed,uint32_t milliseconds) {
#ifdef _WIN32
  if (REXCVAR_GET(aot_gpu_vblank_wake) && milliseconds) return wake.Wait(observed,milliseconds);
#endif
  return false;
}
void NotifyGpuVblankWake() {
#ifdef _WIN32
  if (REXCVAR_GET(aot_gpu_vblank_wake)) wake.Signal();
#endif
}
}
