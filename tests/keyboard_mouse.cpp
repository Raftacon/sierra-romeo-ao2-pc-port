#include "src/keyboard_mouse.h"
#include <rex/cvar.h>
#include <rex/ui/window_listener.h>
#include <iostream>
#include <filesystem>
#include <fstream>
#include <thread>
#include <chrono>
#include <windows.h>

using namespace rex;
using namespace rex::input;

int main() {
  auto driver = aot::CreateKeyboardMouseDriver();
  auto* keys = dynamic_cast<rex::ui::WindowInputListener*>(driver.get());
  auto* window = dynamic_cast<rex::ui::WindowListener*>(driver.get());
  if (!keys || !window) return 1;
  std::vector<DeviceInfo> devices; driver->EnumerateDevices(devices);
  if (devices.size() != 1 || !devices[0].synthetic) return 2;
  const auto id = devices[0].id;
  rex::ui::UISetupEvent focus;
  window->OnGotFocus(focus);
  auto key = [&](unsigned code, bool down, bool shift = false) {
    rex::ui::KeyEvent event(nullptr, static_cast<rex::ui::VirtualKey>(code), 1, false, shift, false, false, false);
    if (down) keys->OnKeyDown(event); else keys->OnKeyUp(event);
  };
  auto sample = [&] { X_INPUT_STATE value{}; driver->GetDeviceState(id, &value); return value.gamepad; };
  auto require = [](bool ok, const char* label) { if (!ok) { std::cerr << label << '\n'; std::exit(1); } };
  aot::SetKeyboardMenuContext(true, false, true);
  key(13, true); require(sample().buttons == 0x10, "Enter must dismiss the title screen");
  aot::SetKeyboardMenuContext(false, true, true);
  key(13, true); require(sample().buttons == 0x10, "Held/repeated Enter must not select after leaving the title"); key(13, false);
  aot::SetKeyboardMenuContext(false, true, true);
  key(13, true); require(sample().buttons == 0x1000, "Enter must select without also opening Exit"); key(13, false);
  key(27, true); require(sample().buttons == 0x10, "Escape must open main-menu Exit");
  aot::SetKeyboardMenuContext(false, false, true);
  key(27, true); require(sample().buttons == 0x10, "Held/repeated Escape must not cancel the dialog it opened"); key(27, false);
  key(27, true); require(sample().buttons == 0x2000, "A fresh Escape must cancel a dialog"); key(27, false);
  aot::SetKeyboardMenuContext(false, false, false); aot::ObserveMouseGameplay();
  std::this_thread::sleep_for(std::chrono::milliseconds(350));
  key(27, true); require(sample().buttons == 0x10, "A game-thread stall must not change Escape from Pause to Cancel"); key(27, false);
  for (int i = 0; i < 3; ++i) aot::ObserveMouseGameFrame();
  key(27, true); require(sample().buttons == 0x2000, "Advancing without player input must retire the gameplay context"); key(27, false);
  aot::ObserveMouseGameplay(); aot::ObserveFullscreenMovie();
  key(27, true); require(sample().buttons == 0x2000, "A movie must invalidate prior gameplay even when game frames stop"); key(27, false);
  aot::ObserveMouseGameplay(); aot::SetKeyboardMenuContext(false, false, true);
  aot::SetKeyboardMenuContext(false, false, false);
  key(27, true); require(sample().buttons == 0x2000, "Closing a menu must wait for fresh gameplay input"); key(27, false);
  aot::ObserveMouseGameplay();
  key('W', true); key(16, true, true);
  require(sample().thumb_ly == 32767, "Holding Shift must not stop forward movement");
  key('D', true); auto diagonal = sample();
  require(diagonal.thumb_lx == diagonal.thumb_ly && diagonal.thumb_lx > 23000 && diagonal.thumb_lx < 23200, "Diagonal movement must remain within stick radius");
  key('R', true, true); require(sample().buttons & 0x4000, "Reload must work while Shift is held");
  window->OnLostFocus(focus); auto unfocused = sample();
  require(unfocused.buttons == 0 && unfocused.thumb_ly == 0, "Focus loss must release held movement and buttons");
  window->OnGotFocus(focus); require(sample().thumb_ly == 0, "Focus return must not restore stale keys");
  key('W', true); driver->set_is_active_callback([] { return false; });
  require(sample().thumb_ly == 0, "Interactive overlays must receive neutral game input");
  driver->set_is_active_callback([] { return true; });
  require(sample().thumb_ly == 0, "Closing an overlay must not restore stale keys");
  rex::cvar::SetFlagByName("aot_keyboard_mouse", "false");
  devices.clear(); driver->EnumerateDevices(devices);
  X_INPUT_STATE state{};
  require(devices.empty() && driver->GetDeviceState(id, &state) == X_ERROR_DEVICE_NOT_CONNECTED, "Controller-only mode must remove the synthetic device");
  // Exercise the production factory and its observed stock XInput driver with
  // the isolated test DLL. This also checks that both devices reach user 0.
  const auto directory = std::filesystem::current_path() / "test-controller";
  const auto path = directory / "keyboard-contract.pad";
  SetDllDirectoryW(directory.c_str());
  _putenv_s("AOT_INPUT_SCRIPT", ""); _putenv_s("AOT_INPUT_STATE", path.string().c_str());
  std::ofstream(path) << "0000 0 0 0 0 0 0\n";
  rex::cvar::SetFlagByName("input_backend", "xinput");
  rex::cvar::SetFlagByName("aot_keyboard_mouse", "true");
  auto input = aot::CreatePcInput(false);
  auto* polling = dynamic_cast<rex::input::InputSystem*>(input.get());
  require(polling != nullptr, "Production factory must expose the native input system");
  aot::SetKeyboardMenuContext(false, true, true);
  key('E', true);
  require(polling->GetState(0, &state) == 0 && state.gamepad.buttons == 0x1000 && aot::KeyboardPrompts(),
      "Production factory must accept keyboard input alongside a neutral pad");
  std::ofstream(path) << "2000 0 0 0 0 0 0\n";
  require(polling->GetState(0, &state) == 0 && state.gamepad.buttons == 0x3000 && !aot::KeyboardPrompts(),
      "Physical pad input must merge with keyboard input and restore controller glyphs");
  key('E', false); key('R', true);
  require(aot::KeyboardPrompts(), "Fresh keyboard activity must restore key glyphs");
  key('R', false);
  input->Shutdown(); input.reset();
  _putenv_s("AOT_INPUT_STATE", ""); std::filesystem::remove(path);
  std::cout << "Keyboard menu, movement, modifiers and focus isolation passed\n";
}
