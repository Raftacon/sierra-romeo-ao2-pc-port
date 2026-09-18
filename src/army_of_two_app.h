// army_of_two - ReXGlue Recompiled Project
//
// Customize your app by overriding virtual hooks from rex::ReXApp.

#pragma once

#include <rex/rex_app.h>
#include <cstdlib>
#include <fstream>
#include "scripted_input.h"
#include "pc_features.h"
#include "pc_settings.h"
#include "coop_network_settings.h"
#include "keyboard_mouse.h"
#include "timer_resolution_experiment.h"
#include "network_receive.h"
#include "network_keys.h"
#include "network_campaign.h"
#include <rex/cvar.h>
#include <rex/perf/counter.h>
#include <rex/ui/keybinds.h>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
void MountTutorialText(rex::Runtime*, const std::filesystem::path&, const std::filesystem::path&);
}

class ArmyOfTwoApp : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<ArmyOfTwoApp>(new ArmyOfTwoApp(ctx, "army_of_two",
        PPCImageConfig));
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    if (const char* profile = std::getenv("AOT_PROFILE"); profile && std::string_view(profile) == "1")
      rex::perf::Profiler::Startup();
    aot::LoadPcSettings(user_data_root());
    aot::LoadCoopNetworkSettings(user_data_root());
    timer_resolution_.StartFromEnvironment();
    if (config.gpu_plugin.empty()) {
#ifdef AOT_SPATIAL_GPU
      config.gpu_plugin = "spatial";
#else
      config.gpu_plugin = "xenos";
#endif
    }
    const char* path = std::getenv("AOT_INPUT_SCRIPT");
    const char* live = std::getenv("AOT_INPUT_STATE");
    if (path || live) {
      config.input_factory = [script = std::string(path ? path : ""),
                              state = std::string(live ? live : "")](bool) {
        return aot::CreateScriptedInput(script, state);
      };
    } else config.input_factory = aot::CreatePcInput;
  }

  void OnPostLoadXexImage() override {
    // Opt-in local analysis snapshot, taken before game threads execute.
    // Never part of the distributable project.
    if (const char* path = std::getenv("AOT_DUMP_IMAGE")) {
      std::ofstream output(path, std::ios::binary);
      output.write(reinterpret_cast<const char*>(
          runtime()->memory()->TranslateVirtual(REX_IMAGE_BASE)), REX_IMAGE_SIZE);
      if (!output) throw std::runtime_error("Failed to write analysis image");
      output.close();
      if (const char* setup = std::getenv("AOT_SETUP_ONLY"); setup && std::string_view(setup) == "1")
        std::exit(0);  // Local installer resource extraction; no guest execution or save access.
    }
  }

  void OnPostSetup() override {
    aot::InitializeCampaignNetwork(window()->GetNativeWindowHandle());
    aot::ResetNetworkKeys();
    aot::InitializeNetworkReceive();
    aot::MountTutorialText(runtime(), game_data_root(), user_data_root());
    aot::InitializePcFeatures(runtime(), game_data_root());
    SetGuestFrameStats(aot::GetFrameStats);
    if (std::getenv("AOT_OPEN_CONSOLE")) {
      rex::ui::KeyEvent console_key(window(), rex::ui::VirtualKey::kOem3, 1, false, false, false, false, false);
      rex::ui::ProcessKeyEvent(console_key);
    }
    window()->SetTitle("Sierra Romeo");
#ifdef _WIN32
    auto module = GetModuleHandleW(nullptr);
    auto hwnd = static_cast<HWND>(window()->GetNativeWindowHandle());
    for (auto [kind, size] : {std::pair{ICON_SMALL, 16}, std::pair{ICON_BIG, 32}}) {
      auto icon = LoadImageW(module, MAKEINTRESOURCEW(101), IMAGE_ICON, size, size, LR_SHARED);
      if (icon) SendMessageW(hwnd, WM_SETICON, kind, reinterpret_cast<LPARAM>(icon));
    }
#endif
    if (rex::cvar::GetFlagSource("readback_resolve") == rex::cvar::Source::kDefault)
      rex::cvar::SetFlagByName("readback_resolve", "full");
  }

  void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {
    // Attribution only: persistent empty ImGui dialogs keep host UI repainting.
    // Never enable this in normal play; it suppresses PC controls and prompts.
    if (const char* value = std::getenv("AOT_PERF_NO_PC_OVERLAYS"); value && std::string_view(value)=="1")
      return;
    aot::CreateCursorPolicy(drawer, window());
    aot::CreatePcSettingsOverlay(drawer, immediate_drawer(), window(), user_data_root(), game_data_root().parent_path() / "artifacts/branding");
    aot::CreateMenuOverlay(drawer, immediate_drawer(), window(), game_data_root().parent_path() / "artifacts/branding");
    aot::CreateCoopConnectionOverlay(drawer, immediate_drawer(), game_data_root().parent_path() / "artifacts/branding");
    aot::CreateSkipOverlay(drawer, immediate_drawer(), game_data_root().parent_path() / "artifacts/branding");
  }

  void OnWindowFocusChanged(bool focused) override {
    // A minimized window may stop drawing. Restore visibility immediately
    // when leaving the game, without waiting for the next overlay frame.
    if (!focused && window())
      window()->SetCursorVisibility(rex::ui::Window::CursorVisibility::kVisible);
  }

  bool OnWindowCloseRequested() override {
    aot::ShutdownNetworkReceive();
    // ReXApp's normal window-close path hard-exits before OnShutdown.
    timer_resolution_.Reset();
    return true;
  }
  void OnShutdown() override { aot::ShutdownNetworkReceive(); timer_resolution_.Reset(); }

  // Override virtual hooks for customization:
  // void OnPostInitLogging() override {}
  // void OnPreSetup(rex::RuntimeConfig& config) override {}
  // void OnLoadXexImage(std::string& xex_image) override {}
  // void OnPostLoadXexImage() override {}
  // void OnPostSetup() override {}
  // void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {}
  // std::unique_ptr<rex::ui::ImGuiDialog> CreateAchievementsOverlay() override;
  // std::unique_ptr<rex::ui::AchievementNotificationDialog>
  // CreateAchievementNotificationDialog() override;
  // void OnShutdown() override {}
  // void OnConfigurePaths(rex::PathConfig& paths) override {}
 private:
  aot::TimerResolutionExperiment timer_resolution_;
};
