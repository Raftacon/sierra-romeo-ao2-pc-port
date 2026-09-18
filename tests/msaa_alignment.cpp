#include "src/msaa_alignment.h"
#include <cstdio>
#include <initializer_list>
#include <limits>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "Failed at line %d\n", __LINE__); return 1; } } while (false)
int main() {
  // Reconstruct the sampled surface coordinate, rather than just comparing a
  // returned constant. The observed resolve chooses standard D3D sample 1.
  for (unsigned scale : {1u}) {
    for (unsigned guest_extent : {720u, 208u}) {
      const auto height = guest_extent * scale;
      const float delta = aot::MsaaAlignmentDelta(true, true, true, 1, 524288,
          1087, 4, 639.75f, 360.25f, scale, height);
      const double screen_shift = -double(delta) * height / 2;
      // Before correction sample 1 (.25,.25) evaluates the source at Y-.5,
      // while the later single-sample draw (.5,.5) evaluates the surface at Y.
      const double msaa_surface_y = .25 * scale - (.75 * scale + screen_shift);
      const double single_surface_y = .5 * scale - .5 * scale;
      CHECK(std::abs(msaa_surface_y - single_surface_y) < 1e-6);
    }
  }
  for (unsigned n = 0; n < 15; ++n) {
    bool enabled = true, host = true, host2 = true;
    unsigned samples = 1, clip = 524288, vte = 1087, vertex = 4, scale = 1, height = 720;
    float x = 639.75f, y = 360.25f;
    switch (n) {
      case 0: enabled = false; break;
      case 1: host = false; break;
      case 2: host2 = false; break;
      case 3: samples = 0; break;
      case 4: samples = 2; break;
      case 5: clip |= 1; break; // user clipping
      case 6: clip |= 65536; break; // pretransformed clear/UI draw
      case 7: vte = 768; break;
      case 8: vertex |= 1; break;
      case 9: x = 640; y = 360; break; // unjittered scene
      case 10: y = std::numeric_limits<float>::quiet_NaN(); break;
      case 11: scale = 0; break;
      case 12: height = 0; break;
      case 13: scale = 2; break; // unverified host sample/resolve placement
      case 14: scale = 3; break;
    }
    CHECK(aot::MsaaAlignmentDelta(enabled, host, host2, samples, clip, vte,
        vertex, x, y, scale, height) == 0);
  }
  return 0;
}
