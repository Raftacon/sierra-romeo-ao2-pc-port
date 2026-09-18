#include "src/zero_stencil_tiles.h"
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

static void Check(bool condition) { if (!condition) std::abort(); }
int main() {
  using aot::ZeroStencilTiles;
  for (uint32_t msaa = 0; msaa != 3; ++msaa) {
    const uint32_t tw = msaa == 2 ? 40 : 80, th = msaa ? 8 : 16;
    for (uint32_t pitch : {1u, 7u, 16u, 33u}) {
      ZeroStencilTiles state;
      Check(!state.Contains(0, 0, 1));
      state.Clear(pitch, msaa, 0, 0, pitch * tw, th, true);
      Check(state.Contains(0, 0, pitch));
      state.Clear(pitch, msaa, tw - 1, th - 1, 1, 1, false);
      Check(!state.Contains(0, 0, 1));
      state.Clear(pitch, msaa, tw - 1, th - 1, 1, 1, true);
      Check(!state.Contains(0, 0, 1)); // partial clear is not whole-tile proof
      state.Clear(pitch, msaa, 0, 0, tw, th, true);
      Check(state.Contains(0, 0, pitch));
      ZeroStencilTiles other;
      other.TransferFrom(state, 2047, 17, 2047, 2048);
      Check(other.Contains(17, 2047, 2048));
      other.TransferFrom(state, 2047, 17, 0, 1);
      Check(other.Contains(17, 0, 1) == (pitch > 1));
      other.InvalidateRange(17, 2047, 2048);
      Check(!other.Contains(17, 2047, 2048));

      // Independent pixel oracle: arbitrary overlapping zero/nonzero clears.
      // The tracker may conservatively miss zero tiles, but may never claim a
      // tile known-zero when the oracle still has an unknown/nonzero pixel.
      state.Invalidate();
      const uint32_t rows = 5, w = pitch * tw, h = rows * th;
      std::vector<uint8_t> pixels(w * h, 0);
      std::mt19937 rng(341 + pitch + msaa * 100);
      unsigned accepted = 0;
      for (unsigned step = 0; step < 180; ++step) {
        uint32_t x = rng() % w, y = rng() % h;
        uint32_t cw = 1 + rng() % (w - x), ch = 1 + rng() % (h - y);
        bool zero = (rng() % 3) != 0;
        if (step % 20 == 0) { x = y = 0; cw = w; ch = h; zero = true; }
        state.Clear(pitch, msaa, x, y, cw, ch, zero);
        for (uint32_t py = y; py < y + ch; ++py)
          for (uint32_t px = x; px < x + cw; ++px) pixels[py * w + px] = zero;
        for (uint32_t tile = 0; tile < rows * pitch; ++tile) {
          if (!state.Contains(0, tile, tile + 1)) continue;
          ++accepted;
          for (uint32_t dy = 0; dy < th; ++dy)
            for (uint32_t dx = 0; dx < tw; ++dx)
              Check(pixels[((tile / pitch) * th + dy) * w + (tile % pitch) * tw + dx]);
        }
      }
      Check(accepted > 0);
      state.Clear(pitch, msaa, UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX, false);
      Check(!state.Contains(2048, 0, 1));
      Check(!state.Contains(0, 5, 4));
      Check(!state.Contains(0, 0, 2049));
      state.Invalidate();
      Check(!state.Contains(0, 0, 1));
    }
  }
  std::puts("Zero-stencil tile knowledge: clear coverage, MSAA, wrap, transfer and pixel oracle passed");
}
