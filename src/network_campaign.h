#pragma once
#include <string>
#include "coop_lobby.h"
struct PPCContext;
namespace aot {
void InitializeCampaignNetwork(void* native_window);
// Isolated UI fixture only; never a backend-ready or peer-connected signal.
bool ConsumeCampaignSetupDiagnostic();
std::string PollCampaignService();
std::string ConsumeCampaignNotice();
std::string CampaignConnectionStatus();
const CoopLobbySnapshot* CampaignServiceSnapshot();
bool SubmitCampaignLoadout(uint32_t plasma, uint8_t* base);
bool ResetCampaignLoadout(PPCContext& context, uint32_t plasma, uint8_t* base);
bool AcknowledgeCampaignImport(uint32_t plasma, uint8_t* base);
bool BeginCampaignLoad(PPCContext& context, uint8_t* base);
bool PrepareCampaignCheckpoint(PPCContext& context, uint8_t* base);
bool ImportCampaignCheckpoint(PPCContext& context, uint8_t* base);
}
