// Test-only XInput device for a separate, local Xenia baseline directory.
// Never installed into Windows or into the normal Xenia directory.
#include <Windows.h>
#include <Xinput.h>
#include <chrono>
#include <cstdlib>
#include <mutex>
#include "../src/input_script.h"

namespace {
std::mutex mutex;
const char* env(const char* name) { const char* value = std::getenv(name); return value ? value : ""; }
bool enabled() { return *env("AOT_INPUT_SCRIPT") || *env("AOT_INPUT_STATE"); }
}

extern "C" DWORD WINAPI XInputGetState(DWORD user, XINPUT_STATE* state) {
  if (user || !enabled()) return ERROR_DEVICE_NOT_CONNECTED;
  if (!state) return ERROR_BAD_ARGUMENTS;
  std::lock_guard lock(mutex);
  try {
    static aot::InputScript script(env("AOT_INPUT_SCRIPT"));
    static auto start = std::chrono::steady_clock::now();
    static DWORD packet = 0;
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
    auto pad = script.Sample(elapsed);
    if (*env("AOT_INPUT_STATE")) {
      auto live = aot::ReadLivePad(env("AOT_INPUT_STATE"));
      pad.buttons |= live.buttons;
      if (live.lt) pad.lt = live.lt;
      if (live.rt) pad.rt = live.rt;
      if (live.lx) pad.lx = live.lx;
      if (live.ly) pad.ly = live.ly;
      if (live.rx) pad.rx = live.rx;
      if (live.ry) pad.ry = live.ry;
    }
    *state = {};
    state->dwPacketNumber = ++packet;
    state->Gamepad = {pad.buttons, pad.lt, pad.rt, pad.lx, pad.ly, pad.rx, pad.ry};
    return ERROR_SUCCESS;
  } catch (...) { return ERROR_BAD_ARGUMENTS; }
}
extern "C" DWORD WINAPI XInputGetCapabilities(DWORD user, DWORD, XINPUT_CAPABILITIES* caps) {
  if (user || !enabled()) return ERROR_DEVICE_NOT_CONNECTED;
  if (!caps) return ERROR_BAD_ARGUMENTS;
  *caps = {};
  caps->Type = XINPUT_DEVTYPE_GAMEPAD;
  caps->SubType = XINPUT_DEVSUBTYPE_GAMEPAD;
  caps->Gamepad = {0xF3FF, 255, 255, 32767, 32767, 32767, 32767};
  return ERROR_SUCCESS;
}
extern "C" DWORD WINAPI XInputSetState(DWORD user, XINPUT_VIBRATION*) { return user || !enabled() ? ERROR_DEVICE_NOT_CONNECTED : ERROR_SUCCESS; }
extern "C" DWORD WINAPI XInputGetKeystroke(DWORD user, DWORD, PXINPUT_KEYSTROKE) { return user || !enabled() ? ERROR_DEVICE_NOT_CONNECTED : ERROR_EMPTY; }
extern "C" void WINAPI XInputEnable(BOOL) {}
