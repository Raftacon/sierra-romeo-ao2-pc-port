#include "projection_precision_runtime.h"
#include "projection_precision.h"
#include <atomic>
#include <d3d12.h>
#include <rex/assert.h>
#include <rex/cvar.h>
#include <rex/logging.h>

REXCVAR_DEFINE_BOOL(aot_projection_precision, true, "GPU/Sierra Romeo",
    "Scene projection correction; requires restart and supported double operations")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(aot_wall_projection_precision, false, "GPU/Sierra Romeo",
    "Legacy static projection group for comparisons; requires restart")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(aot_automatic_projection_precision, true, "GPU/Sierra Romeo",
    "Recognize projection arithmetic across materials; requires restart")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(aot_projection_source_snapshots, true, "GPU/Sierra Romeo",
    "Preserve proven projection source reads before register reuse; requires restart")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);
REXCVAR_DEFINE_BOOL(aot_projection_fma, true, "GPU/Sierra Romeo",
    "Fused double world projection; requires restart")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace aot {
namespace { std::atomic<bool> enabled{false}, wall_enabled{false}, automatic_enabled{false}, snapshots_enabled{false}, fused_enabled{false}; }
void InitializeProjectionPrecision(ID3D12Device* device) {
  D3D12_FEATURE_DATA_D3D12_OPTIONS options{};
  const bool requested=REXCVAR_GET(aot_projection_precision);
  const bool supported=requested && SUCCEEDED(device->CheckFeatureSupport(
      D3D12_FEATURE_D3D12_OPTIONS,&options,sizeof(options))) && options.DoublePrecisionFloatShaderOps;
  enabled.store(supported);
  // D3D12 DoublePrecisionFloatShaderOps includes extended double instructions.
  fused_enabled.store(supported && REXCVAR_GET(aot_projection_fma));
  wall_enabled.store(supported && REXCVAR_GET(aot_wall_projection_precision));
  automatic_enabled.store(supported && REXCVAR_GET(aot_automatic_projection_precision));
  snapshots_enabled.store(automatic_enabled.load() && REXCVAR_GET(aot_projection_source_snapshots));
  if (requested) REXLOG_INFO("AOT projection precision: requested=true, supported={}, enabled={}",supported,supported);
  if (requested) REXLOG_INFO("AOT wall projection precision: enabled={}", WallProjectionPrecisionEnabled());
  if (requested) REXLOG_INFO("AOT automatic projection precision: enabled={}", AutomaticProjectionPrecisionEnabled());
  if (requested) REXLOG_INFO("AOT projection source snapshots: enabled={}", ProjectionSourceSnapshotsEnabled());
  if (requested) REXLOG_INFO("AOT projection FMA: enabled={}", ProjectionFmaEnabled());
}
bool ProjectionPrecisionEnabled() { return enabled.load(std::memory_order_relaxed); }
bool WallProjectionPrecisionEnabled() { return wall_enabled.load(std::memory_order_relaxed); }
bool AutomaticProjectionPrecisionEnabled() { return automatic_enabled.load(std::memory_order_relaxed); }
bool ProjectionSourceSnapshotsEnabled() { return snapshots_enabled.load(std::memory_order_relaxed); }
bool ProjectionFmaEnabled() { return fused_enabled.load(std::memory_order_relaxed); }
void TranslateProjectionPrecision(uint64_t guest, uint64_t modification,
    const ProjectionTranslationLayout& layout, std::vector<uint8_t>& binary) {
  if (!ProjectionPrecisionEnabled()) return;
  // The shared prepass and its material users must switch together. Retain
  // the whole-group override for diagnosis; never change only the wall pair.
  if (!layout.automatic && (guest == 0x813A25A25223C7F5ull || guest == 0x5E20CD3F81F88801ull ||
       guest == 0x91B258F7198B1CE4ull || guest == 0xD66D9932DC2EC8D8ull ||
       guest == 0x3306D6C23B238BE6ull || guest == 0xAC2A17351535ED19ull ||
       guest == 0x807B2A09A19C3B15ull || guest == 0x494DCD69B7BA177Cull ||
       guest == 0x87C093F46637D13Full || guest == 0xD66FF6280606248Cull ||
       guest == 0xB22EF913802807F8ull || guest == 0x998F2B953D9B74FDull ||
       guest == 0xCB7E063397190431ull) &&
      !WallProjectionPrecisionEnabled()) return;
  auto selected_layout=layout;
  selected_layout.fused=ProjectionFmaEnabled();
  const auto result=PatchTranslatedProjection(guest,selected_layout,binary);
  if (result==ProjectionPatchResult::Unrelated) return;
  if (result==ProjectionPatchResult::Patched) {
    REXLOG_INFO("AOT projection precision applied: guest={:016X}, modification={:016X}",guest,modification);
    return;
  }
  // Never continue with only half the depth/color pair changed.
  REXLOG_ERROR("AOT projection precision unsupported translation: guest={:016X}, modification={:016X}",guest,modification);
  rex::FlushLogging();
  rex::FatalError("Unsupported projection variant; restart with --aot_projection_precision=false or Play.cmd -NoProjectionPrecision");
}
}
