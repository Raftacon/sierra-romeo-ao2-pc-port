#include "src/dof_uv.h"
#include <cstdio>
#include <initializer_list>
#include <limits>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "Failed at line %d\n", __LINE__); return 1; } } while(false)
int main() {
  const aot::DofDimensions dims{1280, 720, 4, 322, 182};
  const aot::DofRectangle captured{1, 1, 1280, 640, 322, 182, 1280, 720};
  aot::DofRectangle result{};
  CHECK(aot::CorrectDofRectangle(0x8255FF84, dims, captured, result));
  // The observed Gaussian destination spans x=1..321. Its source center
  // must map back to the same populated texel, across the entire width.
  for (double destination : {1.5, 2.5, 160.5, 319.5, 320.5}) {
    const double fraction = (destination - 1.0) / 320.0;
    const double sampled_texel = (result.x + fraction * result.width) / result.source_w * 322;
    CHECK(std::abs(sampled_texel - destination) < 1e-10);
  }
  CHECK(result.target_w == captured.target_w && result.target_h == captured.target_h);
  auto crop = captured; crop.x = 321; crop.y = 161; crop.width = 640; crop.height = 320;
  CHECK(aot::CorrectDofRectangle(0x8255FF84, dims, crop, result));
  CHECK(result.x == 81 && result.y == 41 && result.width == 160 && result.height == 80);
  const aot::DofRectangle final{0, 0, 1280, 640, 1280, 720, 1280, 720};
  CHECK(aot::CorrectDofRectangle(0x82498678, dims, final, result));
  for (double fraction : {.5 / 1280, 1279.5 / 1280}) {
    const double texel = result.x + fraction * result.width;
    CHECK(texel > .5 && texel < 321.5); // both edge pixels retain valid bilinear coverage
  }
  CHECK(result.target_w == 1280 && result.target_h == 720);
  const aot::DofRectangle sentinel{-17, -19, -23, -29, 31, 37, 41, 43};
  for (int case_number = 0; case_number < 9; ++case_number) {
    auto bad = captured; auto d = dims; uint32_t caller = 0x8255FF84;
    switch(case_number) {
      case 0: caller = 0x82498024; break; // original downsample path must remain unchanged
      case 1: d.factor = 0; break;
      case 2: d.filter_w = 320; break;
      case 3: bad.source_w = 322; break;
      case 4: bad.target_h = 720; break;
      case 5: bad.x = 0; break;
      case 6: bad.width = 1281; break;
      case 7: bad.height = std::numeric_limits<double>::infinity(); break;
      case 8: bad.x = std::numeric_limits<double>::quiet_NaN(); break;
    }
    result = sentinel;
    CHECK(!aot::CorrectDofRectangle(caller, d, bad, result));
    CHECK(result.x == sentinel.x && result.y == sentinel.y && result.width == sentinel.width &&
          result.height == sentinel.height && result.target_w == sentinel.target_w && result.target_h == sentinel.target_h &&
          result.source_w == sentinel.source_w && result.source_h == sentinel.source_h);
  }
}
