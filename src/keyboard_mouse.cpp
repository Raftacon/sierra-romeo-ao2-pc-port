#include "keyboard_mouse.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/runtime.h>
#include <rex/input/sdl/sdl_input_driver.h>
#include <rex/input/xinput/xinput_input_driver.h>
#include <rex/ui/window_listener.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <unordered_map>

REXCVAR_DEFINE_BOOL(aot_keyboard_mouse, true, "Sierra Romeo/Input", "Enable PC keyboard and mouse alongside physical controllers");
REXCVAR_DEFINE_DOUBLE(aot_mouse_sensitivity, .08, "Sierra Romeo/Input", "Relative mouse sensitivity; the original camera applies aim scaling").range(.005, 1.0);

namespace aot {
rex::input::InputSystem* InputSystemForGuest() {
  return static_cast<rex::input::InputSystem*>(rex::Runtime::instance()->input_system());
}
using rex::X_STATUS;
using rex::X_RESULT;
namespace {
using namespace rex::input;
using Clock = std::chrono::steady_clock;
int64_t Now() { return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count(); }
std::atomic<bool> title_screen{true}, main_menu{false}, game_menu{true};
std::atomic<uint64_t> game_frame{1}, gameplay_frame{0};
std::atomic<int64_t> fullscreen_movie_seen{0};
bool RecentGameplay() {
  const auto observed = gameplay_frame.load();
  // A game-thread stall does not advance this clock. Two intervening frames
  // allow for the UI polling before the current frame reaches player input.
  return observed != 0 && game_frame.load() - observed <= 2;
}
std::atomic<bool> keyboard_prompts{true};
std::atomic<bool> keyboard_used{false};
std::atomic<bool> driver_present{false};
struct State {
  std::mutex mutex;
  std::array<bool, 256> keys{};
  uint16_t enter_action = 0, escape_action = 0;
  float dx = 0, dy = 0;
  bool focused = false, captured = false, overlay = false;
  uint32_t frame = UINT32_MAX;
  std::pair<float, float> sample{};
};
State state;
void Release(rex::ui::Window* window) {
  bool captured;
  { std::lock_guard lock(state.mutex); captured = state.captured; state.captured = false; state.dx = state.dy = 0; state.sample = {}; }
  if (captured) { window->SetRelativeMouseMode(false); window->ReleaseMouse(); REXLOG_INFO("PC mouse capture released"); }
}
class KeyboardMouse final : public InputDriver, public rex::ui::WindowInputListener, public rex::ui::WindowListener {
 public:
  KeyboardMouse() : InputDriver(nullptr, 0) { driver_present = true; }
  ~KeyboardMouse() override { Detach(); driver_present = false; }
  rex::X_STATUS Setup() override { return X_STATUS_SUCCESS; }
  void OnWindowAvailable(rex::ui::Window* window) override {
    window_ = window; window->AddInputListener(this, 0); window->AddListener(this);
    std::lock_guard lock(state.mutex); state.focused = window->HasFocus();
  }
  void OnClosing(rex::ui::UIEvent&) override { Detach(); }
  void OnLostFocus(rex::ui::UISetupEvent&) override {
    { std::lock_guard lock(state.mutex); state.focused = false; state.keys.fill(false); }
    if (window_) Release(window_);
  }
  void OnGotFocus(rex::ui::UISetupEvent&) override { std::lock_guard lock(state.mutex); state.focused = true; }
  void OnKeyDown(rex::ui::KeyEvent& e) override {
    const auto key = static_cast<unsigned>(e.virtual_key());
    std::lock_guard lock(state.mutex);
    if (key < state.keys.size() && state.focused && !state.overlay && !e.is_alt_pressed() && REXCVAR_GET(aot_keyboard_mouse)) {
      // Keep the action chosen on key-down until release. Opening a menu must
      // not turn the same held Escape into Cancel, or Start into Select.
      if (!state.keys[key]) {
        if (key == 13) state.enter_action = title_screen ? 0x0010 : 0x1000;
        if (key == 27) state.escape_action = main_menu || (!game_menu && RecentGameplay()) ? 0x0010 : 0x2000;
      }
      state.keys[key] = true; keyboard_used = true; keyboard_prompts = true;
    }
  }
  void OnKeyUp(rex::ui::KeyEvent& e) override {
    const auto key = static_cast<unsigned>(e.virtual_key());
    std::lock_guard lock(state.mutex); if (key < state.keys.size()) state.keys[key] = false;
  }
  void OnMouseDown(rex::ui::MouseEvent& e) override { MouseButton(e, true); }
  void OnMouseUp(rex::ui::MouseEvent& e) override { MouseButton(e, false); }
  void OnMouseMove(rex::ui::MouseEvent& e) override {
    std::lock_guard lock(state.mutex);
    if (state.captured && state.focused && !state.overlay) {
      state.dx += e.dx(); state.dy += e.dy();
      if (e.dx() || e.dy()) { keyboard_used = true; keyboard_prompts = true; }
    }
  }
  void EnumerateDevices(std::vector<DeviceInfo>& devices) override {
    if (REXCVAR_GET(aot_keyboard_mouse)) devices.push_back({kId, 0, "Sierra Romeo keyboard and mouse", "aot-kbm", true});
  }
  rex::X_RESULT GetDeviceState(DeviceId id, X_INPUT_STATE* output) override {
    if (id != kId || !REXCVAR_GET(aot_keyboard_mouse)) return X_ERROR_DEVICE_NOT_CONNECTED;
    *output = {}; output->packet_number = ++packet_;
    const bool active = is_active();
    std::lock_guard lock(state.mutex);
    if (!active || !state.focused || state.overlay) { state.keys.fill(false); return X_ERROR_SUCCESS; }
    auto down = [&](unsigned key) { return state.keys[key]; };
    uint16_t buttons = 0;
    if (down('E')) buttons |= 0x1000;
    if (down(13)) buttons |= state.enter_action;
    if (down(27)) buttons |= state.escape_action;
    if (down('F') || down(8)) buttons |= 0x2000;
    if (down('R')) buttons |= 0x4000;
    if (down(32)) buttons |= 0x8000;
    if (down('Q')) buttons |= 0x0100;
    if (down(9)) buttons |= 0x0200;
    if (down('Z')) buttons |= 0x0020;
    if (down('C') || down(17)) buttons |= 0x0040;
    if (down(4)) buttons |= 0x0080;
    if (down(38) || down('1')) buttons |= 1;
    if (down(40) || down('4')) buttons |= 2;
    if (down(37) || down('2')) buttons |= 4;
    if (down(39) || down('3')) buttons |= 8;
    const int x = int(down('D')) - int(down('A')), y = int(down('W')) - int(down('S'));
    const int radius = x && y ? 23170 : 32767;
    output->gamepad.buttons = buttons;
    output->gamepad.thumb_lx = static_cast<int16_t>(x * radius); output->gamepad.thumb_ly = static_cast<int16_t>(y * radius);
    output->gamepad.left_trigger = down(2) ? 255 : 0; output->gamepad.right_trigger = down(1) ? 255 : 0;
    return X_ERROR_SUCCESS;
  }
  rex::X_RESULT GetDeviceCapabilities(DeviceId id, uint32_t, X_INPUT_CAPABILITIES* caps) override {
    if (id != kId || !REXCVAR_GET(aot_keyboard_mouse)) return X_ERROR_DEVICE_NOT_CONNECTED;
    *caps = {}; caps->type = caps->sub_type = 1; caps->gamepad.buttons = 0xF3FF;
    caps->gamepad.left_trigger = caps->gamepad.right_trigger = 255;
    caps->gamepad.thumb_lx = caps->gamepad.thumb_ly = 32767;
    return X_ERROR_SUCCESS;
  }
  rex::X_RESULT SetDeviceVibration(DeviceId id, X_INPUT_VIBRATION*) override { return id == kId ? X_ERROR_SUCCESS : X_ERROR_DEVICE_NOT_CONNECTED; }
  rex::X_RESULT GetDeviceKeystroke(DeviceId id, uint32_t, X_INPUT_KEYSTROKE*) override { return id == kId ? X_ERROR_EMPTY : X_ERROR_DEVICE_NOT_CONNECTED; }
 private:
  void MouseButton(rex::ui::MouseEvent& e, bool pressed) {
    unsigned key = 0;
    if (e.button() == rex::ui::MouseEvent::Button::kLeft) key = 1;
    if (e.button() == rex::ui::MouseEvent::Button::kRight) key = 2;
    if (e.button() == rex::ui::MouseEvent::Button::kMiddle) key = 4;
    std::lock_guard lock(state.mutex);
    if (key && (!pressed || (state.focused && !state.overlay))) { state.keys[key] = pressed; if (pressed) { keyboard_used = true; keyboard_prompts = true; } }
  }
  void Detach() {
    if (!window_) return;
    auto* window = window_;
    window->app_context().CallInUIThreadSynchronous([this, window] {
      Release(window); window->RemoveInputListener(this); window->RemoveListener(this); window_ = nullptr;
      std::lock_guard lock(state.mutex); state.keys.fill(false); state.focused = false;
    });
  }
  static constexpr auto kId = static_cast<DeviceId>(0x414F5402);
  rex::ui::Window* window_ = nullptr;
  std::atomic<uint32_t> packet_{0};
};
class ObservedPad final : public InputDriver {
 public:
  explicit ObservedPad(std::unique_ptr<InputDriver> pad) : InputDriver(nullptr, 0), pad_(std::move(pad)) {
    pad_->set_is_active_callback([this] { return is_active(); });
  }
  X_STATUS Setup() override { return pad_->Setup(); }
  void OnWindowAvailable(rex::ui::Window* window) override { pad_->OnWindowAvailable(window); }
  void EnumerateDevices(std::vector<DeviceInfo>& devices) override {
    pad_->EnumerateDevices(devices);
    if (!keyboard_used && !devices.empty()) keyboard_prompts = false;
  }
  X_RESULT GetDeviceState(DeviceId id, X_INPUT_STATE* output) override {
    const auto result = pad_->GetDeviceState(id, output);
    if (result == X_ERROR_SUCCESS) {
      auto [previous, inserted] = packets_.try_emplace(id, output->packet_number);
      const bool changed = inserted || previous->second != output->packet_number;
      previous->second = output->packet_number;
      const auto& p = output->gamepad;
      if (changed && (p.buttons || p.left_trigger > 30 || p.right_trigger > 30 ||
          std::abs(int(p.thumb_lx)) > 9000 || std::abs(int(p.thumb_ly)) > 9000 ||
          std::abs(int(p.thumb_rx)) > 9000 || std::abs(int(p.thumb_ry)) > 9000)) keyboard_prompts = false;
    }
    return result;
  }
  X_RESULT GetDeviceCapabilities(DeviceId id, uint32_t flags, X_INPUT_CAPABILITIES* caps) override { return pad_->GetDeviceCapabilities(id, flags, caps); }
  X_RESULT SetDeviceVibration(DeviceId id, X_INPUT_VIBRATION* vibration) override { return pad_->SetDeviceVibration(id, vibration); }
  X_RESULT GetDeviceKeystroke(DeviceId id, uint32_t flags, X_INPUT_KEYSTROKE* key) override { return pad_->GetDeviceKeystroke(id, flags, key); }
 private:
  std::unique_ptr<InputDriver> pad_;
  std::unordered_map<DeviceId, uint32_t> packets_;
};
}
std::unique_ptr<rex::input::InputDriver> CreateKeyboardMouseDriver() { return std::make_unique<KeyboardMouse>(); }
std::unique_ptr<rex::system::IInputSystem> CreatePcInput(bool tool_mode) {
  if (tool_mode) return rex::input::CreateDefaultInputSystem(true);
  auto input = std::make_unique<rex::input::InputSystem>(nullptr);
  if (!tool_mode) {
    std::unique_ptr<rex::input::InputDriver> pad;
    if (rex::cvar::GetFlagByName("input_backend") == "xinput") pad = std::make_unique<rex::input::xinput::XinputInputDriver>(nullptr, 0);
    else pad = std::make_unique<rex::input::sdl::SDLInputDriver>(nullptr, 0);
    auto observed = std::make_unique<ObservedPad>(std::move(pad));
    if (observed->Setup() == X_STATUS_SUCCESS) input->AddDriver(std::move(observed));
    input->AddDriver(CreateKeyboardMouseDriver());
  }
  input->SetDeviceAssignment(std::make_unique<rex::input::SlotAssignment>());
  return input;
}
void UpdateMouseCapture(rex::ui::Window* window, bool interactive) {
  const auto now = Now();
  const bool capture = driver_present && REXCVAR_GET(aot_keyboard_mouse) && window->HasFocus() && !interactive && !game_menu && RecentGameplay() && now - fullscreen_movie_seen > 200;
  bool captured;
  { std::lock_guard lock(state.mutex); state.overlay = interactive; if (interactive) state.keys.fill(false); captured = state.captured; }
  if (!capture) { Release(window); return; }
  if (!captured) {
    window->CaptureMouse();
    if (!window->SetRelativeMouseMode(true)) { window->ReleaseMouse(); return; }
    std::lock_guard lock(state.mutex); state.captured = true; state.dx = state.dy = 0; state.sample = {};
    REXLOG_INFO("PC relative mouse capture enabled");
  }
}
void SetKeyboardMenuContext(bool title, bool main, bool menu) {
  title_screen = title; main_menu = main; game_menu = menu;
  if (menu) gameplay_frame = 0;
}
void ObserveMouseGameFrame() { ++game_frame; }
void ObserveMouseGameplay() { gameplay_frame = game_frame.load(); }
void ObserveFullscreenMovie() { gameplay_frame = 0; fullscreen_movie_seen = Now(); }
std::pair<float, float> MouseFrame(uint32_t frame) {
  std::lock_guard lock(state.mutex);
  if (frame != state.frame) {
    state.frame = frame;
    const auto sensitivity = static_cast<float>(REXCVAR_GET(aot_mouse_sensitivity));
    state.sample = {state.dx * sensitivity, state.dy * sensitivity}; state.dx = state.dy = 0;
    if (std::getenv("AOT_TRACE_MOUSE") && (state.sample.first || state.sample.second))
      REXLOG_INFO("PC mouse frame {}: aim_delta={}/{}", frame, state.sample.first, state.sample.second);
  }
  return state.sample;
}
bool KeyboardPrompts() { return REXCVAR_GET(aot_keyboard_mouse) && keyboard_prompts; }
}
