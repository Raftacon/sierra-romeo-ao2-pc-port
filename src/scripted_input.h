#pragma once

#include <rex/input/input_system.h>
#include <rex/logging.h>
#include <chrono>
#include <fstream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include "input_script.h"
#include "keyboard_mouse.h"

namespace aot {
using rex::X_STATUS;
using rex::X_RESULT;

// Optional test controller. Timings are elapsed milliseconds from the first
// guest input poll, not deterministic simulation frames. Normal play uses the
// SDK's normal input drivers. Only enabled with AOT_INPUT_SCRIPT.
class ScriptedInput final : public rex::input::InputDriver {
 public:
  explicit ScriptedInput(const std::string& path, std::string live = {})
      : InputDriver(nullptr, 0), script_(path), live_(std::move(live)) {}

  X_STATUS Setup() override { return X_STATUS_SUCCESS; }
  void EnumerateDevices(std::vector<rex::input::DeviceInfo>& out) override {
    out.push_back({kId, 0, "Sierra Romeo test controller", "aot-script", true});
  }
  X_RESULT GetDeviceState(rex::input::DeviceId id, rex::input::X_INPUT_STATE* state) override {
    if (id != kId) return X_ERROR_DEVICE_NOT_CONNECTED;
    std::lock_guard lock(mutex_);
    const auto now = std::chrono::steady_clock::now();
    if (!started_) {
      start_ = now; started_ = true;
      const auto unix_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count();
      REXLOG_INFO("AOT scripted controller clock started: unix_ms={}", unix_ms);
    }
    const uint64_t elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - start_).count();
    *state = {};
    state->packet_number = ++packet_;
    auto pad = script_.Sample(elapsed);
    if (!live_.empty()) {
      auto live = ReadLivePad(live_);
      pad.buttons |= live.buttons;
      if (live.lt) pad.lt = live.lt;
      if (live.rt) pad.rt = live.rt;
      if (live.lx) pad.lx = live.lx;
      if (live.ly) pad.ly = live.ly;
      if (live.rx) pad.rx = live.rx;
      if (live.ry) pad.ry = live.ry;
    }
    state->gamepad.buttons = pad.buttons;
    state->gamepad.left_trigger = pad.lt;
    state->gamepad.right_trigger = pad.rt;
    state->gamepad.thumb_lx = pad.lx;
    state->gamepad.thumb_ly = pad.ly;
    state->gamepad.thumb_rx = pad.rx;
    state->gamepad.thumb_ry = pad.ry;
    return X_ERROR_SUCCESS;
  }
  X_RESULT GetDeviceCapabilities(rex::input::DeviceId id, uint32_t, rex::input::X_INPUT_CAPABILITIES* caps) override {
    if (id != kId) return X_ERROR_DEVICE_NOT_CONNECTED;
    *caps = {};
    caps->type = 1;
    caps->sub_type = 1;
    caps->gamepad.buttons = 0xF3FF;
    caps->gamepad.left_trigger = caps->gamepad.right_trigger = 255;
    caps->gamepad.thumb_lx = caps->gamepad.thumb_ly = 32767;
    caps->gamepad.thumb_rx = caps->gamepad.thumb_ry = 32767;
    return X_ERROR_SUCCESS;
  }
  X_RESULT SetDeviceVibration(rex::input::DeviceId id, rex::input::X_INPUT_VIBRATION*) override {
    return id == kId ? X_ERROR_SUCCESS : X_ERROR_DEVICE_NOT_CONNECTED;
  }
  X_RESULT GetDeviceKeystroke(rex::input::DeviceId id, uint32_t, rex::input::X_INPUT_KEYSTROKE*) override {
    return id == kId ? X_ERROR_EMPTY : X_ERROR_DEVICE_NOT_CONNECTED;
  }

 private:
  static constexpr auto kId = static_cast<rex::input::DeviceId>(0x414F5401);
  InputScript script_;
  std::string live_;
  std::mutex mutex_;
  std::chrono::steady_clock::time_point start_;
  bool started_ = false;
  uint32_t packet_ = 0;
};

inline std::unique_ptr<rex::system::IInputSystem> CreateScriptedInput(const std::string& path, const std::string& live) {
  auto input = std::make_unique<rex::input::InputSystem>(nullptr);
  auto driver = std::make_unique<ScriptedInput>(path, live);
  driver->Setup();
  input->AddDriver(std::move(driver));
  if (std::getenv("AOT_TEST_KBM")) input->AddDriver(CreateKeyboardMouseDriver());
  input->SetDeviceAssignment(std::make_unique<rex::input::SlotAssignment>());
  return input;
}
}  // namespace aot
