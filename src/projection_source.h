#pragma once
#include <array>
#include <cstdint>

namespace aot {
struct ProjectionCapture {
  // Ordinal among ALU callbacks, including no-ops, before either ALU result
  // is stored. Fetch callbacks do not increment this ordinal.
  uint32_t alu = UINT32_MAX;
  uint32_t reg = UINT32_MAX;
  uint32_t component = UINT32_MAX;
  bool operator==(const ProjectionCapture&) const = default;
};
// A proof of oPos = c0*x + c1*y + c2*z + c3*w. With captured=false,
// components 0-3 select live world lanes and 4/5 denote proven zero/one.
// With captured=true, the translator saves the four proven source reads in
// a separate host temporary; world is only a recognition sentinel until then.
struct ProjectionSource {
  uint32_t world = UINT32_MAX;
  std::array<uint32_t,4> components{};
  bool captured = false;
  std::array<ProjectionCapture,4> captures{};
  bool operator==(const ProjectionSource&) const = default;
};
}
