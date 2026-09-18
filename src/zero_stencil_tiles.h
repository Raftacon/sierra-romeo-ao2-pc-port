#pragma once
#include <algorithm>
#include <bitset>
#include <cstdint>

namespace aot {
// Knowledge about actual host depth-texture stencil contents, not guest EDRAM
// ownership. New resources are unknown; optimized clear values are not clears.
class ZeroStencilTiles {
 public:
  static constexpr uint32_t kCount = 2048;
  void Invalidate() { zero_.reset(); }
  void InvalidateRange(uint32_t base, uint32_t start, uint32_t end) {
    if (!Valid(base, start, end)) { Invalidate(); return; }
    for (uint32_t tile = start; tile < end; ++tile)
      zero_.reset((tile - base) & (kCount - 1));
  }
  bool Contains(uint32_t base, uint32_t start, uint32_t end) const {
    if (!Valid(base, start, end)) return false;
    for (uint32_t tile = start; tile < end; ++tile)
      if (!zero_[(tile - base) & (kCount - 1)]) return false;
    return true;
  }
  void TransferFrom(const ZeroStencilTiles& source, uint32_t source_base,
                    uint32_t destination_base, uint32_t start, uint32_t end) {
    if (&source == this || !Valid(source_base, start, end) ||
        !Valid(destination_base, start, end)) {
      InvalidateRange(destination_base, start, end); return;
    }
    for (uint32_t tile = start; tile < end; ++tile)
      zero_.set((tile - destination_base) & (kCount - 1),
                source.zero_[(tile - source_base) & (kCount - 1)]);
  }
  // Coordinates are unscaled host pixels. A 32bpp EDRAM tile is 80x16 samples;
  // 2x MSAA halves its pixel height, and 4x also halves its pixel width.
  void Clear(uint32_t pitch, uint32_t msaa_log2, uint32_t x, uint32_t y,
             uint32_t width, uint32_t height, bool value_is_zero) {
    if (!pitch || pitch > kCount || msaa_log2 > 2) { Invalidate(); return; }
    if (!width || !height) return;
    const uint32_t tw = msaa_log2 == 2 ? 40 : 80;
    const uint32_t th = msaa_log2 ? 8 : 16;
    const uint64_t right = uint64_t(x) + width, bottom = uint64_t(y) + height;
    // Zero adds only fully covered tiles. Nonzero invalidates even a single
    // touched pixel. Existing knowledge survives a partial zero clear.
    const uint64_t first_x = value_is_zero ? (uint64_t(x) + tw - 1) / tw : x / tw;
    const uint64_t first_y = value_is_zero ? (uint64_t(y) + th - 1) / th : y / th;
    const uint64_t last_x = std::min<uint64_t>(pitch,
        value_is_zero ? right / tw : (right + tw - 1) / tw);
    const uint64_t last_y = std::min<uint64_t>((kCount + pitch - 1) / pitch,
        value_is_zero ? bottom / th : (bottom + th - 1) / th);
    for (uint64_t row = first_y; row < last_y; ++row)
      for (uint64_t col = first_x; col < last_x; ++col) {
        const uint64_t tile = row * pitch + col;
        if (tile < kCount) zero_.set(size_t(tile), value_is_zero);
      }
  }
 private:
  static bool Valid(uint32_t base, uint32_t start, uint32_t end) {
    return base < kCount && start < end && end <= kCount;
  }
  std::bitset<kCount> zero_{};
};
}  // namespace aot
