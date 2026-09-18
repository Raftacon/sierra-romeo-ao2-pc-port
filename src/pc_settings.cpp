#include "pc_settings.h"
#include "keyboard_mouse.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/runtime.h>
#include <rex/system/interfaces/graphics.h>
#include <rex/ui/window.h>
#include <rex/ui/window_listener.h>
#include <imgui.h>
#include <atomic>
#include <array>
#include <algorithm>
#include <chrono>
#include <deque>
#include <fstream>
#include <mutex>
#include <memory>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
namespace {
constexpr char kHostTearingFlag[] = "d3d12_allow_variable_refresh_rate_and_tearing";
std::atomic<bool> open_requested{false}, begin_requested{false}, visible{false}, close_requested{false};
std::mutex input_mutex;
std::deque<uint16_t> inputs;
uint16_t previous_buttons = 0;
uint16_t blocked_buttons = 0;
std::chrono::steady_clock::time_point repeat_at;
constexpr std::array<std::pair<int, int>, 5> resolutions{{{1280,720},{1600,900},{1920,1080},{2560,1440},{3840,2160}}};
constexpr int kSettingRows = 9;
struct Settings {
  bool fullscreen = false, vsync = true, subtitle = true, spatial = true;
  bool hud_contrast = false;
  int width = 1920, height = 1080, scale = 1, fps = 60, opacity = 60;
  std::string anti_aliasing = "fxaa";
};
Settings CurrentSettings() {
  auto number = [](const char* name, int fallback) {
    try { return std::stoi(rex::cvar::GetFlagByName(name)); } catch (...) { return fallback; }
  };
  Settings s;
  s.fullscreen = rex::cvar::GetFlagByName("fullscreen") == "true";
  s.vsync = rex::cvar::GetFlagByName("vsync") == "true";
  s.subtitle = rex::cvar::GetFlagByName("aot_subtitle_background") == "true";
  s.hud_contrast = rex::cvar::GetFlagByName("aot_hud_text_contrast") == "true";
  s.spatial = rex::cvar::GetFlagByName("aot_spatial_upscale") == "true";
  s.width = number("window_width", 1920); s.height = number("window_height", 1080);
  if (s.width <= 0 || s.height <= 0) { s.width = 1280; s.height = 720; }
  s.scale = number("resolution_scale", 1); s.fps = number("aot_fps", 60);
  s.anti_aliasing = rex::cvar::GetFlagByName("swap_post_effect");
  if (s.anti_aliasing != "none" && s.anti_aliasing != "fxaa_extreme") s.anti_aliasing = "fxaa";
  try { s.opacity = static_cast<int>(std::stod(rex::cvar::GetFlagByName("aot_subtitle_background_opacity")) * 100 + .5); } catch (...) {}
  return s;
}
std::string Serialize(const Settings& s) {
  std::ostringstream out;
  out << "# Sierra Romeo PC display preferences\n" << std::boolalpha
      << "fullscreen = " << s.fullscreen << "\nwindow_width = " << s.width
      << "\nwindow_height = " << s.height << "\nresolution_scale = " << s.scale
      << "\naot_spatial_upscale = " << s.spatial
      << "\nswap_post_effect = \"" << s.anti_aliasing << "\""
      << "\nvsync = " << s.vsync << "\naot_fps = " << s.fps
      << "\naot_subtitle_background = " << s.subtitle
      << "\naot_hud_text_contrast = " << s.hud_contrast
      << "\naot_subtitle_background_opacity = " << s.opacity / 100.0 << '\n';
  return out.str();
}
bool Save(const std::filesystem::path& file, const Settings& s) {
  std::error_code ec;
  std::filesystem::create_directories(file.parent_path(), ec);
  if (ec) return false;
  auto temporary = file; temporary += ".tmp";
  { std::ofstream out(temporary, std::ios::binary | std::ios::trunc); out << Serialize(s); out.flush(); if (!out) return false; }
#ifdef _WIN32
  return MoveFileExW(temporary.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
#else
  std::filesystem::rename(temporary, file, ec); return !ec;
#endif
}
class PcSettingsOverlay final : public rex::ui::ImGuiDialog, public rex::ui::WindowListener {
 public:
  PcSettingsOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
      rex::ui::Window* window, std::filesystem::path file, const std::filesystem::path& branding)
      : ImGuiDialog(drawer), window_(window), file_(std::move(file)) {
    std::ifstream font(branding / "pc-font.rgba", std::ios::binary);
    uint32_t header[4]{}; font.read(reinterpret_cast<char*>(header), sizeof(header));
    if (font && header[0] && header[0] <= 4096 && header[1] && header[1] <= 4096 && header[2] == 256) {
      font_width_ = header[0]; font_height_ = header[1];
      font.read(reinterpret_cast<char*>(glyphs_.data()), sizeof(glyphs_));
      std::vector<uint8_t> pixels(font_width_ * font_height_ * 4);
      font.read(reinterpret_cast<char*>(pixels.data()), pixels.size());
      if (font) font_ = immediate->CreateTexture(font_width_, font_height_, rex::ui::ImmediateTextureFilter::kLinear, false, pixels.data());
    }
    for (int i = 0; i < 2; ++i) {
    std::ifstream controls(branding / (i ? "pc-settings-controls-keyboard.rgba" : "pc-settings-controls.rgba"), std::ios::binary);
    controls.read(reinterpret_cast<char*>(header), sizeof(header));
    if (controls && header[0] && header[0] <= 1024 && header[1] && header[1] <= 256) {
      controls_width_[i] = header[0]; controls_height_[i] = header[1];
      std::vector<uint8_t> pixels(header[0] * header[1] * 4);
      controls.read(reinterpret_cast<char*>(pixels.data()), pixels.size());
      if (controls) controls_[i] = immediate->CreateTexture(header[0], header[1], rex::ui::ImmediateTextureFilter::kLinear, false, pixels.data());
    }
    }
    initial_scale_ = CurrentSettings().scale;
    initial_anti_aliasing_ = CurrentSettings().anti_aliasing;
    window_->AddListener(this);
  }
  ~PcSettingsOverlay() override {
    surface_lifetime_.reset();
    if (window_) window_->RemoveListener(this);
  }
  void OnClosing(rex::ui::UIEvent&) override {
    surface_lifetime_.reset();
    window_->RemoveListener(this);
    window_ = nullptr;
  }
  void OnDraw(ImGuiIO& io) override {
    // Track the physical client size, including fullscreen/DPI/manual resize.
    // This is one atomic flag value so the GPU never sees mismatched dimensions.
    if (window_ && rex::cvar::GetFlagInfo("aot_upscale_output_size")) {
      const auto width = window_->GetActualPhysicalWidth(), height = window_->GetActualPhysicalHeight();
      const auto size = std::to_string(width) + "x" + std::to_string(height);
      if (size != output_size_ && width > 0 && height > 0) {
        rex::cvar::SetFlagByName("aot_upscale_output_size", size);
        output_size_ = size;
      }
    }
    if (begin_requested.exchange(false)) { draft_ = CurrentSettings(); selected_ = 0; status_.clear(); }
    if (!visible) return;
    std::deque<uint16_t> events;
    { std::lock_guard lock(input_mutex); events.swap(inputs); }
    for (auto buttons : events) {
      if (buttons & 0x2000) { close_requested = true; break; }
      if (buttons & 0x4000) { draft_ = Settings{}; draft_.spatial = rex::cvar::GetFlagInfo("aot_spatial_upscale") != nullptr; status_.clear(); }
      if (buttons & 0x0001) selected_ = (selected_ + kSettingRows - 1) % kSettingRows;
      if (buttons & 0x0002) selected_ = (selected_ + 1) % kSettingRows;
      if (buttons & 0x000C) Adjust((buttons & 0x0008) ? 1 : -1);
      if (buttons & 0x1000) {
        if (Apply()) { close_requested = true; break; }
        status_ = "Could not save PC settings. Try again.";
      }
    }
    auto* draw = ImGui::GetForegroundDrawList();
    const float scale = io.DisplaySize.y / 720.f, cx = io.DisplaySize.x / 2;
    const char* labels[] = {"Display mode", "Window resolution", "Rendering (restart)", "Frame limit", "VSync", "Subtitle background", "Background opacity", "Anti-aliasing (restart)", "Gameplay text contrast"};
    const std::string values[] = {draft_.fullscreen ? "Borderless fullscreen" : "Windowed",
        draft_.fullscreen ? "Desktop resolution" : std::to_string(draft_.width) + " x " + std::to_string(draft_.height),
        draft_.scale == 1 ? (draft_.spatial ? "1x + FSR" : "1x") : std::to_string(draft_.scale) + "x supersampling",
        std::to_string(draft_.fps) + " FPS", draft_.vsync ? "On" : "Off",
        draft_.subtitle ? "On" : "Off", std::to_string(draft_.opacity) + "%",
        draft_.anti_aliasing == "none" ? "Off" : draft_.anti_aliasing == "fxaa_extreme" ? "Strong FXAA" : "FXAA",
        draft_.hud_contrast ? "On" : "Off"};
    static_assert(std::size(labels) == kSettingRows && std::size(values) == kSettingRows);
    for (int row = 0; row < kSettingRows; ++row) {
      // The native confirmation panel's body starts below y=260. Keep all
      // nine rows between its header and the help/footer area at y=467.
      const float y = (278 + row * 20) * scale;
      if (row == selected_) draw->AddRectFilled(ImVec2(cx - 277 * scale, y - 2 * scale), ImVec2(cx + 278 * scale, y + 17 * scale), IM_COL32(185, 190, 190, 45));
      const auto color = row == selected_ ? IM_COL32_WHITE : IM_COL32(195, 198, 198, 255);
      Text(draw, labels[row], cx - 265 * scale, y, .74f * scale, color);
      const auto value = std::string(row == selected_ ? "< " : "") + values[row] + (row == selected_ ? " >" : "");
      Text(draw, value, cx + 265 * scale - Width(value, .74f * scale), y, .74f * scale, color);
    }
    std::string help = status_;
    if (help.empty()) {
      if (selected_ == 1 && draft_.fullscreen) help = "Fullscreen uses your desktop resolution.";
      else if (selected_ == 8) help = "Light HUD text with shadows and fitted dark backing.";
      else if (draft_.anti_aliasing != initial_anti_aliasing_) help = "Anti-aliasing will change after restarting the game.";
      else if (selected_ == 7) help = "Strong FXAA smooths more edges but softens fine detail.";
      else if (draft_.scale != initial_scale_) help = "Render scale will change after restarting the game.";
      else if (selected_ == 2) help = draft_.scale > 1 ? "Supersampling increases GPU cost substantially." : draft_.spatial ? "FSR sharpens upscaled output while rendering at 1x." : "Original rendering with smooth output scaling.";
      else help = KeyboardPrompts() ? "Arrow keys: select and adjust. Changes apply when accepted." : "D-pad: select and adjust. Changes apply when accepted.";
    }
    Text(draw, help, cx - Width(help, .60f * scale) / 2, 467 * scale, .60f * scale, IM_COL32(205, 208, 208, 255));
    const int mode = KeyboardPrompts() ? 1 : 0;
    if (controls_[mode]) {
      const float size = .80f * scale, x = cx - controls_width_[mode] * size / 2;
      draw->AddImage(reinterpret_cast<ImTextureID>(controls_[mode].get()), ImVec2(x, 485 * scale), ImVec2(x + controls_width_[mode] * size, 485 * scale + controls_height_[mode] * size));
    }
  }
 private:
  void Adjust(int direction) {
    status_.clear();
    switch (selected_) {
      case 0: draft_.fullscreen = !draft_.fullscreen; break;
      case 1: {
        if (draft_.fullscreen) break;
        size_t index = 0;
        for (size_t i = 0; i < resolutions.size(); ++i) if (resolutions[i] == std::pair{draft_.width, draft_.height}) index = i;
        index = (index + resolutions.size() + direction) % resolutions.size();
        draft_.width = resolutions[index].first; draft_.height = resolutions[index].second; break;
      }
      case 2: {
        const bool available = rex::cvar::GetFlagInfo("aot_spatial_upscale") != nullptr;
        int index = draft_.scale == 1 ? (available && draft_.spatial ? 1 : 0) : draft_.scale - 1 + int(available);
        index = (index + direction + 3 + int(available)) % (3 + int(available));
        draft_.spatial = available && index == 1;
        draft_.scale = index <= int(available) ? 1 : index + 1 - int(available);
        break;
      }
      case 3: draft_.fps = draft_.fps == 60 ? 30 : 60; break;
      case 4: draft_.vsync = !draft_.vsync; break;
      case 5: draft_.subtitle = !draft_.subtitle; break;
      case 6: draft_.opacity = std::clamp(draft_.opacity + direction * 10, 0, 100); break;
      case 7: {
        constexpr const char* modes[] = {"none", "fxaa", "fxaa_extreme"};
        const int index = draft_.anti_aliasing == "none" ? 0 : draft_.anti_aliasing == "fxaa_extreme" ? 2 : 1;
        draft_.anti_aliasing = modes[(index + direction + 3) % 3];
        break;
      }
      case 8: draft_.hud_contrast = !draft_.hud_contrast; break;
    }
  }
  bool Apply() {
    if (!window_) return false;
    if (!Save(file_, draft_)) { REXLOG_WARN("Could not save PC display preferences"); return false; }
    auto set = [](const char* key, std::string value) { return rex::cvar::SetFlagByName(key, value); };
    set("window_width", std::to_string(draft_.width)); set("window_height", std::to_string(draft_.height));
    set("resolution_scale", std::to_string(draft_.scale)); set("aot_fps", std::to_string(draft_.fps));
    if (rex::cvar::GetFlagInfo("aot_spatial_upscale")) set("aot_spatial_upscale", draft_.spatial ? "true" : "false");
    set("swap_post_effect", draft_.anti_aliasing);
    set("vsync", draft_.vsync ? "true" : "false");
    const bool allow_tearing = !draft_.vsync;
    const bool recreate_surface = (rex::cvar::GetFlagByName(kHostTearingFlag) == "true") != allow_tearing;
    set(kHostTearingFlag, allow_tearing ? "true" : "false");
    set("aot_subtitle_background", draft_.subtitle ? "true" : "false");
    set("aot_hud_text_contrast", draft_.hud_contrast ? "true" : "false");
    set("aot_subtitle_background_opacity", std::to_string(draft_.opacity / 100.0));
    set("fullscreen", draft_.fullscreen ? "true" : "false");
#ifdef _WIN32
    if (!draft_.fullscreen) {
      auto hwnd = static_cast<HWND>(window_->GetNativeWindowHandle());
      RECT rectangle{0, 0, static_cast<LONG>(window_->SizeToPhysical(draft_.width)), static_cast<LONG>(window_->SizeToPhysical(draft_.height))};
      AdjustWindowRectExForDpi(&rectangle, static_cast<DWORD>(GetWindowLongPtrW(hwnd, GWL_STYLE)), false,
          static_cast<DWORD>(GetWindowLongPtrW(hwnd, GWL_EXSTYLE)), window_->GetDpi());
      SetWindowPos(hwnd, nullptr, 0, 0, rectangle.right - rectangle.left, rectangle.bottom - rectangle.top,
          SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOZORDER);
    }
#endif
    if (recreate_surface) {
      // DXGI's tearing creation flag cannot be changed by ResizeBuffers.
      // The SDK forbids reconnecting a surface from inside UI drawing, so
      // defer until drawing completes. The weak token also cancels this work
      // if the dialog or its window closes before the callback runs.
      const std::weak_ptr<int> lifetime = surface_lifetime_;
      window_->app_context().CallInUIThreadDeferred([window = window_, lifetime] {
        if (lifetime.expired()) return;
        auto* runtime = rex::Runtime::instance();
        auto* graphics = runtime ? runtime->graphics_system() : nullptr;
        auto* presenter = graphics ? graphics->presenter() : nullptr;
        if (!presenter) return;
        window->SetPresenter(nullptr);
        window->SetPresenter(presenter);
        REXLOG_INFO("PC VSync presentation refreshed: allow_tearing={}",
            rex::cvar::GetFlagByName(kHostTearingFlag));
      });
    }
    REXLOG_INFO("PC display preferences applied and saved: fullscreen={}, window={}x{}, scale={}, fps={}, vsync={}, spatial_fsr={}, anti_aliasing={}, hud_text_contrast={}", draft_.fullscreen, draft_.width, draft_.height, draft_.scale, draft_.fps, draft_.vsync, draft_.spatial, draft_.anti_aliasing, draft_.hud_contrast);
    return true;
  }
  float Width(std::string_view text, float scale) const {
    float width = 0;
    for (unsigned char c : text) width += (c == ' ' ? 7 : glyphs_[c].w + 1) * scale;
    return width;
  }
  void Text(ImDrawList* draw, std::string_view text, float x, float y, float scale, ImU32 color) {
    if (!font_) { draw->AddText(ImVec2(x, y), color, text.data(), text.data() + text.size()); return; }
    for (unsigned char c : text) {
      const auto& g = glyphs_[c];
      if (c == ' ') { x += 7 * scale; continue; }
      if (g.w && g.h) draw->AddImage(reinterpret_cast<ImTextureID>(font_.get()), ImVec2(x, y), ImVec2(x + g.w * scale, y + g.h * scale),
          ImVec2(float(g.u) / font_width_, float(g.v) / font_height_), ImVec2(float(g.u + g.w) / font_width_, float(g.v + g.h) / font_height_), color);
      x += (g.w + 1) * scale;
    }
  }
  struct Glyph { uint32_t u, v, w, h; };
  std::array<Glyph, 256> glyphs_{};
  std::unique_ptr<rex::ui::ImmediateTexture> font_, controls_[2];
  uint32_t font_width_ = 0, font_height_ = 0, controls_width_[2]{}, controls_height_[2]{};
  rex::ui::Window* window_;
  std::shared_ptr<int> surface_lifetime_ = std::make_shared<int>(0);
  std::filesystem::path file_;
  Settings draft_;
  int selected_ = 0, initial_scale_ = 1;
  std::string status_, initial_anti_aliasing_;
  std::string output_size_;
};
}
void LoadPcSettings(const std::filesystem::path& user_data) {
  rex::cvar::LoadConfig(user_data / "pc-settings.toml");
}
void CreatePcSettingsOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
    rex::ui::Window* window, const std::filesystem::path& user_data, const std::filesystem::path& branding) {
  // The GPU plugin has registered vsync by this point, but ReXApp has not
  // attached the presenter's window surface yet. OnPreSetup is too early to
  // read late-registered GPU flags, even if their config was already loaded.
  // Preserve explicit advanced host overrides until PC Display is applied.
  if (rex::cvar::GetFlagSource(kHostTearingFlag) == rex::cvar::Source::kDefault)
    rex::cvar::SetFlagByName(kHostTearingFlag,
        rex::cvar::GetFlagByName("vsync") == "true" ? "false" : "true");
  REXLOG_INFO("PC initial presentation: vsync={}, allow_tearing={}, anti_aliasing={}",
      rex::cvar::GetFlagByName("vsync"), rex::cvar::GetFlagByName(kHostTearingFlag),
      rex::cvar::GetFlagByName("swap_post_effect"));
  new PcSettingsOverlay(drawer, immediate, window, user_data / "pc-settings.toml", branding);
}
void RequestPcSettings() { open_requested = true; }
bool TakePcSettingsOpenRequest() { return open_requested.exchange(false); }
void BeginPcSettings() { begin_requested = true; visible = true; }
void EndPcSettings() {
  std::lock_guard lock(input_mutex);
  blocked_buttons |= previous_buttons;
  inputs.clear(); visible = false;
}
bool TakePcSettingsCloseRequest() { return close_requested.exchange(false); }
void FilterPcSettingsInput(rex::input::X_INPUT_STATE& state) {
  std::lock_guard lock(input_mutex);
  uint16_t buttons = state.gamepad.buttons;
  if (state.gamepad.thumb_ly > 18000) buttons |= 1;
  if (state.gamepad.thumb_ly < -18000) buttons |= 2;
  if (state.gamepad.thumb_lx < -18000) buttons |= 4;
  if (state.gamepad.thumb_lx > 18000) buttons |= 8;
  uint16_t pressed = buttons & ~previous_buttons;
  const auto now = std::chrono::steady_clock::now();
  if ((buttons & 15) != (previous_buttons & 15)) repeat_at = now + std::chrono::milliseconds(400);
  else if ((buttons & 15) && now >= repeat_at) { pressed |= buttons & 15; repeat_at = now + std::chrono::milliseconds(130); }
  previous_buttons = buttons;
  blocked_buttons &= buttons;
  if (!visible) { state.gamepad.buttons = state.gamepad.buttons & ~blocked_buttons; return; }
  if (pressed && inputs.size() < 32) inputs.push_back(pressed);
  state.gamepad = {};
}
}
