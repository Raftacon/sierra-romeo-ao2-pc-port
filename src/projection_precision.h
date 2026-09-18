#pragma once
#include <cstdint>
#include <array>
#include <vector>

namespace aot {
enum class ProjectionPatchResult { Unrelated, Patched, Unsupported };
struct ProjectionTranslationLayout {
  uint32_t position, vector_result, system_constants, float_constants;
  uint32_t world = UINT32_MAX;
  // 0-3 select live world lanes; 4/5 are proven literal zero/one.
  std::array<uint32_t,4> components{};
  bool automatic = false;
  bool captured = false; // world is a translator-owned saved source temporary
  uint32_t captured_mask = 0;
  bool fused = false; // opt-in float-input world products; viewport stays separate
};
// Legacy captured-program entry point. Unsupported never modifies the input.
ProjectionPatchResult PatchProjection(uint64_t guest, uint64_t modification,
                                      std::vector<uint8_t>& binary);
// Only call with metadata from the pinned DXBC translator that emitted binary.
// Automatic layouts additionally require AnalyzeProjection's arithmetic proof.
ProjectionPatchResult PatchTranslatedProjection(uint64_t guest,
    const ProjectionTranslationLayout& layout, std::vector<uint8_t>& binary);
}
