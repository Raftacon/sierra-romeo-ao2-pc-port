#pragma once
#include <rex/runtime.h>
#include <rex/ui/overlay/debug_overlay.h>
#include <rex/ui/imgui_dialog.h>
#include "coop_connection_ui.h"
namespace aot {
void InitializePcFeatures(rex::Runtime* runtime, const std::filesystem::path& game_data);
void CreateCursorPolicy(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window);
void CreateMenuOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
    rex::ui::Window* window, const std::filesystem::path& branding);
rex::ui::FrameStats GetFrameStats();
rex::ui::ImGuiDialog* CreateSkipOverlay(rex::ui::ImGuiDrawer* drawer,
    rex::ui::ImmediateDrawer* immediate, const std::filesystem::path& branding);
}
