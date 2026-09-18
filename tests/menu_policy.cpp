#include "src/menu_policy.h"
#include <cstdio>
#define CHECK(condition) do { if (!(condition)) { std::fprintf(stderr, "Failed at line %d\n", __LINE__); return 1; } } while (false)

int main() {
  aot::ExitMenuPolicy menu;
  CHECK(!menu.Sample(false, 0x0010)); // dismiss title
  CHECK(!menu.Sample(true, 0x0010)); // still holding Start after transition
  CHECK(!menu.Sample(true, 0));
  CHECK(menu.Sample(true, 0x0010));
  CHECK(!menu.Sample(true, 0x0010)); // no repeated open while held
  CHECK(!menu.Sample(false, 0)); // original confirmation overlays main menu
  CHECK(!menu.Sample(false, 0x1000)); // A handled by native dialog
  CHECK(!menu.Sample(false, 0x2000)); // B handled by native dialog
  CHECK(!menu.Sample(false, 0x0010)); // Start in a modal dialog or gameplay
  CHECK(!menu.Sample(true, 0x0010));
  CHECK(!menu.Sample(true, 0));
  CHECK(menu.Sample(true, 0x0010)); // can reopen after cancelling
  aot::ExitMenuPolicy simultaneous;
  CHECK(!simultaneous.Sample(true, 0));
  CHECK(simultaneous.Sample(true, 0x1010)); // Start and A together
  CHECK(simultaneous.blocked == 0x1010);
  CHECK(!simultaneous.Sample(false, 0x1000));
  CHECK(simultaneous.blocked == 0x1000); // held A cannot confirm the new dialog
  CHECK(!simultaneous.Sample(false, 0));
  CHECK(simultaneous.blocked == 0);
}
