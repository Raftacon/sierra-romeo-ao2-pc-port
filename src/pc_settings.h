#pragma once
#include <filesystem>
#include <rex/input/input.h>
#include <rex/ui/imgui_dialog.h>

namespace aot {
void LoadPcSettings(const std::filesystem::path& user_data);
void CreatePcSettingsOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
    rex::ui::Window* window, const std::filesystem::path& user_data, const std::filesystem::path& branding);
void RequestPcSettings();
bool TakePcSettingsOpenRequest();
void BeginPcSettings();
void EndPcSettings();
bool TakePcSettingsCloseRequest();
void FilterPcSettingsInput(rex::input::X_INPUT_STATE& state);
}
