#pragma once
#include <cmath>
#include <cstdint>

namespace aot {
struct DofDimensions {
  uint32_t scene_w, scene_h, factor, filter_w, filter_h;
};
struct DofRectangle {
  double x, y, width, height;
  uint32_t target_w, target_h, source_w, source_h;
};
// Verified retail return addresses: Gaussian blur and final DOF composite.
// Return false without touching output for every unsupported call/layout.
inline bool CorrectDofRectangle(uint32_t caller, const DofDimensions& d,
                                const DofRectangle& input, DofRectangle& output) {
  const bool gaussian = caller == 0x8255FF84;
  if (!gaussian && caller != 0x82498678) return false;
  const double border = gaussian ? 1.0 : 0.0;
  const double x = input.x - border, y = input.y - border;
  if (d.factor != 4 || d.scene_w < 4 || d.scene_w > 8192 || d.scene_h < 4 || d.scene_h > 8192 ||
      d.filter_w != d.scene_w / 4 + 2 || d.filter_h != d.scene_h / 4 + 2 ||
      input.target_w != (gaussian ? d.filter_w : d.scene_w) ||
      input.target_h != (gaussian ? d.filter_h : d.scene_h) ||
      input.source_w != d.scene_w || input.source_h != d.scene_h ||
      !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(input.width) || !std::isfinite(input.height) ||
      x < 0 || y < 0 || input.width <= 0 || input.height <= 0 ||
      x + input.width > d.scene_w || y + input.height > d.scene_h) return false;
  output = input;
  output.x = double(float(x / d.factor + 1.0));
  output.y = double(float(y / d.factor + 1.0));
  output.width = double(float(input.width / d.factor));
  output.height = double(float(input.height / d.factor));
  output.source_w = d.filter_w;
  output.source_h = d.filter_h;
  return true;
}
}  // namespace aot
