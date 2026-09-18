#pragma once
#include <cstdint>

namespace aot {
// The caller enables Exit on eligible front-end menus, excluding modal dialogs
// and gameplay. A held Start from the title is not a new press on arrival.
struct ExitMenuPolicy {
  bool available = false;
  uint16_t previous = 0;
  uint16_t blocked = 0;
  bool Sample(bool front_end, uint16_t buttons) {
    const uint16_t pressed = buttons & ~previous;
    blocked &= buttons;
    previous = buttons;
    const bool open = front_end && available && (pressed & 0x0010);
    available = front_end;
    if (open) blocked |= buttons;
    return open;
  }
};
}
