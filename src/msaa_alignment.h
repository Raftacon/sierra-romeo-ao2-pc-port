#pragma once
#include <cmath>
#include <cstdint>

namespace aot {
// Experimental correspondence for the captured quarter-jittered 2x scene at
// 1x rendering resolution. Higher-resolution sample/resolve placement is not
// covered by the captured evidence and retains the original behavior.
// Return a delta for a fresh copy of the cached viewport, never the cache itself.
// Positive host NDC Y moves geometry upward. See crate-depth-correspondence.md.
inline float MsaaAlignmentDelta(bool enabled, bool host_targets, bool host_2x,
                                uint32_t guest_samples, uint32_t clip_control,
                                uint32_t vte_control, uint32_t vertex_control,
                                float xoffset, float yoffset,
                                uint32_t resolution_scale_y, uint32_t viewport_height) {
  if (!enabled || !host_targets || !host_2x || guest_samples != 1 ||
      (clip_control & 0x3003f) != 0 || (vte_control & 0x73f) != 0x43f ||
      (vertex_control & 1) != 0 || resolution_scale_y != 1 || !viewport_height ||
      !std::isfinite(xoffset) || !std::isfinite(yoffset) ||
      xoffset - std::floor(xoffset) != 0.75f ||
      yoffset - std::floor(yoffset) != 0.25f) return 0.0f;
  return float(resolution_scale_y) / float(viewport_height);
}
}  // namespace aot
