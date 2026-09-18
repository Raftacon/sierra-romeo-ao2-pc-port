// Exercise the stock SDK XInput backend against the isolated test DLL.
#include <rex/input/input_system.h>
#include <rex/cvar.h>
#include <windows.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include "../src/input_script.h"

int main() {
  auto directory = std::filesystem::current_path() / "test-controller";
  auto path = directory / "contract.pad";
  try {
    SetDllDirectoryW(directory.c_str());
    _putenv_s("AOT_INPUT_SCRIPT", "");
    _putenv_s("AOT_INPUT_STATE", path.string().c_str());
    std::ofstream(path) << "9000 17 239 -32768 32767 -12345 12345\n";
    rex::cvar::SetFlagByName("input_backend", "xinput");
    rex::cvar::SetFlagByName("mnk_mode", "false");
    auto input = rex::input::CreateDefaultInputSystem(false);
    wchar_t loaded[MAX_PATH]{};
    GetModuleFileNameW(GetModuleHandleW(L"xinput1_4.dll"), loaded, MAX_PATH);
    std::wcout << "XInput DLL: " << loaded << '\n';
    std::cout << "Backend: " << rex::cvar::GetFlagByName("input_backend") << '\n';
    std::cout << "Live file: " << aot::ReadLivePad(path.string()).buttons << '\n';
    rex::input::X_INPUT_STATE state{};
    rex::input::X_INPUT_CAPABILITIES caps{};
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    require(input->GetCapabilities(0, 0, &caps) == 0, "Gamepad not assigned to player 1");
    require(input->GetState(0, &state) == 0, "State unavailable through stock XInput backend");
    std::cout << "state=" << std::hex << uint16_t(state.gamepad.buttons) << std::dec << " "
        << unsigned(state.gamepad.left_trigger) << " " << unsigned(state.gamepad.right_trigger)
        << " " << int16_t(state.gamepad.thumb_lx) << " " << int16_t(state.gamepad.thumb_ly) << '\n';
    require(state.gamepad.buttons == 0x9000 && state.gamepad.left_trigger == 17 && state.gamepad.right_trigger == 239,
            "Button/trigger translation failed");
    require(state.gamepad.thumb_lx == -32768 && state.gamepad.thumb_ly == 32767 &&
            state.gamepad.thumb_rx == -12345 && state.gamepad.thumb_ry == 12345, "Stick translation failed");
    rex::input::X_INPUT_VIBRATION rumble{};
    rumble.left_motor_speed = 123; rumble.right_motor_speed = 456;
    require(input->SetState(0, &rumble) == 0, "Rumble dispatch failed");
    _putenv_s("AOT_INPUT_STATE", "");
    input->GetState(0, &state);
    require(state.gamepad.buttons == 0 && state.gamepad.left_trigger == 0,
            "Disconnected controller retains held input");
    input->Shutdown();
    std::filesystem::remove(path);
    std::cout << "Stock XInput: slot assignment, buttons, triggers, sticks, rumble dispatch, disconnect passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::error_code ignored; std::filesystem::remove(path, ignored);
    std::cerr << error.what() << '\n'; return 1;
  }
}
