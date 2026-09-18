#pragma once
#include "coop_connection.h"
#include <filesystem>
#include <rex/input/input.h>
#include <rex/ui/imgui_dialog.h>
namespace aot {
void CreateCoopConnectionOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
    const std::filesystem::path& branding);
void RequestCoopConnection(bool private_room);
bool TakeCoopConnectionOpenRequest();
void BeginCoopConnection();
bool TakeCoopConnectionCloseRequest();
void EndCoopConnection();
std::optional<CoopConnectionResult> TakeCoopConnectionResult();
void SetCoopInvitation(std::string address, uint16_t port, std::string invite);
void ClearCoopInvitation();
void SetCoopRelayInvitation(std::string invitation,std::string notice={});
void SetCoopDirectoryNotice(std::string notice);
void FilterCoopConnectionInput(rex::input::X_INPUT_STATE& state);
}
