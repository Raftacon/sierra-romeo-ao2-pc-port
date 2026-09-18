#include "pc_features.h"
#include "hud_ink_bounds.h"
#include "dof_uv.h"
#include "skip_policy.h"
#include "menu_policy.h"
#include "port_credits.h"
#include "pc_settings.h"
#include "keyboard_mouse.h"
#include "frame_diagnostics.h"
#include "wait_diagnostics.h"
#include "rhi_resolve_diagnostics.h"
#include "network_campaign.h"
#include "keyboard_glyphs.generated.h"
#include <rex/cvar.h>
#include <rex/dbg.h>
#include <rex/input/input_system.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/system/thread_state.h>
#include <chrono>
#include <cmath>
#include <mutex>
#include <thread>
#include <fstream>
#include <cstdlib>
#include <rex/hook.h>
#include <rex/system/function_dispatcher.h>
#include <deque>
#include <atomic>
#include <array>
#include <unordered_set>
#include <algorithm>
#include <vector>
#include <imgui.h>
#ifdef _WIN32
#include <windows.h>
#endif

REX_EXTERN(sub_8262B8E0);
REX_EXTERN(sub_82305110);
REX_EXTERN(sub_82305190);
REX_EXTERN(sub_824B5E18);
REX_EXTERN(sub_824340D0);
REX_EXTERN(sub_824621B8);
REX_EXTERN(sub_82642158);
REX_EXTERN(sub_82641CB8);
REX_EXTERN(sub_8260C280);
REX_EXTERN(__imp__XamInputGetState);
REX_EXTERN(sub_824552B0);
REX_EXTERN(sub_824716C8);
REX_EXTERN(sub_828B0078);
REX_EXTERN(sub_8237E6E0);
REX_EXTERN(sub_82336AE8);
REX_EXTERN(sub_829D9710);
REX_EXTERN(sub_829D5278);
REX_EXTERN(sub_829D5330);
REX_EXTERN(sub_824C42F0);
REX_EXTERN(__imp__sub_829D7880);
REX_EXTERN(__imp__sub_82A584F0);
REX_EXTERN(__imp__sub_8250C4E0);
REX_EXTERN(__imp__sub_8254B320);
REX_EXTERN(__imp__sub_8254B388);
REX_EXTERN(__imp__sub_8250A830);

REXCVAR_DEFINE_BOOL(aot_preserve_scene_before_shadows, true, "Sierra Romeo",
    "Preserve scene color before the later shadow-rendering phase");

REX_HOOK_RAW(sub_829D7880) {
  aot::ObserveRhiResolve(1, ctx, base);
  __imp__sub_829D7880(ctx, base);
}
REX_HOOK_RAW(sub_82A584F0) {
  aot::ObserveRhiResolve(2, ctx, base);
  __imp__sub_82A584F0(ctx, base);
}
REX_HOOK_RAW(sub_8250C4E0) {
  aot::ObserveRhiResolve(3, ctx, base);
  // The observed phase 2 starts with a clean flag even though the preceding
  // phase's color remains in EDRAM. Use the engine's own
  // save/restore path before its shadow mask can replace that color.
  if (REXCVAR_GET(aot_preserve_scene_before_shadows) &&
      ctx.lr == 0x82565564 && ctx.r4.u32 == 2 && ctx.r5.u32 == 0) {
    ctx.r5.u64 = 1;
    aot::ObserveRhiResolve(10, ctx, base);
  }
  __imp__sub_8250C4E0(ctx, base);
  aot::ObserveRhiResolve(4, ctx, base);
}
REX_HOOK_RAW(sub_8254B320) {
  aot::ObserveRhiResolve(5, ctx, base);
  __imp__sub_8254B320(ctx, base);
}
REX_HOOK_RAW(sub_8254B388) {
  aot::ObserveRhiResolve(7, ctx, base);
  __imp__sub_8254B388(ctx, base);
}
REX_HOOK_RAW(sub_8250A830) {
  aot::ObserveRhiResolve(8, ctx, base);
  __imp__sub_8250A830(ctx, base);
  aot::ObserveRhiResolve(9, ctx, base);
}

REXCVAR_DEFINE_INT32(aot_fps, 60, "Sierra Romeo", "Game frame cap: 30 preserves original limit; 60 enables native 60 FPS patches")
    .range(30, 60);
REXCVAR_DEFINE_BOOL(aot_precise_frame_pacing, true, "Sierra Romeo",
    "Use a high-resolution Windows timer for the 60 FPS frame deadline");
REXCVAR_DEFINE_BOOL(aot_immediate_guest_present, true, "Sierra Romeo",
    "Use immediate guest presentation at 60 FPS; retain the PC frame cap and display VSync policy");
REXCVAR_DEFINE_BOOL(aot_gpu_poll_backoff, false, "Sierra Romeo",
    "Experimental short backoff during repeated original GPU-progress polling");
REXCVAR_DEFINE_BOOL(aot_dof_uv_correction, true, "Sierra Romeo",
    "Correct padded source coordinates for the retail DOF blur and final blend");
REXCVAR_DEFINE_BOOL(aot_disable_blur, false, "Sierra Romeo", "Disable Gaussian blur for sharper upscaled output; changes bloom and depth of field");
REXCVAR_DEFINE_BOOL(aot_keyboard_coop_prompt, true, "Sierra Romeo", "Show the keyboard F texture in co-op cancel prompts when using keyboard input");
REXCVAR_DEFINE_BOOL(aot_local_storage, true, "Sierra Romeo", "Preselect local save device and suppress the game's initial storage-selection prompt");
REXCVAR_DEFINE_BOOL(aot_subtitle_shadow, true, "Sierra Romeo", "Add a semi-opaque shadow behind the original subtitles");
REXCVAR_DEFINE_BOOL(aot_subtitle_background, true, "Sierra Romeo", "Add a translucent dark box behind each subtitle line");
REXCVAR_DEFINE_DOUBLE(aot_subtitle_background_opacity, .6, "Sierra Romeo", "Subtitle box opacity")
    .range(0.0, 1.0);
REXCVAR_DEFINE_BOOL(aot_hud_text_contrast, false, "Sierra Romeo",
    "Add fitted backing and letter shadow to gameplay captions and objectives");

namespace {
rex::Runtime* runtime = nullptr;
std::mutex stats_mutex;
rex::ui::FrameStats stats;
using Clock = std::chrono::steady_clock;
Clock::time_point last_frame{}, next_frame{}, pc_features_started{};
void WaitForFrameDeadline(Clock::time_point deadline) {
#ifdef _WIN32
  if (REXCVAR_GET(aot_precise_frame_pacing)) {
    struct FrameTimer {
      HANDLE handle = CreateWaitableTimerExW(nullptr, nullptr,
          CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE);
      FrameTimer() { REXLOG_INFO("High-resolution frame timer: {}", handle ? "available" : "unavailable; using standard sleep"); }
      ~FrameTimer() { if (handle) CloseHandle(handle); }
    };
    static FrameTimer timer;
    if (timer.handle) {
      using TimerTicks = std::chrono::duration<LONGLONG, std::ratio<1, 10000000>>;
      const auto ticks = std::chrono::duration_cast<TimerTicks>(deadline - Clock::now()).count();
      if (ticks <= 0) return;
      LARGE_INTEGER due;
      due.QuadPart = -ticks;  // Relative duration in 100 ns units.
      if (SetWaitableTimer(timer.handle, &due, 0, nullptr, nullptr, FALSE) &&
          WaitForSingleObject(timer.handle, INFINITE) == WAIT_OBJECT_0) return;
    }
  }
#endif
  std::this_thread::sleep_until(deadline);
}
std::ofstream timing;
std::ofstream phase_timing;
std::mutex command_mutex;
std::deque<std::string> commands;
std::atomic<uint32_t> console_actor{0};
std::mutex movie_mutex;
std::atomic<int64_t> movie_seen_ms{0};
std::atomic<int64_t> movie_input_ms{0};
std::atomic<float> skip_progress{0};
std::atomic<int64_t> menu_seen_ms{0};
std::atomic<uint32_t> checkpoint_root_image{0};
std::atomic<bool> main_menu_active{false}, frontend_exit_active{false}, exit_open_requested{false}, exit_requested{false};
std::atomic<bool> options_active{false};
std::atomic<uint32_t> extras_scene{0}, unlock_open_requested{0};
std::atomic<uint64_t> controller_font_name{0};
std::atomic<uint64_t> hud_readability_font_name{0};
std::atomic<uint32_t> hud_readability_font{0};
std::array<std::atomic<uint64_t>, 3> dark_hud_font_names{};
std::mutex menu_input_mutex;
aot::ExitMenuPolicy exit_policy;
int64_t Milliseconds() { return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count(); }
bool Readable(rex::memory::Memory* memory, uint32_t address, size_t size) {
  if (!address || uint64_t(address) + size > 0x100000000ull) return false;
#ifdef _WIN32
  MEMORY_BASIC_INFORMATION info{};
  auto* pointer = memory->TranslateVirtual<uint8_t*>(address);
  return VirtualQuery(pointer, &info, sizeof(info)) && info.State == MEM_COMMIT &&
      !(info.Protect & (PAGE_NOACCESS | PAGE_GUARD)) &&
      pointer + size <= static_cast<uint8_t*>(info.BaseAddress) + info.RegionSize;
#else
  return false;
#endif
}
bool IsLocalConsoleActor(rex::memory::Memory* memory, uint32_t actor) {
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  if (!Readable(memory, actor, 0x314) || !Readable(memory, read(actor), 0x10C) ||
      read(read(actor) + 0x108) != 0x8262B8E0) return false;
  const auto player = read(actor + 0x310);
  return Readable(memory, player, 0x44) && read(player) == 0x820BDF90 &&
      read(player + 0x40) == actor;
}
void GameCommand(std::string_view text) {
  if (text.empty() || text.size() > 255 || text.find_first_of("\r\n") != std::string_view::npos) {
    REXLOG_WARN("Usage: game <engine command, at most 255 characters>");
    return;
  }
  std::lock_guard lock(command_mutex);
  if (commands.size() < 16) commands.emplace_back(text);
  else REXLOG_WARN("Game command queue is full");
}
void God(std::string_view) { GameCommand("God"); }
std::vector<std::string> checkpoint_ids;
bool IsCheckpointId(std::string_view id) {
  if (id.size() < 4 || id.size() > 32 || id[2] != '_' ||
      id[0] < '0' || id[0] > '9' || id[1] < '0' || id[1] > '9') return false;
  for (size_t i = 0; i < id.size(); ++i)
    if (i != 2 && !((id[i] >= '0' && id[i] <= '9') ||
        (id[i] >= 'A' && id[i] <= 'Z') || (id[i] >= 'a' && id[i] <= 'z'))) return false;
  return true;
}
void Checkpoint(std::string_view id) {
  const auto first = id.find_first_not_of(" \t");
  if (first == std::string_view::npos) id = {};
  else id = id.substr(first, id.find_last_not_of(" \t") - first + 1);
  if (id.empty() || id == "list") {
    REXLOG_INFO("checkpoint <id>: load a retail checkpoint at Difficulty=1 from an active campaign. Example: checkpoint 01_02");
    std::string line;
    for (const auto& name : checkpoint_ids) {
      if (line.size() > 100) { REXLOG_INFO("Checkpoints: {}", line); line.clear(); }
      if (!line.empty()) line += ' ';
      line += name;
    }
    if (!line.empty()) REXLOG_INFO("Checkpoints: {}", line);
    return;
  }
  if (!IsCheckpointId(id) ||
      std::find(checkpoint_ids.begin(), checkpoint_ids.end(), id) == checkpoint_ids.end()) {
    REXLOG_WARN("Unknown checkpoint '{}'; use checkpoint list for installed IDs", id);
    return;
  }
  GameCommand("open Checkpoint?LoadSaveGame?CheckpointToLoad=" + std::string(id) + "?Difficulty=1");
}
void ExecuteGameCommand(const std::string& command, uint32_t storage_profile = 0);
void RunGameCommands() {
  if (aot::ConsumeCampaignSetupDiagnostic())
    GameCommand("@test-coop-setup");
  if (auto event=aot::PollCampaignService(); !event.empty()) GameCommand(event);
  // Diagnostic file is opt-in. Each nonempty line is executed once per run.
  static size_t consumed_lines = 0;
  if (const char* path = std::getenv("AOT_GAME_COMMANDS")) {
    std::ifstream file(path);
    std::string line;
    size_t number = 0;
    while (std::getline(file, line)) {
      if (++number > consumed_lines && !line.empty()) GameCommand(line);
    }
    consumed_lines = number;
  }
  std::string command;
  {
    std::lock_guard lock(command_mutex);
    if (commands.empty()) return;
    command = std::move(commands.front());
    commands.pop_front();
  }
  if (command == "@test-frame-stall" && std::getenv("AOT_GAME_COMMANDS") &&
      std::getenv("AOT_TEST_KBM")) {
    // Fault injection for the native mouse test. No input or command mutex is
    // held: the UI must keep processing relative motion while gameplay waits.
    REXLOG_INFO("Test game-thread stall: begin (800 ms)");
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    REXLOG_INFO("Test game-thread stall: end");
    return;
  }
  ExecuteGameCommand(command);
}
void ExecuteGameCommand(const std::string& command, uint32_t storage_profile) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto actor = console_actor.load();
  if (!IsLocalConsoleActor(memory, actor)) {
    REXLOG_WARN("Game console unavailable until a local player is active: {}", command);
    return;
  }
  rex::CallFrame frame(*thread->context());
  const uint32_t sp = frame.ctx.r1.u32 - 0x400;
  if (!Readable(memory, sp - 0x1000, 0x1400)) {
    REXLOG_WARN("Insufficient guest stack for game console");
    return;
  }
  auto* base = memory->virtual_membase();
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  std::memset(base + sp, 0, 0x400);
  write(sp, frame.ctx.r1.u32);
  frame.ctx.r1.u64 = sp;
  if (command == "@preview-checkpoint-stats" && std::getenv("AOT_GAME_COMMANDS")) {
    const auto pawn = read(actor + 0x1C4); // reflected Engine.Controller.Pawn
    if (!Readable(memory, pawn, 0x1264) || !Readable(memory, read(pawn), 0xE8)) return;
    std::strcpy(reinterpret_cast<char*>(base + sp + 0x100), "OpenCheckpointInfoScene");
    frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1; frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    frame.ctx.r3.u64 = pawn;
    frame.ctx.r4.u64 = *memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80);
    frame.ctx.r5.u64 = 0;
    sub_824621B8(frame.ctx, base);
    const auto function = frame.ctx.r3.u32;
    auto* invoke = runtime->function_dispatcher()->GetFunction(read(read(pawn) + 0xE4));
    if (!function || !invoke) return;
    frame.ctx.r3.u64 = pawn; frame.ctx.r4.u64 = function;
    frame.ctx.r5.u64 = sp + 0x90; frame.ctx.r6.u64 = 0; frame.ctx.r7.s64 = -7;
    invoke(frame.ctx, base);
    REXLOG_INFO("Checkpoint table preview requested through original character method");
    return;
  }
  if (command.starts_with("@pc-coop-")) {
    if (command=="@pc-coop-checkpoint") {
      aot::ImportCampaignCheckpoint(frame.ctx,base); return;
    }
    const auto* snapshot=aot::CampaignServiceSnapshot();
    if (!snapshot || !Readable(memory,actor+0x7EC,4)) return;
    const auto plasma=read(actor+0x7EC);
    if (!Readable(memory,plasma,0x38) || read(plasma)!=0x820E90C8) {
      REXLOG_WARN("PC co-op callback has no live Plasma controller"); return;
    }
    const char* event=nullptr;
    if (command=="@pc-coop-connected") event="OnConnect";
    else if (command=="@pc-coop-created") event="OnGameCreate";
    else if (command=="@pc-coop-joined") event="OnGameJoin";
    else if (command=="@pc-coop-player-joined") event="OnPlayerJoin";
    else if (command=="@pc-coop-player-left") event="OnPlayerLeave";
    else if (command=="@pc-coop-updated") event="OnPlayerUpdated";
    else if (command=="@pc-coop-attributes") event="OnGameAttrChange";
    else if (command=="@pc-coop-disconnected") event="OnDisconnect";
    else if (command=="@pc-coop-prepare") event="OnGameStart";
    else if (command=="@pc-coop-prepared") event="AddRemoteWeapon";
    else if (command=="@pc-coop-load") event="OnPostGameStart";
    if (!event) return;
    const bool updated=command=="@pc-coop-updated";
    const bool prepared=command=="@pc-coop-prepared";
    const bool loading=command=="@pc-coop-load";
    if (loading && !snapshot->load_authorized) return;
    if ((prepared || loading || command=="@pc-coop-prepare") &&
        (!snapshot->preparation || snapshot->members.size()!=2 ||
         (snapshot->phase!=aot::CoopLobbyPhase::Hosting && snapshot->phase!=aot::CoopLobbyPhase::Joined))) return;
    const unsigned remote=snapshot->members.size()==2 && snapshot->members[0].id==snapshot->local_id ? 1 : 0;
    if (prepared && (!snapshot->prepared || !snapshot->loadouts[remote])) return;
    if (command=="@pc-coop-prepare" && !aot::ResetCampaignLoadout(frame.ctx,plasma,base)) return;
    const auto iterations=prepared ? snapshot->loadouts[remote]->weapons.size()+1 : updated ? snapshot->members.size() : size_t(1);
    for (size_t i=0;i<iterations;++i) {
      std::memset(base+sp+0x80,0,0x380);
      const auto parameters=sp+0x140;
      if (prepared) {
        const auto& equipment=*snapshot->loadouts[remote];
        write(parameters,remote);
        if (i==equipment.weapons.size()) {
          event="AddRemoteArmor";
          write(parameters+4,equipment.armor); write(parameters+8,equipment.mask);
        } else {
          const auto& weapon=equipment.weapons[i];
          const auto set_string=[&](uint32_t offset,uint32_t text_offset,const std::string& text) {
            std::memcpy(base+sp+text_offset,text.c_str(),text.size()+1);
            write(parameters+offset,sp+text_offset);
            write(parameters+offset+4,uint32_t(text.size()+1)); write(parameters+offset+8,uint32_t(text.size()+1));
          };
          set_string(4,0x200,weapon.archetype); set_string(0x10,0x300,weapon.class_name);
          for (unsigned n=0;n<7;++n) write(parameters+0x1C+n*4,weapon.upgrades[n]);
        }
      }
      else if (loading) write(parameters,0x80000000u); // native BoolProperty bit 31
      else if (updated) write(parameters,uint32_t(i));
      else if (command=="@pc-coop-player-joined" && snapshot->members.size()==2) {
        const auto& name=snapshot->members.back().name;
        std::memcpy(base+sp+0x200,name.c_str(),name.size()+1);
        write(parameters,sp+0x200); write(parameters+4,uint32_t(name.size()+1));
        write(parameters+8,uint32_t(name.size()+1));
      }
      // Other callbacks take no parameters or an empty success Error FString.
      std::memcpy(base+sp+0x100,event,std::strlen(event)+1);
      frame.ctx.r3.u64=sp+0x80; frame.ctx.r4.u64=sp+0x100;
      frame.ctx.r5.u64=1; frame.ctx.r6.u64=1;
      sub_824340D0(frame.ctx,base);
      frame.ctx.r3.u64=plasma;
      frame.ctx.r4.u64=*memory->TranslateVirtual<rex::be<uint64_t>*>(sp+0x80);
      frame.ctx.r5.u64=0;
      sub_824621B8(frame.ctx,base);
      const auto function=frame.ctx.r3.u32;
      auto* invoke=runtime->function_dispatcher()->GetFunction(read(read(plasma)+0xE4));
      if (!function || !invoke) { REXLOG_WARN("PC co-op callback missing: {}",event); return; }
      frame.ctx.r3.u64=plasma; frame.ctx.r4.u64=function; frame.ctx.r5.u64=parameters;
      frame.ctx.r6.u64=0; frame.ctx.r7.s64=-7;
      invoke(frame.ctx,base);
      REXLOG_INFO("PC co-op native callback: {} members={}",event,snapshot->members.size());
    }
    if (command=="@pc-coop-prepare") aot::SubmitCampaignLoadout(plasma,base);
    if (prepared) REXLOG_INFO("PC co-op native preparation complete: id={}, remote={}, weapons={}; gameplay loading pending",
        snapshot->preparation,remote,snapshot->loadouts[remote]->weapons.size());
    if (prepared) aot::AcknowledgeCampaignImport(plasma,base);
    if (prepared) aot::PrepareCampaignCheckpoint(frame.ctx,base);
    if (loading) aot::BeginCampaignLoad(frame.ctx,base);
    return;
  }
  if (command == "@test-coop-setup") {
    const auto* diagnostic = std::getenv("AOT_PC_COOP_UI_DIAGNOSTIC");
    const auto* gate = std::getenv("AOT_PC_COOP_DIAGNOSTIC");
    if (!diagnostic || std::string_view(diagnostic) != "1" ||
        !gate || std::string_view(gate) != "1" || !std::getenv("AOT_NETWORK_LOG")) return;
    // Replay the verified no-argument UI callback. Its original script both
    // dispatches ConnectedToPlasmaEvent and closes ConnectingToPlasmaScene.
    // No native service flags or peer/session state are synthesized.
    constexpr char event[] = "OnConnect";
    std::memcpy(base + sp + 0x100, event, sizeof(event));
    frame.ctx.r3.u64 = sp + 0x80;
    frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1;
    frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    frame.ctx.r3.u64 = actor;
    frame.ctx.r4.u64 = *memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80);
    frame.ctx.r5.u64 = 0;
    sub_824621B8(frame.ctx, base);
    const auto function = frame.ctx.r3.u32;
    auto* invoke = runtime->function_dispatcher()->GetFunction(read(read(actor) + 0xE4));
    if (!function || !invoke) return;
    frame.ctx.r3.u64 = actor; frame.ctx.r4.u64 = function;
    frame.ctx.r5.u64 = 0; frame.ctx.r6.u64 = 0; frame.ctx.r7.s64 = -7;
    invoke(frame.ctx, base);
    REXLOG_INFO("Campaign UI fixture: retail OnConnect UI callback replayed; backend remains offline");
    return;
  }
  if (command.starts_with("@test-remote-event ")) {
    // Diagnostic replay uses the retail ActivateRemoteEvent action's lookup
    // and CheckActivate ABI (0x828369D0), without changing trigger limits.
    if (!std::getenv("AOT_GAME_COMMANDS")) {
      REXLOG_WARN("Remote-event replay requires a diagnostic command file");
      return;
    }
    const auto event_name = command.substr(19);
    if (event_name.empty() || event_name.size() > 96 ||
        event_name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos) {
      REXLOG_WARN("Remote-event replay requires one event name");
      return;
    }
    std::memcpy(base + sp + 0x100, event_name.c_str(), event_name.size() + 1);
    frame.ctx.r3.u64 = sp + 0x80;
    frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1;
    frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    const auto name = static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80));
    const auto objects = read(0x8311471C), count = read(0x83114720);
    if (!count || count > 500000 || !Readable(memory, objects, count * 4)) return;
    uint32_t event = 0, matches = 0;
    for (uint32_t i = 0; i < count; ++i) {
      const auto object = read(objects + i * 4);
      // The live table is owned by this game thread, as in the menu lookup.
      if (!object || read(object) != 0x820D1DA0 || !Readable(memory, object, 0xF0)) continue;
      if (*memory->TranslateVirtual<rex::be<uint64_t>*>(object + 0xE8) != name) continue;
      event = object;
      ++matches;
    }
    if (matches != 1 || !(read(event + 0xD8) & 0x80000000u)) {
      REXLOG_WARN("Remote event {} requires one enabled instance; matches={}", event_name, matches);
      return;
    }
    const auto world = read(0x83122588);
    if (!Readable(memory, world, 0x54)) return;
    const auto level = read(world + 0x50);
    if (!Readable(memory, level, 0x94) || !read(level + 0x90)) return;
    const auto actors = read(level + 0x8C);
    if (!Readable(memory, actors, 4)) return;
    const auto world_info = read(actors);
    if (!Readable(memory, world_info, 0x340)) return;
    const auto method = read(read(event) + 0x16C);
    auto* invoke = runtime->function_dispatcher()->GetFunction(method);
    if (method != 0x827E8A30 || !invoke) return;
    const auto before = read(event + 0xCC);
    frame.ctx.r3.u64 = event;
    frame.ctx.r4.u64 = world_info;
    frame.ctx.r5.u64 = world_info;
    frame.ctx.r6.u64 = 0;
    frame.ctx.r7.u64 = 0;
    frame.ctx.r8.u64 = 0;
    invoke(frame.ctx, base);
    REXLOG_INFO("Remote event {}: instance={:08X}, eligible={}, trigger_count={} -> {}",
        event_name, event, frame.ctx.r3.u32, before, read(event + 0xCC));
    return;
  }
  if (command == "@local-device-selected") {
    // The device-check path obtains AO2ProfileSettings from controller+0x7E0.
    // Select the device through its original setter, including the profile's
    // SelectedDeviceID setting, rather than updating only the UI cache.
    const auto profile = storage_profile;
    if (!Readable(memory, profile, 0x38) || !Readable(memory, read(profile), 0xE8)) return;
    constexpr char event[] = "SetCurrentDeviceID";
    std::memcpy(base + sp + 0x100, event, sizeof(event));
    frame.ctx.r3.u64 = sp + 0x80;
    frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1;
    frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    frame.ctx.r3.u64 = profile;
    frame.ctx.r4.u64 = *memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80);
    frame.ctx.r5.u64 = 0;
    sub_824621B8(frame.ctx, base);
    const auto function = frame.ctx.r3.u32;
    const auto method = read(read(profile) + 0xE4);
    auto* invoke = runtime->function_dispatcher()->GetFunction(method);
    if (!function || !invoke) { REXLOG_WARN("Local device notification is unavailable"); return; }
    write(sp + 0x90, 1); // DeviceID, the setter's only input parameter
    frame.ctx.r3.u64 = profile;
    frame.ctx.r4.u64 = function;
    frame.ctx.r5.u64 = sp + 0x90;
    frame.ctx.r6.u64 = 0;
    frame.ctx.r7.s64 = -7;
    invoke(frame.ctx, base);
    REXLOG_INFO("Local save device selected through AO2ProfileSettings.SetCurrentDeviceID");
    return;
  }
  write(sp + 0x90, sp + 0x100);
  write(sp + 0x94, static_cast<uint32_t>(command.size() + 1));
  write(sp + 0x98, static_cast<uint32_t>(command.size() + 1));
  std::memcpy(base + sp + 0x100, command.c_str(), command.size() + 1);
  frame.ctx.r3.u64 = sp + 0x80;
  frame.ctx.r4.u64 = actor;
  frame.ctx.r5.u64 = sp + 0x90;
  frame.ctx.r6.u64 = 0;
  sub_8262B8E0(frame.ctx, base);
  const auto output = read(sp + 0x80), count = read(sp + 0x84);
  if (count && count < 65536 && Readable(memory, output, count))
    REXLOG_INFO("Game [{}]: {}", command, std::string(reinterpret_cast<char*>(base + output), count - 1));
  else REXLOG_INFO("Game [{}]: command returned without text", command);
  frame.ctx.r3.u64 = sp + 0x80;
  sub_82305110(frame.ctx, base);
}
void Controllers(std::string_view) {
  if (!runtime) return;
  auto* input = dynamic_cast<rex::input::InputSystem*>(runtime->input_system());
  if (!input) return;
  for (unsigned slot = 0; slot < 4; ++slot) {
    rex::input::X_INPUT_CAPABILITIES caps{};
    auto result = input->GetCapabilities(slot, 0, &caps);
    REXLOG_INFO("Controller {}: {}", slot + 1, result == 0 ? "connected" : "not connected");
  }
}
void Help(std::string_view) {
  REXLOG_INFO("Sierra Romeo PC: controllers | aot_fps 30/60 | aot_disable_blur true/false | fullscreen true/false");
  REXLOG_INFO("Tilde: console. F3: measured game FPS. F4: runtime settings. Resolution scale changes require restart.");
  REXLOG_INFO("god: toggle original player godmode. game <command>: original engine console (experimental).");
  REXLOG_INFO("checkpoint list | checkpoint 01_02 (Somalia courtyard) | checkpoint 04_01 (Afghanistan canyon). Debug travel can update the active save.");
}
}
REXCVAR_DEFINE_COMMAND_ARGS(controllers, Controllers, "Sierra Romeo", "Report game controller slots");
REXCVAR_DEFINE_COMMAND_ARGS(aot_help, Help, "Sierra Romeo", "Show PC debug commands");
REXCVAR_DEFINE_COMMAND_ARGS(game, GameCommand, "Sierra Romeo", "Run an experimental original engine console command on the game thread");
REXCVAR_DEFINE_COMMAND_ARGS(god, God, "Sierra Romeo", "Toggle the original player godmode flag for testing");
REXCVAR_DEFINE_COMMAND_ARGS(checkpoint, Checkpoint, "Sierra Romeo", "Load an installed retail checkpoint; checkpoint list shows IDs");

namespace aot {
class MenuOverlay final : public rex::ui::ImGuiDialog {
 public:
  MenuOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
      rex::ui::Window* window, const std::filesystem::path& branding)
      : ImGuiDialog(drawer), window_(window) {
  }
  void OnDraw(ImGuiIO& io) override {
    if (main_menu_active.load() && Milliseconds() - menu_seen_ms.load() < 500) {
      constexpr auto label = "Sierra Romeo  " AOT_VERSION;
      const auto size = ImGui::CalcTextSize(label);
      const ImVec2 position{io.DisplaySize.x - size.x - 20.0f,
                            io.DisplaySize.y - size.y - 12.0f};
      auto* draw = ImGui::GetBackgroundDrawList();
      draw->AddRectFilled({position.x - 7, position.y - 4},
          {position.x + size.x + 7, position.y + size.y + 4},
          IM_COL32(0, 0, 0, 160), 4.0f);
      draw->AddText(position, IM_COL32(235, 235, 235, 230), label);
    }
    if (exit_requested.exchange(false)) {
      REXLOG_INFO("PC Exit confirmed; requesting window close");
#ifdef _WIN32
      PostMessageW(static_cast<HWND>(window_->GetNativeWindowHandle()), WM_CLOSE, 0, 0);
#endif
    }
  }
 private:
  rex::ui::Window* window_;
};
void CreateMenuOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
    rex::ui::Window* window, const std::filesystem::path& branding) {
  new MenuOverlay(drawer, immediate, window, branding);
}
class CursorPolicy final : public rex::ui::ImGuiDialog {
 public:
  CursorPolicy(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window)
      : ImGuiDialog(drawer), window_(window) {}
  void OnDraw(ImGuiIO& io) override {
    const bool interactive = io.WantCaptureMouse || io.WantTextInput;
    UpdateMouseCapture(window_, interactive);
    const auto visibility = !window_->HasFocus() || interactive
        ? rex::ui::Window::CursorVisibility::kVisible
        : rex::ui::Window::CursorVisibility::kHidden;
    if (window_->GetCursorVisibility() != visibility) {
      window_->SetCursorVisibility(visibility);
    }
    if (!reported_ || previous_visibility_ != visibility) {
      REXLOG_INFO("PC cursor: {} (focused={}, interactive overlay={})",
          visibility == rex::ui::Window::CursorVisibility::kVisible ? "visible" : "hidden",
          window_->HasFocus(), interactive);
      reported_ = true;
      previous_visibility_ = visibility;
    }
  }
 private:
  rex::ui::Window* window_;
  bool reported_ = false;
  rex::ui::Window::CursorVisibility previous_visibility_{};
};
void CreateCursorPolicy(rex::ui::ImGuiDrawer* drawer, rex::ui::Window* window) {
  new CursorPolicy(drawer, window);
}
class SkipOverlay final : public rex::ui::ImGuiDialog {
 public:
  SkipOverlay(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate,
      const std::filesystem::path& branding) : ImGuiDialog(drawer) {
    for (int i = 0; i < 2; ++i) {
    std::ifstream file(branding / (i ? "skip-prompt-keyboard.rgba" : "skip-prompt.rgba"), std::ios::binary);
    uint32_t header[4]{};
    file.read(reinterpret_cast<char*>(header), sizeof(header));
    if (!file || !header[0] || header[0] > 1024 || !header[1] || header[1] > 256) return;
    std::vector<uint8_t> pixels(header[0] * header[1] * 4);
    file.read(reinterpret_cast<char*>(pixels.data()), pixels.size());
    if (!file) return;
    width_ = header[0]; height_ = header[1]; button_x_ = header[2]; button_y_ = header[3];
    textures_[i] = immediate->CreateTexture(header[0], header[1], rex::ui::ImmediateTextureFilter::kLinear, false, pixels.data());
    }
  }
  void OnDraw(ImGuiIO& io) override {
    const auto now = Milliseconds();
    opacity_ = SkipPromptAlpha(opacity_, now - movie_seen_ms.load() <= 200,
        now - movie_input_ms.load() <= 1500, io.DeltaTime);
    if (opacity_ <= 0) return;
    const auto alpha = static_cast<int>(opacity_ * 255);
    const float scale = io.DisplaySize.y / 720.f;
    auto* draw = ImGui::GetForegroundDrawList();
    ImVec2 pos(io.DisplaySize.x - (width_ + 40) * scale, io.DisplaySize.y - (height_ + 28) * scale);
    auto* texture = textures_[KeyboardPrompts() ? 1 : 0].get();
    if (texture) {
      draw->AddImage(reinterpret_cast<ImTextureID>(texture), ImVec2(pos.x + 1, pos.y + 1),
          ImVec2(pos.x + width_ * scale + 1, pos.y + height_ * scale + 1), ImVec2(0, 0), ImVec2(1, 1), IM_COL32(0, 0, 0, alpha));
      draw->AddImage(reinterpret_cast<ImTextureID>(texture), pos, ImVec2(pos.x + width_ * scale, pos.y + height_ * scale), ImVec2(0, 0), ImVec2(1, 1), IM_COL32(255, 255, 255, alpha));
    }
    const float progress = skip_progress.load();
    if (progress > 0) {
      const ImVec2 center(pos.x + button_x_ * scale, pos.y + button_y_ * scale);
      draw->AddCircle(center, 16 * scale, IM_COL32(15, 15, 15, alpha), 48, 3 * scale);
      draw->PathArcTo(center, 16 * scale, -1.5707963f, -1.5707963f + progress * 6.2831853f, 48);
      draw->PathStroke(IM_COL32(255, 255, 255, alpha), 0, 2.5f * scale);
    }
  }
 private:
  std::unique_ptr<rex::ui::ImmediateTexture> textures_[2];
  float width_ = 210, height_ = 40, button_x_ = 65, button_y_ = 20;
  float opacity_ = 0;
};
rex::ui::ImGuiDialog* CreateSkipOverlay(rex::ui::ImGuiDrawer* drawer,
    rex::ui::ImmediateDrawer* immediate, const std::filesystem::path& branding) {
  return new SkipOverlay(drawer, immediate, branding);
}
void InitializePcFeatures(rex::Runtime* instance, const std::filesystem::path& game_data) {
  runtime = instance;
  checkpoint_ids.clear();
  std::error_code error;
  std::filesystem::directory_iterator files(game_data / "AO2Game/Checkpoints", error), end;
  for (size_t count = 0; !error && files != end && count < 128; files.increment(error), ++count) {
    const auto id = files->path().filename().string();
    if (IsCheckpointId(id) && files->is_regular_file(error)) checkpoint_ids.push_back(id);
  }
  std::sort(checkpoint_ids.begin(), checkpoint_ids.end());
  pc_features_started = Clock::now();
  InitializeWaitDiagnostics();
  if (const char* path = std::getenv("AOT_FRAME_LOG")) {
    timing.open(path);
    timing << "frame,interval_ms\n";
  }
  if (const char* path = std::getenv("AOT_FRAME_PHASE_LOG")) {
    phase_timing.open(path);
    phase_timing << "frame,entry_interval_ms,between_hooks_ms,between_hooks_cpu_ms,commands_ms,pacing_ms,bookkeeping_ms,previous_trace_write_ms\n";
  }
}

} // namespace aot

void AotCreditsPrefix(PPCRegister& stack, PPCRegister& group, PPCRegister& row) {
  if (group.u32 != 1 || row.u32 != 1) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  constexpr uint32_t reserve = 0x1000;
  static_assert(sizeof(aot::kPortCredits) < reserve - 0x100);
  if (stack.u32 < reserve + 0x2000 ||
      !Readable(memory, stack.u32 - reserve - 0x2000, reserve + 0x2084)) return;
  rex::CallFrame frame(*thread->context());
  const auto sp = stack.u32 - reserve;
  auto* base = memory->virtual_membase();
  *memory->TranslateVirtual<rex::be<uint32_t>*>(sp) = stack.u32;
  std::memcpy(base + sp + 0x100, aot::kPortCredits, sizeof(aot::kPortCredits));
  frame.ctx.r1.u64 = sp;
  // UIScene_Credits::Initialize builds each group with FString::operator+=
  // at 8234FE60. Append our prefix to the same string before its first line.
  frame.ctx.r3.u64 = stack.u32 + 0x58;
  frame.ctx.r4.u64 = sp + 0x100;
  sub_82305190(frame.ctx, base);
  REXLOG_INFO("PC port credits prepended; original credits retained");
}

namespace {
// Retail AO2PlayerControllerNative.ProfileSettings is at +0x7E0, verified
// through its UProperty offset and the live local-player/controller backrefs.
bool GrantLocalWeapons(uint32_t player) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  if (!Readable(memory, player, 0x44) || read(player) != 0x820BDF90) return false;
  const auto controller = read(player + 0x40);
  if (!IsLocalConsoleActor(memory, controller) || !Readable(memory, controller, 0x7E4) ||
      read(controller + 0x310) != player || read(controller) != 0x82042C00) return false;
  const auto profile = read(controller + 0x7E0);
  if (!Readable(memory, profile, 0xA0) || read(profile) != 0x8203C660) return false;
  rex::CallFrame frame(*thread->context());
  const auto sp = frame.ctx.r1.u32 - 0x400;
  if (!Readable(memory, sp - 0x2000, 0x2400)) return false;
  auto* base = memory->virtual_membase();
  std::memset(base + sp, 0, 0x400);
  write(sp, frame.ctx.r1.u32); frame.ctx.r1.u64 = sp;
  auto method = [&](uint32_t receiver, const char* text, uint16_t params_size) {
    std::strcpy(reinterpret_cast<char*>(base + sp + 0x100), text);
    frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1; frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    frame.ctx.r3.u64 = receiver;
    frame.ctx.r4.u64 = *memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80);
    frame.ctx.r5.u64 = 0;
    sub_824621B8(frame.ctx, base);
    const auto function = frame.ctx.r3.u32;
    if (!Readable(memory, function, 0xAC) || read(function) != 0x82060570 ||
        (read(function + 0x9C) & 0xFFFF) != params_size) return uint32_t(0);
    return function;
  };
  const auto own = method(profile, "OwnAllWeapons", 4);
  const auto bonus = method(profile, "UnlockContestWeapons", 4);
  const auto get = method(profile, "GetProfileSettingValueInt", 12);
  const auto save = method(controller, "SaveProfileSettings", 0);
  auto* profile_invoke = runtime->function_dispatcher()->GetFunction(read(read(profile) + 0xE4));
  auto* controller_invoke = runtime->function_dispatcher()->GetFunction(read(read(controller) + 0xE4));
  if (!own || !bonus || !get || !save || !profile_invoke || !controller_invoke) {
    REXLOG_WARN("Local weapon unlock: retail methods unavailable (own={:08X}, bonus={:08X}, get={:08X}, save={:08X})", own, bonus, get, save);
    return false;
  }
  auto invoke = [&](uint32_t receiver, uint32_t function, uint32_t argument) {
    std::memset(base + sp + 0x200, 0, 16); write(sp + 0x200, argument);
    frame.ctx.r3.u64 = receiver; frame.ctx.r4.u64 = function; frame.ctx.r5.u64 = sp + 0x200;
    frame.ctx.r6.u64 = 0; frame.ctx.r7.s64 = -7;
    (receiver == profile ? profile_invoke : controller_invoke)(frame.ctx, base);
  };
  for (uint32_t type = 0; type < 3; ++type) invoke(profile, own, type);
  invoke(profile, bonus, 2);
  unsigned verified = 0;
  for (uint32_t id = 600; id <= 771; ++id) {
    if (!((id <= 611) || (id >= 620 && id <= 630) ||
          (id >= 640 && id <= 647) || id == 770 || id == 771)) continue;
    invoke(profile, get, id);
    if (!read(sp + 0x208) || read(sp + 0x204) != 1) {
      REXLOG_WARN("Local weapon unlock verification failed for profile setting {}", id);
      return false;
    }
    ++verified;
  }
  invoke(controller, save, 0);
  REXLOG_INFO("Local weapon unlock: {} ownership/bonus settings verified; local profile save requested (player={:08X}, profile={:08X})", verified, player, profile);
  return true;
}
} // namespace

bool AotOpenWeaponUnlock(PPCRegister& action) {
  const auto scene = extras_scene.load();
  if (!scene || Milliseconds() - menu_seen_ms >= 250) return false;
  auto* memory = rex::runtime::ThreadState::Get()->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  if (!Readable(memory, action.u32, 0xE8) || read(action.u32) != 0x8203DF58) return false;
  // Restrict the replacement to the permission action owned by the live Extras scene.
  auto outer = action.u32;
  for (unsigned depth = 0; depth < 10 && Readable(memory, outer, 0x2C); ++depth) {
    outer = read(outer + 0x28);
    if (outer != scene) continue;
    { std::lock_guard lock(menu_input_mutex); exit_policy.blocked |= exit_policy.previous; }
    unlock_open_requested = scene;
    REXLOG_INFO("Extras Unlock Weapons: local confirmation requested");
    return true;
  }
  return false;
}

void AotObserveMenu(PPCRegister& client) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  if (!Readable(memory, client.u32, 0x140) || read(client.u32) != 0x820FA198) return;
  const uint32_t scenes = read(client.u32 + 0xC0), count = read(client.u32 + 0xC4);
  if (count > 64 || (count && !Readable(memory, scenes, count * 4))) return;
  rex::CallFrame frame(*thread->context());
  const uint32_t sp = frame.ctx.r1.u32 - 0x500;
  if (!Readable(memory, sp - 0x2000, 0x2500)) return;
  auto* base = memory->virtual_membase();
  std::memset(base + sp, 0, 0x500);
  write(sp, frame.ctx.r1.u32);
  frame.ctx.r1.u64 = sp;
  auto name = [&](const char* text) {
    std::strcpy(reinterpret_cast<char*>(base + sp + 0x100), text);
    frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = sp + 0x100;
    frame.ctx.r5.u64 = 1; frame.ctx.r6.u64 = 1;
    sub_824340D0(frame.ctx, base);
    return static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80));
  };
  static uint64_t main_tag = 0, action_name = 0, options_tag = 0, storage_button_name = 0, extras_tag = 0;
  if (!main_tag) {
    extras_tag = name("ExtrasScene");
    main_tag = name("MainMenuScene"); action_name = name("Default__UIAction_DisplayMessageBox");
    options_tag = name("AllOptionsUIScene"); storage_button_name = name("UILabelButton_0");
    controller_font_name = name("Xbox360_18pt");
    hud_readability_font_name = name("hud_action_font_z");
    dark_hud_font_names[0] = name("mp_small");
    dark_hud_font_names[1] = name("mp_med");
    dark_hud_font_names[2] = name("mp_large");
  }
  const uint32_t top = count ? read(scenes + (count - 1) * 4) : 0;
  const bool is_main = Readable(memory, top, 0x15C) &&
      *memory->TranslateVirtual<rex::be<uint64_t>*>(top + 0x154) == main_tag;
  const bool is_options = Readable(memory, top, 0x180) &&
      *memory->TranslateVirtual<rex::be<uint64_t>*>(top + 0x154) == options_tag;
  const bool is_extras = Readable(memory, top, 0x180) &&
      *memory->TranslateVirtual<rex::be<uint64_t>*>(top + 0x154) == extras_tag;
  extras_scene = is_extras ? top : 0;
  options_active = is_options;
  static bool reached_main = false;
  reached_main |= is_main;
  aot::SetKeyboardMenuContext(!reached_main, is_main, count != 0);
  if (main_menu_active.exchange(is_main) != is_main)
    REXLOG_INFO("PC menu state: main_menu={}, scenes={}, top={:08X}", is_main, count, top);
  menu_seen_ms = Milliseconds();

  auto set_text = [&](uint32_t receiver, const char* method_name, const char* text) {
    const auto method_name_id = name(method_name);
    frame.ctx.r3.u64 = receiver; frame.ctx.r4.u64 = method_name_id; frame.ctx.r5.u64 = 0;
    sub_824621B8(frame.ctx, base);
    const auto function = frame.ctx.r3.u32;
    auto* invoke = runtime->function_dispatcher()->GetFunction(read(read(receiver) + 0xE4));
    if (!function || !invoke) return;
    std::strcpy(reinterpret_cast<char*>(base + sp + 0x200), text);
    write(sp + 0x90, sp + 0x200); write(sp + 0x94, static_cast<uint32_t>(std::strlen(text) + 1));
    write(sp + 0x98, static_cast<uint32_t>(std::strlen(text) + 1));
    frame.ctx.r3.u64 = receiver; frame.ctx.r4.u64 = function; frame.ctx.r5.u64 = sp + 0x90;
    frame.ctx.r6.u64 = 0; frame.ctx.r7.s64 = -7;
    invoke(frame.ctx, base);
  };
  static uint32_t labeled_options = 0;
  static uint32_t connection_label_scene=0;
  static std::string connection_label;
  const auto connection_status=aot::CampaignConnectionStatus();
  if (!connection_status.empty() && Readable(memory,top,0x170) && read(top)==0x82044C88 &&
      *memory->TranslateVirtual<rex::be<uint64_t>*>(top+0x154)==name("AO2Confirmation") &&
      (top!=connection_label_scene || connection_status!=connection_label)) {
    set_text(top,"SetTitle",connection_status==" "?"PC Co-op Connection":"PC Co-op");
    set_text(top,"SetMessage",connection_status.c_str());
    connection_label_scene=top;connection_label=connection_status;
  }
  if (connection_status.empty()) {connection_label_scene=0;connection_label.clear();}
  static uint32_t labeled_coop_lobby=0;
  const auto* coop_state=aot::CampaignServiceSnapshot();
  const bool coop_lobby=coop_state && !coop_state->preparation &&
      (coop_state->phase==aot::CoopLobbyPhase::Hosting || coop_state->phase==aot::CoopLobbyPhase::Joined) &&
      Readable(memory,top,0x180) && *memory->TranslateVirtual<rex::be<uint64_t>*>(top+0x154)==name("CoopNetworkLobbyScene");
  if (!coop_lobby) labeled_coop_lobby=0;
  if (coop_lobby && labeled_coop_lobby!=top) {
    // The original Friends button is nested inside the safe-region panel.
    std::vector<uint32_t> widgets{top};
    const auto operations=name("Operations");
    for (size_t index=0;index<widgets.size() && index<512;++index) {
      const auto widget=widgets[index];
      if (!Readable(memory,widget,0x180)) continue;
      if (read(widget)==0x8204BD20 && *memory->TranslateVirtual<rex::be<uint64_t>*>(widget+0x2C)==operations) {
        set_text(widget,"SetCaption","Invite Partner");labeled_coop_lobby=top;
        REXLOG_INFO("PC co-op lobby invitation button relabeled");break;
      }
      const auto children=read(widget+0x178), size=read(widget+0x17C);
      if (size<=128 && widgets.size()+size<=512 && Readable(memory,children,size*4))
        for (uint32_t i=0;i<size;++i) widgets.push_back(read(children+i*4));
    }
  }
  if (!is_options) labeled_options = 0;
  if (is_options && labeled_options != top) {
    const auto widgets = read(top + 0x178), widget_count = read(top + 0x17C);
    if (widget_count <= 256 && Readable(memory, widgets, widget_count * 4)) {
      for (uint32_t i = 0; i < widget_count; ++i) {
        const auto widget = read(widgets + i * 4);
        if (Readable(memory, widget, 0x174) && read(widget) == 0x820360C0 && read(widget + 0x170) == top &&
            *memory->TranslateVirtual<rex::be<uint64_t>*>(widget + 0x2C) == storage_button_name) {
          set_text(widget, "SetCaption", "PC Display");
          labeled_options = top;
          REXLOG_INFO("Replaced Options storage button with PC Display: widget={:08X}", widget);
          break;
        }
      }
    }
  }

  static uint32_t owned_action = 0, owned_scene = 0, owned_index = 0;
  enum class Dialog { Exit, Settings, Unlock, UnlockResult, CoopNotice, CoopSetup };
  static Dialog owned_kind = Dialog::Exit;
  static uint32_t owned_player = 0;
  static bool result_received = false, accepted = false;
  bool show_unlock_result = false, unlock_succeeded = false;
  const bool owned_pc_settings = owned_kind == Dialog::Settings;
  const uint32_t objects = read(0x8311471C), object_count = read(0x83114720);
#include "checkpoint_table.inc"
  if (owned_scene) {
    if (owned_index < object_count && Readable(memory, objects + owned_index * 4, 4) &&
        read(objects + owned_index * 4) == owned_action && Readable(memory, owned_action, 0x114)) {
      if (owned_pc_settings && aot::TakePcSettingsCloseRequest())
        write(owned_action + 0x104, (read(owned_action + 0x104) & ~0x08000000u) | 0x10000000u);
      if (owned_kind==Dialog::CoopSetup && aot::TakeCoopConnectionCloseRequest())
        write(owned_action + 0x104, (read(owned_action + 0x104) & ~0x08000000u) | 0x10000000u);
      // Normally the Kismet sequence scheduler ticks this latent action. Our
      // private action needs the same update to fade/close its scene and restore
      // the menu beneath it, including the original output/result handling.
      frame.ctx.r3.u64 = owned_action; frame.ctx.f1.f64 = 1.0 / 60.0;
      sub_82336AE8(frame.ctx, base);
      const auto flags = read(owned_action + 0x104);
      if (!result_received && (flags & 0x10000000)) {
        result_received = true; accepted = (flags & 0x08000000) != 0;
        REXLOG_INFO("Original {} dialog result: accepted={}", owned_kind==Dialog::CoopSetup ? "PC Co-op Connection" : owned_pc_settings ? "PC Display" : owned_kind == Dialog::Unlock ? "Unlock Weapons" : owned_kind == Dialog::UnlockResult ? "Unlock Result" : owned_kind == Dialog::CoopNotice ? "PC Co-op" : "Exit", accepted);
      }
    }
    bool present = false;
    for (uint32_t i = 0; i < count; ++i) present |= read(scenes + i * 4) == owned_scene;
    if (!present) {
      if (owned_pc_settings) aot::EndPcSettings();
      else if (owned_kind==Dialog::CoopSetup) aot::EndCoopConnection();
      else if (result_received && accepted && owned_kind == Dialog::Exit) exit_requested = true;
      else if (result_received && accepted && owned_kind == Dialog::Unlock) {
        unlock_succeeded = GrantLocalWeapons(owned_player);
        show_unlock_result = true;
      }
      owned_scene = owned_action = 0;
    }
  }
  // The shell's shared help scene owns the real Select/Back footer. Append to
  // its binding so the retail renderer supplies the font, glyph, spacing and
  // safe-region layout. Never decorate shared templates or in-game pause UI.
  const auto help_tag = name("HelpTextUIScene");
  const auto help_label_name = name("lblHelpText");
  const auto confirmation_tag = name("AO2Confirmation");
  const auto coop_lobby_tag = name("CoopNetworkLobbyScene");
  uint32_t help_scene = 0;
  for (uint32_t i = 0; i < count; ++i) {
    const auto scene = read(scenes + i * 4);
    if (!Readable(memory, scene, 0x180)) continue;
    const auto tag = static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(scene + 0x154));
    if (tag == help_tag) help_scene = scene;
  }
  const auto top_tag = Readable(memory, top, 0x15C)
      ? static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(top + 0x154)) : 0;
  frontend_exit_active = reached_main && help_scene && !owned_scene &&
      top_tag != confirmation_tag && top_tag != coop_lobby_tag;
  if (help_scene) {
    std::vector<uint32_t> footer_widgets{help_scene};
    if (top != help_scene) footer_widgets.push_back(top);
    for (size_t index = 0; index < footer_widgets.size() && index < 256; ++index) {
      const auto label = footer_widgets[index];
      if (!Readable(memory, label, 0x180)) continue;
      const auto children = read(label + 0x60), size = read(label + 0x64);
      if (size <= 64 && footer_widgets.size() + size <= 256 && Readable(memory, children, size * 4))
        for (uint32_t i = 0; i < size; ++i) footer_widgets.push_back(read(children + i * 4));
      {
        if (!Readable(memory, label, 0x350) || read(label) != 0x82049F90 ||
            *memory->TranslateVirtual<rex::be<uint64_t>*>(label + 0x2C) != help_label_name) continue;
        const auto data = read(label + 0x344), length = read(label + 0x348);
        if (!length || length > 384 || !Readable(memory, data, length) || base[data + length - 1]) continue;
        std::string binding(reinterpret_cast<const char*>(base + data), length - 1);
        constexpr std::string_view suffix = "  <Fonts:00_fonts.Xbox360_18pt>><Fonts:/> Exit";
        const bool appended = binding.ends_with(suffix);
        if (frontend_exit_active && !appended && binding.starts_with("<Strings:AO2Game.ShellHelpText.")) {
          binding += suffix;
          set_text(label, "SetDataStoreBinding", binding.c_str());
          REXLOG_INFO("Native shell Exit footer installed: label={:08X}", label);
        } else if (!frontend_exit_active && appended) {
          binding.resize(binding.size() - suffix.size());
          set_text(label, "SetDataStoreBinding", binding.c_str());
        }
      }
    }
  }
  const bool open_exit = exit_open_requested.exchange(false) && frontend_exit_active;
  const bool open_pc = aot::TakePcSettingsOpenRequest() && is_options;
  const auto unlock_request = unlock_open_requested.exchange(0);
  const bool open_unlock = unlock_request && unlock_request == top && is_extras;
  const bool open_coop=!owned_scene && top && !open_exit && !open_pc && !open_unlock && !show_unlock_result &&
      aot::TakeCoopConnectionOpenRequest();
  const auto coop_notice=owned_scene || !top || open_exit || open_pc || open_unlock || show_unlock_result || open_coop
      ? std::string{} : aot::ConsumeCampaignNotice();
  const bool show_coop_notice=!coop_notice.empty();
  if ((!open_exit && !open_pc && !open_unlock && !show_unlock_result && !show_coop_notice && !open_coop) || owned_scene) return;
  struct SetupRollback {
    bool pending;
    ~SetupRollback() {if (pending) aot::EndCoopConnection();}
  } setup_rollback{open_coop};
  // Duplicate the game's message-box action, which owns the original result
  // delegates. Never edit a shared template or borrow another menu's callback.
  uint32_t source = 0;
  const auto opened_at = Milliseconds();
  if (object_count > 300000 || !Readable(memory, objects, object_count * 4)) return;
  for (uint32_t i = 0; i < object_count; ++i) {
    const auto object = read(objects + i * 4);
    // GObjects is the engine's live object table, read on its own game thread.
    // Avoid a Windows VirtualQuery syscall for every object in this table.
    if (object && read(object) == 0x8203E3A8 && Readable(memory, object, 0x114) &&
        *memory->TranslateVirtual<rex::be<uint64_t>*>(object + 0x2C) == action_name) {
      source = object; break;
    }
  }
  if (!source) { REXLOG_WARN("Original confirmation action template unavailable"); return; }
  sub_824552B0(frame.ctx, base); // original transient package
  const auto outer = frame.ctx.r3.u32;
  std::strcpy(reinterpret_cast<char*>(base + sp + 0x100), open_coop ? "PCCoopSetupAction" : show_coop_notice ? "PCCoopNoticeAction" : open_pc ? "PCDisplayAction" : open_unlock ? "PCWeaponUnlockAction" : show_unlock_result ? "PCWeaponUnlockResultAction" : "PCExitConfirmationAction");
  frame.ctx.r3.u64 = source; frame.ctx.r4.u64 = source; frame.ctx.r5.u64 = outer;
  frame.ctx.r6.u64 = sp + 0x100; frame.ctx.r7.u64 = 0x400000000000ull;
  frame.ctx.r8.u64 = 0; frame.ctx.r9.u64 = 0;
  sub_824716C8(frame.ctx, base);
  const auto action = frame.ctx.r3.u32;
  if (!Readable(memory, action, 0x114) || read(action) != 0x8203E3A8) {
    REXLOG_WARN("Could not duplicate original confirmation action"); return;
  }
  if (open_coop) {
    // ConnectingToPlasma already owns a message box with the standard tag.
    // OpenScene rejects duplicate tags, so give this private copy its own
    // scene archetype and tag without editing the shared retail asset.
    const auto template_scene=read(action+0x10C);
    if (!Readable(memory,template_scene,0x170)) return;
    std::strcpy(reinterpret_cast<char*>(base+sp+0x100),"PCCoopConnectionTemplate");
    frame.ctx.r3.u64=template_scene;frame.ctx.r4.u64=template_scene;frame.ctx.r5.u64=outer;
    frame.ctx.r6.u64=sp+0x100;frame.ctx.r7.u64=0x400000000000ull;frame.ctx.r8.u64=0;frame.ctx.r9.u64=0;
    sub_824716C8(frame.ctx,base);
    const auto copy=frame.ctx.r3.u32;
    if (!Readable(memory,copy,0x170)) return;
    *memory->TranslateVirtual<rex::be<uint64_t>*>(copy+0x154)=name("PCCoopConnectionScene");
    write(action+0x10C,copy);
  }
  write(action + 0x104, read(action + 0x104) & ~0xF8000000u); // full Accept/Cancel, no previous result
  if (open_pc || open_coop) write(action + 0x104, read(action + 0x104) | 0x20000000u); // overlay handles input
  if (show_unlock_result || show_coop_notice) write(action + 0x104, read(action + 0x104) | 0x80000000u); // original informational Continue-only layout
  write(action + 0xB8, 0); // local player index
  frame.ctx.r3 = client; frame.ctx.r4.u64 = read(action + 0x10C);
  frame.ctx.r5.u64 = read(top + 0x164); frame.ctx.r6.u64 = sp + 0x90; frame.ctx.r7.u64 = 0;
  write(sp + 0x90, 0);
  sub_828B0078(frame.ctx, base);
  const auto scene = read(sp + 0x90);
  if (!frame.ctx.r3.u32 || !Readable(memory, scene, 0x170)) {
    REXLOG_WARN("Original confirmation scene could not open"); return;
  }
  write(action + 0x108, scene);
  frame.ctx.r3.u64 = action; frame.ctx.r4.u64 = scene;
  sub_8237E6E0(frame.ctx, base); // original SetupScene binds OnBoxClosed / fade delegates
  set_text(scene, "SetTitle", open_coop ? "PC Co-op Connection" : show_coop_notice ? "PC Co-op" : open_pc ? "PC Display" : open_unlock ? "Unlock Weapons?" : show_unlock_result ? "Unlock Weapons" : "Exit Sierra Romeo?");
  set_text(scene, "SetMessage", show_coop_notice ? coop_notice.c_str() : (open_pc || open_coop) ? "" : open_unlock ?
      "Are you sure? This cheat grants all weapons, including bonus weapons, and saves them to your local profile." :
      show_unlock_result ? (unlock_succeeded ? "Weapons unlocked for your local profile." :
          "Could not unlock weapons. Please return to the main menu and try again.") : "Return to the desktop?");
  if (open_pc || open_coop) set_text(scene, "SetButtonLabelDatastoreBinding", "");
  owned_action = action; owned_scene = scene; owned_index = read(action + 4);
  owned_kind = open_coop ? Dialog::CoopSetup : show_coop_notice ? Dialog::CoopNotice : open_pc ? Dialog::Settings : open_unlock ? Dialog::Unlock : show_unlock_result ? Dialog::UnlockResult : Dialog::Exit;
  owned_player = read(top + 0x164);
  if (open_pc) aot::BeginPcSettings();
  if (open_coop) aot::BeginCoopConnection();
  setup_rollback.pending=false;
  result_received = accepted = false;
  main_menu_active = false;
  frontend_exit_active = false;
  REXLOG_INFO("Original {} dialog opened: action={:08X}, scene={:08X}, setup_ms={}", open_coop ? "PC Co-op Connection" : show_coop_notice ? "PC Co-op" : open_pc ? "PC Display" : open_unlock ? "Unlock Weapons" : show_unlock_result ? "Unlock Result" : "Exit", action, scene, Milliseconds() - opened_at);
}

bool AotOpenPcSettings(PPCRegister& action) {
  if (!options_active || Milliseconds() - menu_seen_ms >= 250) return false;
  auto* memory = rex::runtime::ThreadState::Get()->memory();
  if (!Readable(memory, action.u32, 0xE8)) return false;
  auto* flags = memory->TranslateVirtual<rex::be<uint32_t>*>(action.u32 + 0xE4);
  *flags = (static_cast<uint32_t>(*flags) & ~0x60000000u) | 0x40000000u;
  aot::RequestPcSettings();
  return true;
}

bool AotMenuInput(PPCRegister& user, PPCRegister& output) {
  if (!runtime || user.u32 != 0) return false;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, output.u32, sizeof(rex::input::X_INPUT_STATE))) return false;
  rex::CallFrame frame(*thread->context());
  frame.ctx.r3 = user; frame.ctx.r4.u64 = 1; frame.ctx.r5 = output;
  __imp__XamInputGetState(frame.ctx, memory->virtual_membase());
  user = frame.ctx.r3;
  if (user.u32 != 0) return true;
  auto* state = memory->TranslateVirtual<rex::input::X_INPUT_STATE*>(output.u32);
  std::lock_guard lock(menu_input_mutex);
  const bool main = frontend_exit_active && Milliseconds() - menu_seen_ms < 250;
  if (exit_policy.Sample(main, state->gamepad.buttons)) {
    exit_open_requested = true;
    REXLOG_INFO("Front-end Start pressed: opening original Exit confirmation");
  }
  state->gamepad.buttons = state->gamepad.buttons & ~exit_policy.blocked;
  // The native confirmation scene handles A/B and consumes those events itself.
  if (main || exit_open_requested) state->gamepad.buttons = state->gamepad.buttons & ~0x0010;
  aot::FilterPcSettingsInput(*state);
  aot::FilterCoopConnectionInput(*state);
  return true;
}

void AotMovieTick(PPCRegister& movie) {
  if (!runtime) return;
  // The startup/loading movie player can tick on different guest threads.
  std::lock_guard movie_lock(movie_mutex);
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  bool active = Readable(memory, movie.u32, 0xBC) && read(movie.u32 + 0x58) && read(movie.u32 + 0xB4);
  if (active) aot::ObserveFullscreenMovie();
  static uint32_t previous_handle = 0;
  static int64_t hold_start = 0;
  static bool latched = false;
  rex::input::X_INPUT_STATE state{};
  auto* input = dynamic_cast<rex::input::InputSystem*>(runtime->input_system());
  const bool connected = input && input->GetState(0, &state) == 0;
  const bool held = connected && (state.gamepad.buttons & 0x2000);
  if (!held) { hold_start = 0; latched = false; skip_progress = 0; }
  if (!active) { hold_start = 0; skip_progress = 0; movie_seen_ms = 0; return; }
  std::string name;
  // +0x34 is the currently playing clip, +0x28 is the requested sequence name
  // and stays EALogo throughout the startup playlist.
  const auto name_pointer = read(movie.u32 + 0x34), name_count = read(movie.u32 + 0x38);
  if (name_count && name_count < 256 && Readable(memory, name_pointer, name_count))
    name.assign(memory->TranslateVirtual<const char*>(name_pointer), name_count - 1);
  const auto handle = read(movie.u32 + 0x58);
  if (previous_handle != handle) {
    previous_handle = handle;
    hold_start = 0;
    movie_input_ms = 0;
    REXLOG_INFO("Movie player active: object={:08X}, handle={:08X}, name={}, playlist={}/{}, stop_flag={}",
        movie.u32, handle, name, static_cast<int32_t>(read(movie.u32 + 0x7C)),
        read(movie.u32 + 0x74), read(movie.u32 + 0x80));
  }
  const bool can_stop = aot::CanStopMovieImmediately(
      static_cast<int32_t>(read(movie.u32 + 0x7C)),
      static_cast<int32_t>(read(movie.u32 + 0x74)), read(movie.u32 + 0x80) != 0);
  if (!aot::IsSkippableMovie(name) || !can_stop) {
    hold_start = 0; skip_progress = 0; movie_seen_ms = 0; movie_input_ms = 0; return;
  }
  const auto now = Milliseconds();
  movie_seen_ms = now;
  if (connected && (state.gamepad.buttons || state.gamepad.left_trigger > 30 || state.gamepad.right_trigger > 30)) movie_input_ms = now;
  if (!held || latched) return;
  if (!hold_start) hold_start = now;
  const float progress = std::min(1.f, float(now - hold_start) / 1200.f);
  skip_progress = progress;
  if (progress < 1) return;
  latched = true;
  rex::CallFrame frame(*thread->context());
  frame.ctx.r1.u32 -= 0x100;
  *memory->TranslateVirtual<rex::be<uint32_t>*>(frame.ctx.r1.u32) = thread->context()->r1.u32;
  frame.ctx.r3.u64 = movie.u32;
  sub_824B5E18(frame.ctx, memory->virtual_membase());
  movie_seen_ms = 0;
  movie_input_ms = 0;
  skip_progress = 0;
  REXLOG_INFO("Hold B: requested original movie stop (result {})", frame.ctx.r3.u32);
}
void AotSubtitleBackground(PPCRegister& stack, PPCRegister& canvas,
    PPCRegister& color, PPCRegister& center_x, PPCRegister& y) {
  if (!REXCVAR_GET(aot_subtitle_background)) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, stack.u32, 0xD0) || !Readable(memory, color.u32, 16)) return;
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const uint32_t caller = read(stack.u32 + 0xC8);
  if (caller < 0x82870418 || caller >= 0x82870FF0) return;
  // The original font measurement has just completed. These are the actual
  // line bounds used by DrawCenteredString, before its scratch values change.
  const int32_t width = static_cast<int32_t>(read(stack.u32 + 0x80));
  const int32_t height = static_cast<int32_t>(read(stack.u32 + 0x88));
  const float alpha = *memory->TranslateVirtual<rex::be<float>*>(color.u32 + 12);
  if (width <= 0 || width > 4096 || height <= 0 || height > 256 || !(alpha > 0.f && alpha <= 1.f)) return;
  const uint32_t sp = stack.u32 - 0x100;
  if (!Readable(memory, sp - 0x1000, 0x1100)) return;
  rex::CallFrame frame(*thread->context());
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  write(sp, stack.u32);
  auto* background = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  background[0] = 0.f; background[1] = 0.f; background[2] = 0.f;
  background[3] = alpha * static_cast<float>(REXCVAR_GET(aot_subtitle_background_opacity));
  // Original canvas DrawTile: no texture selects its built-in white texture;
  // the final argument enables alpha blending. No game asset is changed.
  write(sp + 0x5C, sp + 0x80);
  write(sp + 0x64, 0);
  write(sp + 0x6C, 1);
  frame.ctx.r1.u64 = sp;
  frame.ctx.r3 = canvas;
  frame.ctx.f1.f64 = center_x.f64 - width / 2 - 6;
  // Retail multiline subtitles advance by 24 pixels for a 23-pixel font.
  // Vertical padding crossed that line cell and blended black twice at joins.
  frame.ctx.f2.f64 = y.f64;
  frame.ctx.f3.f64 = width + 12;
  frame.ctx.f4.f64 = height;
  frame.ctx.f5.f64 = 0;
  frame.ctx.f6.f64 = 0;
  frame.ctx.f7.f64 = 1;
  frame.ctx.f8.f64 = 1;
  sub_82641CB8(frame.ctx, memory->virtual_membase());
  static std::atomic<unsigned> reported{0};
  const unsigned layout_mode = caller == 0x82870FE0 ? 2u : 1u;
  if (!(reported.fetch_or(layout_mode) & layout_mode))
    REXLOG_INFO("Subtitle background drawn: line={}x{}, opacity={}, caller={:08X}",
        width, height, REXCVAR_GET(aot_subtitle_background_opacity), caller);
}
void AotSubtitleShadow(PPCRegister& stack, PPCRegister& canvas, PPCRegister& text,
    PPCRegister& font, PPCRegister& color, PPCRegister& x, PPCRegister& y,
    PPCRegister& scale_x, PPCRegister& scale_y) {
  if (!REXCVAR_GET(aot_subtitle_shadow)) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, stack.u32 + 0xC8, 4) || !Readable(memory, color.u32, 16)) return;
  const uint32_t caller = *memory->TranslateVirtual<rex::be<uint32_t>*>(stack.u32 + 0xC8);
  // DrawCenteredString is shared with other UI. Its saved return address must
  // belong to the original subtitle layout routine before we add a shadow.
  if (caller < 0x82870418 || caller >= 0x82870FF0) return;
  const float alpha = *memory->TranslateVirtual<rex::be<float>*>(color.u32 + 12);
  if (!(alpha > 0.f && alpha <= 1.f)) return;
  const uint32_t sp = stack.u32 - 0x100;
  if (!Readable(memory, sp - 0x1000, 0x1100)) return;
  rex::CallFrame frame(*thread->context());
  auto* base = memory->virtual_membase();
  *memory->TranslateVirtual<rex::be<uint32_t>*>(sp) = stack.u32;
  auto* shadow = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  shadow[0] = 0.f; shadow[1] = 0.f; shadow[2] = 0.f; shadow[3] = alpha * .7f;
  frame.ctx.r1.u64 = sp;
  frame.ctx.r3 = canvas;
  frame.ctx.r6 = text;
  frame.ctx.r7 = font;
  frame.ctx.r8.u64 = sp + 0x80;
  frame.ctx.f1.f64 = x.f64 + 2.0;
  frame.ctx.f2.f64 = y.f64 + 2.0;
  frame.ctx.f3 = scale_x;
  frame.ctx.f4 = scale_y;
  sub_82642158(frame.ctx, base);
  static std::atomic<unsigned> reported{0};
  const unsigned layout_mode = caller == 0x82870FE0 ? 2u : 1u;
  if (!(reported.fetch_or(layout_mode) & layout_mode))
    REXLOG_INFO("Original subtitle shadow drawn: font={:08X}, alpha={}, caller={:08X}", font.u32, alpha * .7f, caller);
}
namespace aot {
rex::ui::FrameStats GetFrameStats() {
  std::lock_guard lock(stats_mutex);
  return stats;
}
}

void AotGameFrame() {
  aot::FrameDiagnostics diagnostics(phase_timing.is_open() ? &phase_timing : nullptr);
  aot::FinishWaitInterval();
  SCOPE_profile_cpu_f("AOT frame hook");
  aot::ObserveMouseGameFrame();
#ifdef REXGLUE_ENABLE_PROFILING
  if (TracyIsStarted) { FrameMarkNamed("AOT game frames"); }
#endif
  {
    SCOPE_profile_cpu_f("AOT game commands");
    RunGameCommands();
  }
  diagnostics.CommandsDone();
  auto now = Clock::now();
  if (REXCVAR_GET(aot_fps) == 60) {
    SCOPE_profile_cpu_f("AOT frame pacing");
    if (next_frame != Clock::time_point{} && now < next_frame)
      WaitForFrameDeadline(next_frame);
    now = Clock::now();
    constexpr auto step = std::chrono::nanoseconds(16666667);
    next_frame = next_frame == Clock::time_point{} || now - next_frame > step
        ? now + step : next_frame + step;
  } else next_frame = {};
  diagnostics.PacingDone();
  const double ms = last_frame == Clock::time_point{} ? 0 :
      std::chrono::duration<double, std::milli>(now - last_frame).count();
  last_frame = now;
  std::lock_guard lock(stats_mutex);
  ++stats.frame_count;
  diagnostics.Frame(stats.frame_count);
  if (ms > 0) {
    stats.frame_time_ms = stats.frame_time_ms == 0 ? ms : stats.frame_time_ms * .95 + ms * .05;
    stats.fps = 1000.0 / stats.frame_time_ms;
    if (timing) {
      SCOPE_profile_cpu_f("AOT frame log");
      timing << stats.frame_count << ',' << ms << '\n';
      if (stats.frame_count % 60 == 0) timing.flush();
    }
  }
  aot::StartWaitInterval(stats.frame_count);
}

void AotMouseIntent(PPCRegister& stack, PPCRegister& input, PPCRegister& delta) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, input.u32, 0x180) || !Readable(memory, stack.u32, 0x120)) return;
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  if (read(input.u32) != 0x820441D8) return;
  const auto controller = read(input.u32 + 0x28);
  if (!Readable(memory, controller, 0x314)) return;
  const auto player = read(controller + 0x310);
  if (!Readable(memory, player, 0x44) || read(player) != 0x820BDF90 || read(player + 0x40) != controller) return;
  // Checkpoint travel replaces the controller without necessarily issuing a
  // guest ConsoleCommand. Follow the verified live player-input owner.
  console_actor = controller;
  static uint64_t pitch_name = 0, yaw_name = 0;
  if (!pitch_name) {
    const uint32_t sp = stack.u32 - 0x300;
    if (!Readable(memory, sp - 0x1000, 0x1300)) return;
    rex::CallFrame frame(*thread->context()); frame.ctx.r1.u64 = sp;
    *memory->TranslateVirtual<rex::be<uint32_t>*>(sp) = stack.u32;
    auto name = [&](const char* text) {
      std::strcpy(memory->TranslateVirtual<char*>(sp + 0x100), text);
      frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = sp + 0x100;
      frame.ctx.r5.u64 = 1; frame.ctx.r6.u64 = 1;
      sub_824340D0(frame.ctx, memory->virtual_membase());
      return static_cast<uint64_t>(*memory->TranslateVirtual<rex::be<uint64_t>*>(sp + 0x80));
    };
    pitch_name = name("AimingVelocity_Pitch"); yaw_name = name("AimingVelocity_Yaw");
    REXLOG_INFO("Mouse intention names: pitch={:016X}, yaw={:016X}", pitch_name, yaw_name);
  }
  const uint64_t a = *memory->TranslateVirtual<rex::be<uint64_t>*>(stack.u32 + 0x110);
  const uint64_t b = *memory->TranslateVirtual<rex::be<uint64_t>*>(stack.u32 + 0x118);
  if (a != pitch_name && a != yaw_name && b != pitch_name && b != yaw_name) return;
  aot::ObserveMouseGameplay();
  const uint32_t frame_count = read(input.u32 + 0x140);
  const auto mouse = aot::MouseFrame(frame_count);
  if (delta.f64 >= .001 && delta.f64 <= .25) {
    // This hook runs after the game's analog modifiers have integrated the
    // stick velocity. The camera consumes the result as angular displacement,
    // so dividing a mouse displacement by dt makes sensitivity depend on FPS.
    // Preserve the previous sensitivity at its observed 16 ms reference tick.
    constexpr float mouse_gain = 1.0f / .016f;
    auto add = [&](uint64_t name, uint32_t address) {
      auto* value = memory->TranslateVirtual<rex::be<float>*>(address);
      if (name == yaw_name) *value = static_cast<float>(*value) + mouse.first * mouse_gain;
      if (name == pitch_name) *value = static_cast<float>(*value) - mouse.second * mouse_gain;
    };
    add(a, stack.u32 + 0x60); add(b, stack.u32 + 0x64);
  }
  if (!std::getenv("AOT_TRACE_MOUSE")) return;
  static int64_t previous = 0;
  if (Milliseconds() - previous < 500) return;
  previous = Milliseconds();
  const float x = *memory->TranslateVirtual<rex::be<float>*>(stack.u32 + 0x60);
  const float y = *memory->TranslateVirtual<rex::be<float>*>(stack.u32 + 0x64);
  REXLOG_INFO("Mouse intention trace: input={:08X}, frame={}, dt={}, names={:016X}/{:016X}, value={}/{}, caller={:08X}", input.u32, frame_count, delta.f64, a, b, x, y, read(stack.u32 + 0xE8));
}

// AO2HudItem_WheelMenu::Draw, after both original material layers and before
// the caption. These are the native wheel bounds, not a screen-space estimate.
void AotHudWheel(PPCRegister& stack, PPCRegister& wheel, PPCRegister& canvas,
    PPCRegister& x, PPCRegister& y, PPCRegister& size) {
  if (!aot::KeyboardPrompts()) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  if (!Readable(memory, wheel.u32, 0x125) || read(wheel.u32) != 0x8204C320 ||
      !Readable(memory, canvas.u32, 0x7C)) return;
  const auto target = read(canvas.u32 + 0x74);
  const float alpha = *memory->TranslateVirtual<rex::be<float>*>(wheel.u32 + 0x70);
  if (!Readable(memory, target, 0x38) || !std::isfinite(alpha) || alpha <= 0 || alpha > 1 ||
      !std::isfinite(x.f64) || !std::isfinite(y.f64) || size.f64 != 128) return;
  const uint32_t sp = stack.u32 - 0x500;
  if (!Readable(memory, sp - 0x2000, 0x2500)) return;
  rex::CallFrame frame(*thread->context());
  frame.ctx.r1.u64 = sp; write(sp, stack.u32);
  auto* color = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  write(sp + 0x5C, sp + 0x80); write(sp + 0x64, 0); write(sp + 0x6C, 1);
  auto rectangle = [&](float left, float top, float width, float height) {
    frame.ctx.r3.u64 = target;
    frame.ctx.f1.f64 = left; frame.ctx.f2.f64 = top;
    frame.ctx.f3.f64 = width; frame.ctx.f4.f64 = height;
    frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
    frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
    sub_82641CB8(frame.ctx, memory->virtual_membase());
  };
  struct Key { unsigned char code; float x, y; };
  // Preserve all action artwork and highlights. The caption moves down 24 px
  // below the bottom key; the original caption renderer still supplies its font.
  constexpr Key keys[] = {{109, 54, -24}, {115, -24, 54}, {111, 132, 54}, {113, 54, 132}};
  const float left = static_cast<int>(x.f64), top = static_cast<int>(y.f64);
  for (const auto& key : keys) {
    const aot::KeyboardGlyph* shape = nullptr;
    for (const auto& item : aot::keyboard_glyphs) if (item.code == key.code) { shape = &item; break; }
    if (!shape) continue;
    color[0] = color[1] = color[2] = 0; color[3] = alpha * .65f;
    rectangle(left + key.x - 2, top + key.y - 2, 24, 24);
    color[0] = color[1] = color[2] = 1;
    for (unsigned i = 0; i < shape->count; ++i) {
      const auto& r = aot::keyboard_rects[shape->offset + i];
      color[3] = alpha * r.alpha / 255.f;
      rectangle(left + key.x + 20.f * r.x / shape->width,
          top + key.y + 20.f * r.y / shape->height,
          20.f * r.width / shape->width, 20.f * r.height / shape->height);
    }
  }
  static const bool trace = std::getenv("AOT_TRACE_HUD") != nullptr;
  if (trace) {
    static thread_local unsigned records = 0;
    static thread_local uint64_t previous = 0;
    const auto mode = (uint64_t(wheel.u32) << 32) | (read(wheel.u32 + 0x120) & 0xC0000000) |
        *memory->TranslateVirtual<uint8_t*>(wheel.u32 + 0x124);
    if (mode != previous && records < 32) {
      ++records;
      previous = mode;
      REXLOG_INFO("Keyboard wheel: object={:08X}, canvas={:08X}, xy={}/{}, alpha={}, menu_type={}",
          wheel.u32, target, left, top, alpha, unsigned(*memory->TranslateVirtual<uint8_t*>(wheel.u32 + 0x124)));
    }
  }
  y.f64 += 24;
}

namespace {
std::atomic<uint32_t> traced_coop_texture_rhi{0};
std::atomic<uint32_t> traced_coop_texture_owner{0};
bool ReplaceCoopTexture() {
  return REXCVAR_GET(aot_keyboard_coop_prompt);
}
bool TraceCoopTexture() {
  static const bool enabled = [] {
    const char* value = std::getenv("AOT_TRACE_COOP_TEXTURE");
    return value && std::string_view(value) == "1";
  }();
  return enabled;
}

uint32_t CreateCoopKeyboardTexture() {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  const aot::KeyboardGlyph* glyph = nullptr;
  for (const auto& item : aot::keyboard_glyphs) if (item.code == 'B') { glyph = &item; break; }
  if (!glyph) return 0;
  const uint32_t parent = thread->context()->r1.u32, sp = parent - 0x800;
  if (!Readable(memory, sp - 0x2000, 0x2800)) return 0;
  rex::CallFrame frame(*thread->context());
  frame.ctx.r1.u64 = sp; write(sp, parent); write(sp + 0x80, 0);
  frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = frame.ctx.r5.u64 = 32;
  // Retail GPixelFormats[2] is A8R8G8B8; one mip, ordinary tiled texture.
  frame.ctx.r6.u64 = 2; frame.ctx.r7.u64 = 1; frame.ctx.r8.u64 = 0;
  frame.ctx.r9.u64 = frame.ctx.r10.u64 = 0;
  sub_829D9710(frame.ctx, memory->virtual_membase());
  const uint32_t rhi = read(sp + 0x80);
  if (!rhi) return 0;
  const auto release = [&] {
    frame.ctx.r3.u64 = sp + 0x80; frame.ctx.r4.u64 = 0;
    sub_824C42F0(frame.ctx, memory->virtual_membase());
  };
  if (!Readable(memory, rhi, 0x18)) { release(); return 0; }
  const uint32_t d3d = read(rhi + 8);
  if (!Readable(memory, d3d, 0x34)) { release(); return 0; }
  const uint32_t w0 = read(d3d + 0x1C), w1 = read(d3d + 0x20), w2 = read(d3d + 0x24);
  REXLOG_INFO("Coop keyboard texture created: rhi={:08X}, descriptor={:08X} {:08X} {:08X} {:08X} {:08X} {:08X}",
      rhi, w0, w1, w2, read(d3d + 0x28), read(d3d + 0x2C), read(d3d + 0x30));
  if (!(w0 & 0x80000000) || (w0 & 3) != 2 || ((w0 >> 22) & 511) != 1 ||
      (w1 & 63) != 6 || ((w1 >> 6) & 3) != 2 ||
      (w2 & 8191) != 31 || ((w2 >> 13) & 8191) != 31) {
    REXLOG_WARN("Coop keyboard texture has unsupported layout; retaining original button");
    release(); return 0;
  }
  write(sp + 0x84, 0);
  frame.ctx.r3.u64 = rhi; frame.ctx.r4.u64 = 0; frame.ctx.r5.u64 = 1;
  frame.ctx.r6.u64 = sp + 0x84; frame.ctx.r7.u64 = 1;
  sub_829D5278(frame.ctx, memory->virtual_membase());
  const uint32_t data = frame.ctx.r3.u32;
  const bool valid = read(sp + 0x84) == 128 && Readable(memory, data, 4096);
  if (valid) {
    for (uint32_t y = 0; y < 32; ++y) for (uint32_t x = 0; x < 32; ++x) {
      uint32_t intensity = 0;
      // The material deliberately samples outside [0,1]. Black edge texels
      // prevent clamp addressing from extending the white key across the icon.
      if (x >= 3 && x < 29 && y >= 3 && y < 29) {
        const uint32_t gx = (2 * (x - 3) + 1) * glyph->width / 52;
        const uint32_t gy = (2 * (y - 3) + 1) * glyph->height / 52;
        for (unsigned i = 0; i < glyph->count; ++i) {
          const auto& r = aot::keyboard_rects[glyph->offset + i];
          if (gx >= r.x && gx < r.x + r.width && gy >= r.y && gy < r.y + r.height) {
            intensity = r.alpha; break;
          }
        }
      }
      // Xenos GetTiledOffset2D specialized to a 32px pitch and four-byte pixels.
      const uint32_t micro = ((x & 7) + ((y & 14) << 2)) << 2;
      const uint32_t offset = ((micro & ~15u) << 1) + (micro & 15) + ((y & 1) << 4);
      const uint32_t tiled = ((offset & ~511u) << 3) + ((y & 16) << 7) +
          ((offset & 448) << 2) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6) + (offset & 63);
      write(data + tiled, 0xFF000000 | intensity * 0x010101);
    }
  }
  frame.ctx.r3.u64 = rhi; frame.ctx.r4.u64 = frame.ctx.r5.u64 = 0;
  sub_829D5330(frame.ctx, memory->virtual_membase());
  if (!valid) { release(); return 0; }
  // Retain the creation reference for this runtime; binding does not transfer it.
  return rhi;
}
}

void AotCoopTextureResource(PPCRegister& resource_register) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const uint32_t resource = resource_register.u32;
  if (!Readable(memory, resource, 0x2C) || read(resource) != 0x82081C1C) return;
  const uint32_t texture = read(resource + 0x28);
  if (!Readable(memory, texture, 0xD4) || read(texture) != 0x82081DF0 ||
      read(texture + 0xC8) != 32 || read(texture + 0xCC) != 32) return;
  const uint32_t index = read(texture + 0x2C), names = read(0x83101004);
  if (index >= read(0x83101008) || !Readable(memory, names + index * 4, 4)) return;
  const uint32_t name = read(names + index * 4);
  if (!Readable(memory, name, 0x20) || std::memcmp(memory->virtual_membase() + name + 0x10, "coop_call_xb", 13) != 0) return;
  if (read(texture + 0xB8) != resource) return;
  const uint32_t rhi = read(resource + 0x14);
  if (!Readable(memory, rhi, 0x18)) return;
  traced_coop_texture_owner = texture;
  traced_coop_texture_rhi = rhi;
  static thread_local unsigned count = 0, logged = 0;
  static thread_local int previous_mode = -1;
  const int mode = aot::KeyboardPrompts();
  if (TraceCoopTexture() && ++count && logged < 16 && (count <= 6 || mode != previous_mode)) {
    ++logged; previous_mode = mode;
    REXLOG_INFO("Coop texture resource trace: texture={:08X}, resource={:08X}, rhi={:08X}, d3d={:08X}, caller={:08X}, keyboard={}, sample={}",
        texture, resource, rhi, read(rhi + 8), thread->context()->lr, mode, count);
    const uint32_t d3d = read(rhi + 8);
    if (Readable(memory, d3d, 0x34))
      REXLOG_INFO("Coop texture descriptor trace: {:08X} {:08X} {:08X} {:08X} {:08X} {:08X}",
          read(d3d + 0x1C), read(d3d + 0x20), read(d3d + 0x24),
          read(d3d + 0x28), read(d3d + 0x2C), read(d3d + 0x30));
  }
}

void AotCoopTextureBinding(PPCRegister& slot, PPCRegister& sampler, PPCRegister& texture) {
  if (!texture.u32 || texture.u32 != traced_coop_texture_rhi.load()) return;
  static thread_local unsigned count = 0, logged = 0;
  static thread_local int previous_mode = -1;
  const int mode = aot::KeyboardPrompts();
  if (TraceCoopTexture() && ++count && logged < 16 && (count <= 6 || mode != previous_mode)) {
    ++logged; previous_mode = mode;
    REXLOG_INFO("Coop texture binding trace: slot={}, sampler={:08X}, rhi={:08X}, caller={:08X}, keyboard={}, sample={}",
        slot.u32, sampler.u32, texture.u32, rex::runtime::ThreadState::Get()->context()->lr, mode, count);
  }
  if (!ReplaceCoopTexture() || !mode || slot.u32 != 2 ||
      rex::runtime::ThreadState::Get()->context()->lr != 0x824F4480) return;
  auto* memory = rex::runtime::ThreadState::Get()->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const uint32_t owner = traced_coop_texture_owner.load();
  if (!Readable(memory, owner, 0xD4) || read(owner) != 0x82081DF0) return;
  const uint32_t resource = read(owner + 0xB8);
  if (!Readable(memory, resource, 0x2C) || read(resource) != 0x82081C1C ||
      read(resource + 0x28) != owner || read(resource + 0x14) != texture.u32) return;
  static thread_local bool attempted = false;
  static thread_local uint32_t replacement = 0;
  if (!attempted) {
    attempted = true;
    replacement = CreateCoopKeyboardTexture();
    REXLOG_INFO("Coop keyboard texture replacement ready: rhi={:08X}", replacement);
  }
  if (replacement) texture.u64 = replacement;
}

// The reload and ordinary action materials output only the controller button.
// Background nodes in the latter are disconnected; timing is a separate material.
bool AotKeyboardActionMaterial(PPCRegister& canvas, PPCRegister& material,
    PPCRegister& action, PPCRegister& x, PPCRegister& y, PPCRegister& width,
    PPCRegister& height, PPCRegister& u, PPCRegister& v, PPCRegister& us,
    PPCRegister& vs) {
  auto* thread = rex::runtime::ThreadState::Get();
  if (thread->context()->lr != 0x823A13F8 || !aot::KeyboardPrompts()) return false;
  auto* memory = thread->memory();
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  if (!Readable(memory, action.u32, 0xAC) || read(action.u32) != 0x82047B78 ||
      !material.u32 || material.u32 != read(action.u32 + 0xA8) ||
      !Readable(memory, material.u32, 4) || read(material.u32) != 0x8207A830 ||
      !Readable(memory, canvas.u32, 0x78) ||
      !std::isfinite(x.f64) || !std::isfinite(y.f64) ||
      std::abs(x.f64) > 8192 || std::abs(y.f64) > 8192 ||
      width.f64 != 128 || height.f64 != 128 ||
      u.f64 != 0 || v.f64 != 0 || us.f64 != 1 || vs.f64 != 1) return false;
  const bool reload = material.u32 == read(action.u32 + 0xA0);
  if (!reload && material.u32 != read(action.u32 + 0x9C)) return false;
  const auto target = read(canvas.u32 + 0x74);
  if (!Readable(memory, target, 0x38)) return false;
  // The update at 823AEEB0 supplies this float to the material's Opacity.
  // UCanvas DrawColor is separately quantized for the caption, not this draw.
  const float alpha = *memory->TranslateVirtual<rex::be<float>*>(action.u32 + 0x70);
  if (!std::isfinite(alpha) || alpha < 0 || alpha > 1) return false;
  const aot::KeyboardGlyph* shape = nullptr;
  for (const auto& glyph : aot::keyboard_glyphs) if (glyph.code == (reload ? 'X' : 'A')) { shape = &glyph; break; }
  if (!shape) return false;
  const uint32_t parent = thread->context()->r1.u32, sp = parent - 0x500;
  if (!Readable(memory, sp - 0x2000, 0x2500)) return false;
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  rex::CallFrame frame(*thread->context());
  frame.ctx.r1.u64 = sp; write(sp, parent);
  auto* color = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  color[0] = color[1] = color[2] = 1;
  write(sp + 0x5C, sp + 0x80); write(sp + 0x64, 0); write(sp + 0x6C, 1);
  // The original 128px texture centers its button at (64,64).
  const float left = static_cast<int>(x.f64) + 44.f;
  const float top = static_cast<int>(y.f64) + 44.f;
  // Keep the white key legible over bright scenery, with the same action fade.
  color[0] = color[1] = color[2] = 0;
  color[3] = alpha * .6f;
  frame.ctx.r3.u64 = target;
  frame.ctx.f1.f64 = left; frame.ctx.f2.f64 = top;
  frame.ctx.f3.f64 = frame.ctx.f4.f64 = 40;
  frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
  frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
  sub_82641CB8(frame.ctx, memory->virtual_membase());
  color[0] = color[1] = color[2] = 1;
  for (unsigned i = 0; i < shape->count; ++i) {
    const auto& r = aot::keyboard_rects[shape->offset + i];
    color[3] = alpha * r.alpha / 255.f;
    frame.ctx.r3.u64 = target;
    frame.ctx.f1.f64 = left + 40.f * r.x / shape->width;
    frame.ctx.f2.f64 = top + 40.f * r.y / shape->height;
    frame.ctx.f3.f64 = 40.f * r.width / shape->width;
    frame.ctx.f4.f64 = 40.f * r.height / shape->height;
    frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
    frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
    sub_82641CB8(frame.ctx, memory->virtual_membase());
  }
  static thread_local unsigned logged = 0;
  const unsigned bit = reload ? 1 : 2;
  if (!(logged & bit)) {
    logged |= bit;
    REXLOG_INFO("Keyboard {} material replaced: action={:08X}, material={:08X}, xy={}/{}",
                reload ? "reload" : "action", action.u32, material.u32, left, top);
  }
  return true;
}

// Preserve only the original metallic rim. The interior of every edge/corner
// tile is discarded so the shared canvas fill contributes opacity exactly once.
bool AotCheckpointCorner(PPCRegister& stack, PPCRegister& canvas,
    PPCRegister& x, PPCRegister& y, PPCRegister& w, PPCRegister& h,
    PPCRegister& u, PPCRegister& v, PPCRegister& uw, PPCRegister& vh) {
  static thread_local bool clipping = false;
  const auto root = checkpoint_root_image.load();
  if (clipping || !root) return false;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, root, 0x200) || !Readable(memory, stack.u32, 0x80)) return false;
  const auto* bounds = memory->TranslateVirtual<rex::be<float>*>(root + 0x1F0);
  const double corner = (float(bounds[3]) - float(bounds[1])) * .6;
  if (!(corner > 4 && corner < 60)) return false;
  const double panel_left = float(bounds[0]) - corner, panel_right = float(bounds[2]) + corner;
  const double panel_top = float(bounds[1]) - corner, panel_bottom = float(bounds[3]) + corner;
  const auto close = [](double a, double b) { return std::abs(a - b) < .2; };
  const bool left = close(x.f64, panel_left) && close(w.f64, corner);
  const bool right = close(x.f64, float(bounds[2])) && close(w.f64, corner);
  const bool top = close(y.f64, panel_top) && close(h.f64, corner);
  const bool bottom = close(y.f64, float(bounds[3])) && close(h.f64, corner);
  const bool middle_x = close(x.f64, float(bounds[0])) && close(w.f64, float(bounds[2]) - float(bounds[0]));
  const bool middle_y = close(y.f64, float(bounds[1])) && close(h.f64, float(bounds[3]) - float(bounds[1]));
  if (!((left || right) && (top || bottom || middle_y)) &&
      !(middle_x && (top || bottom))) return false;
  const auto texture = *memory->TranslateVirtual<rex::be<uint32_t>*>(stack.u32 + 0x64);
  if (!texture) return false;
  const uint32_t sp = stack.u32 - 0x200;
  if (!Readable(memory, sp - 0x1000, 0x1200)) return false;
  auto* base = memory->virtual_membase();
  std::memcpy(base + sp, base + stack.u32, 0x80);
  *memory->TranslateVirtual<rex::be<uint32_t>*>(sp) = stack.u32;
  rex::CallFrame frame(*thread->context());
  clipping = true;
  struct Reset { bool& flag; ~Reset() { flag = false; } } reset{clipping};
  const double radius = corner * .8, rim = 3;
  auto inset_at = [](double row, double top_y, double bottom_y, double radius) {
    const double edge = std::min(row - top_y, bottom_y - row);
    return edge < radius ? radius - std::sqrt(std::max(0.0,
        radius * radius - (radius - edge) * (radius - edge))) : 0;
  };
  for (double row = 0; row < h.f64; row += .5) {
    const double height = std::min(.5, h.f64 - row), sample = y.f64 + row + height / 2;
    const double outer = inset_at(sample, panel_top, panel_bottom, radius);
    const double start = std::max(x.f64, panel_left + outer);
    const double end = std::min(x.f64 + w.f64, panel_right - outer);
    auto segment = [&](double begin, double finish) {
      if (!(finish > begin)) return;
      frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
      frame.ctx.f1.f64 = begin; frame.ctx.f2.f64 = y.f64 + row;
      frame.ctx.f3.f64 = finish - begin; frame.ctx.f4.f64 = height;
      frame.ctx.f5.f64 = u.f64 + uw.f64 * (begin - x.f64) / w.f64;
      frame.ctx.f6.f64 = v.f64 + vh.f64 * row / h.f64;
      frame.ctx.f7.f64 = uw.f64 * (finish - begin) / w.f64;
      frame.ctx.f8.f64 = vh.f64 * height / h.f64;
      sub_82641CB8(frame.ctx, base);
    };
    if (sample < panel_top + rim || sample >= panel_bottom - rim) segment(start, end);
    else {
      const double inner = inset_at(sample, panel_top + rim, panel_bottom - rim, radius - rim);
      segment(start, std::min(end, panel_left + rim + inner));
      segment(std::max(start, panel_right - rim - inner), end);
    }
  }
  static std::atomic<bool> logged{false};
  if (!logged.exchange(true)) REXLOG_INFO("Checkpoint original border rim clipping active; single interior fill");
  return true;
}

void AotEditionLabel(PPCRegister& canvas, PPCRegister& text, PPCRegister& font,
    PPCRegister& color, PPCRegister& x, PPCRegister& y,
    PPCRegister& scale_x, PPCRegister& scale_y) {
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, text.u32, 12) ||
      std::memcmp(memory->TranslateVirtual<const char*>(text.u32), "Press START", 12) ||
      !Readable(memory, color.u32, 16) || !Readable(memory, font.u32, 0x70)) return;
  const auto* source = memory->TranslateVirtual<rex::be<float>*>(color.u32);
  if (std::max({float(source[0]), float(source[1]), float(source[2])}) < .2f ||
      !(scale_x.f64 > 0 && scale_x.f64 <= 4 && scale_y.f64 > 0 && scale_y.f64 <= 4)) return;
  const uint32_t parent = thread->context()->r1.u32, sp = parent - 0x300;
  if (!Readable(memory, sp - 0x2000, 0x2300)) return;
  auto* base = memory->virtual_membase();
  const auto write = [&](uint32_t a, uint32_t b) { *memory->TranslateVirtual<rex::be<uint32_t>*>(a) = b; };
  write(sp, parent);
  std::strcpy(reinterpret_cast<char*>(base + sp + 0x100), "Sierra Romeo Edition");
  std::strcpy(reinterpret_cast<char*>(base + sp + 0x140), "%s");
  rex::CallFrame frame(*thread->context());
  auto measure = [&](uint32_t string) {
    frame.ctx.r1.u64 = sp; frame.ctx.r3 = font;
    frame.ctx.r4.u64 = sp + 0x90; frame.ctx.r5.u64 = sp + 0x98;
    frame.ctx.r6.u64 = sp + 0x140; frame.ctx.r7.u64 = string;
    sub_8260C280(frame.ctx, base);
    return int32_t(*memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x90));
  };
  auto advance = [&](const char* value, double scale, double fallback) {
    const uint32_t glyphs = *memory->TranslateVirtual<rex::be<uint32_t>*>(font.u32 + 0x3C);
    const uint32_t count = *memory->TranslateVirtual<rex::be<uint32_t>*>(font.u32 + 0x40);
    const uint32_t remapped = *memory->TranslateVirtual<rex::be<uint32_t>*>(font.u32 + 0x68);
    if (remapped || count < 128 || count > 256 || !Readable(memory, glyphs, count * 20)) return fallback;
    double result = 0;
    for (; *value; ++value) {
      unsigned ch = static_cast<unsigned char>(*value);
      if (ch >= count) ch = 127;
      const auto* glyph = memory->TranslateVirtual<rex::be<int32_t>*>(glyphs + ch * 20);
      result += std::trunc(int32_t(glyph[2]) * scale);
    }
    return result;
  };
  // The logo stripe is centered at x=666 in the authored 1280-wide scene;
  // the original CTA is offset to its right. Anchor to the artwork, not the
  // CTA's text width (its authored left edge is 570.5).
  const double center = x.f64 + 95.5 * scale_x.f64;
  const auto measured = measure(sp + 0x100);
  if (measured <= 0 || measured > 1024) return;
  const double scale = std::min(scale_x.f64, scale_y.f64) * .75;
  const double width = advance("Sierra Romeo Edition", scale, measured * scale);
  const double height = int32_t(*memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x98)) * scale;
  const double top = y.f64 - 58 * scale_y.f64;
  const double phase = std::chrono::duration<double>(Clock::now() - pc_features_started).count();
  const float pulse = float(.92 + .08 * std::sin(phase * 1.65));
  auto* shade = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  write(sp + 0x5C, sp + 0x80); write(sp + 0x64, 0); write(sp + 0x6C, 1);
  // Rounded dark plaque with a restrained light rim. Native canvas coordinates
  // track the real CTA, and drawing stops as soon as that CTA leaves the screen.
  auto rounded = [&](double left, double top_y, double rw, double rh, double radius) {
    for (double row = 0; row < rh; row += 1) {
      const double step = std::min(1.0, rh - row);
      const double edge = std::min(row + step / 2, rh - row - step / 2);
      const double inset = edge < radius ? radius - std::sqrt(std::max(0.0,
          radius * radius - (radius - edge) * (radius - edge))) : 0;
      frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
      frame.ctx.f1.f64 = left + inset; frame.ctx.f2.f64 = top_y + row;
      frame.ctx.f3.f64 = rw - 2 * inset; frame.ctx.f4.f64 = step;
      frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
      frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
      sub_82641CB8(frame.ctx, base);
    }
  };
  const float alpha = float(source[3]);
  shade[0] = shade[1] = shade[2] = .65f; shade[3] = alpha * .35f;
  rounded(center - width / 2 - 9, top - 3, width + 18, height + 6, 6);
  shade[0] = shade[1] = shade[2] = 0; shade[3] = alpha * .8f;
  rounded(center - width / 2 - 8, top - 2, width + 16, height + 4, 5);
  for (unsigned i = 0; i < 3; ++i) shade[i] = float(source[i]) * pulse;
  shade[3] = alpha;
  frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
  frame.ctx.r6.u64 = sp + 0x100; frame.ctx.r7 = font; frame.ctx.r8.u64 = sp + 0x80;
  frame.ctx.f1.f64 = center - width / 2; frame.ctx.f2.f64 = top;
  frame.ctx.f3.f64 = frame.ctx.f4.f64 = scale;
  sub_82642158(frame.ctx, base);
}

void AotHudTextContrast(PPCRegister& canvas, PPCRegister& text, PPCRegister& font,
    PPCRegister& color, PPCRegister& x, PPCRegister& y,
    PPCRegister& scale_x, PPCRegister& scale_y) {
  static thread_local bool decorating = false;
  if (decorating) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  const auto caller = thread->context()->lr;
  if (!Readable(memory, font.u32, 0x70)) return;
  const uint64_t font_name = *memory->TranslateVirtual<rex::be<uint64_t>*>(font.u32 + 0x2C);
  if (hud_readability_font_name && font_name == hud_readability_font_name) hud_readability_font = font.u32;
  // Native observations identify the gameplay caption and objective-list
  // call sites. Menu art, controller glyphs and existing subtitles have their
  // own layout/color paths and must not acquire duplicate decorations here.
  const bool checkpoint_title = caller == 0x828C9784 && checkpoint_root_image.load() &&
      Readable(memory, text.u32, 11) &&
      std::memcmp(memory->TranslateVirtual<const char*>(text.u32), "Checkpoint", 11) == 0;
  if (caller != 0x82642F78 && caller != 0x823A5990 && !checkpoint_title) return;
  if (!REXCVAR_GET(aot_hud_text_contrast) && !checkpoint_title) return;
  if (controller_font_name && font_name == controller_font_name) return;
  if (!Readable(memory, canvas.u32, 0x7C) || !Readable(memory, font.u32, 0x70) ||
      !Readable(memory, text.u32, 256) || !Readable(memory, color.u32, 16)) return;
  bool reward_caption = false;
  if (caller == 0x823A5990 && Readable(memory, thread->context()->r1.u32 + 596, 4)) {
    // The wrapping routine retains its complete input at frame +596. Currency
    // identifies objective/reward copy without re-boxing tutorial glyph runs.
    const auto input = static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(thread->context()->r1.u32 + 596));
    if (Readable(memory, input, 512)) {
      const auto* original = memory->TranslateVirtual<const char*>(input);
      for (size_t i = 0; i < 512 && original[i]; ++i) reward_caption |= original[i] == '$';
    }
  }
  const auto* string = memory->TranslateVirtual<const char*>(text.u32);
  size_t length = 0;
  for (; length < 256 && string[length]; ++length) {}
  if (!length || length == 256 || !std::isfinite(x.f64) || !std::isfinite(y.f64) ||
      !(scale_x.f64 > 0 && scale_x.f64 <= 4) || !(scale_y.f64 > 0 && scale_y.f64 <= 4)) return;
  const float alpha = *memory->TranslateVirtual<rex::be<float>*>(color.u32 + 12);
  if (!(alpha > 0 && alpha <= 1)) return;
  const uint32_t parent = thread->context()->r1.u32, sp = parent - 0x200;
  // String measurement uses a 4 KiB formatting frame. Pass a literal %s format
  // so percent signs in displayed text never become formatting directives.
  if (!Readable(memory, sp - 0x2000, 0x2200)) return;
  struct Guard { bool& flag; Guard(bool& value) : flag(value) { flag = true; } ~Guard() { flag = false; } } guard(decorating);
  rex::CallFrame frame(*thread->context());
  const auto write = [&](uint32_t address, uint32_t value) {
    *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value;
  };
  auto* base = memory->virtual_membase();
  write(sp, parent);
  std::memcpy(base + sp + 0xA0, "%s", 3);
  write(sp + 0x90, 0); write(sp + 0x98, 0);
  frame.ctx.r1.u64 = sp;
  frame.ctx.r3 = font; frame.ctx.r4.u64 = sp + 0x90; frame.ctx.r5.u64 = sp + 0x98;
  frame.ctx.r6.u64 = sp + 0xA0; frame.ctx.r7 = text;
  sub_8260C280(frame.ctx, base);
  const int32_t width = *memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x90);
  const int32_t height = *memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x98);
  if (width <= 0 || width > 4096 || height <= 0 || height > 256) return;
  double draw_width = width * scale_x.f64, draw_height = height * scale_y.f64;
  bool dark_font = false;
  for (const auto& name : dark_hud_font_names) dark_font |= name && font_name == name;
  if (dark_font) {
    // mp_med/mp_large include dark atlas ink that white vertex color cannot
    // brighten. mp_small uses the same light face for caption consistency.
    // Fit within the original extent with a single scale. Independent width
    // and height fits distorted every substituted word's letter proportions.
    const uint32_t replacement = hud_readability_font;
    if (!Readable(memory, replacement, 0x70) ||
        *memory->TranslateVirtual<rex::be<uint64_t>*>(replacement + 0x2C) != hud_readability_font_name) return;
    frame.ctx.r1.u64 = sp;
    frame.ctx.r3.u64 = replacement; frame.ctx.r4.u64 = sp + 0x90; frame.ctx.r5.u64 = sp + 0x98;
    frame.ctx.r6.u64 = sp + 0xA0; frame.ctx.r7 = text;
    sub_8260C280(frame.ctx, base);
    const int32_t replacement_width = *memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x90);
    const int32_t replacement_height = *memory->TranslateVirtual<rex::be<int32_t>*>(sp + 0x98);
    if (replacement_width <= 0 || replacement_width > 4096 || replacement_height <= 0 || replacement_height > 256) return;
    font.u64 = replacement;
    const double fit = std::min(draw_width / replacement_width, draw_height / replacement_height);
    scale_x.f64 = scale_y.f64 = fit;
    draw_width = replacement_width * fit;
    draw_height = replacement_height * fit;
  }
  // The retail draw truncates each scaled glyph advance separately, and
  // truncates the starting position to an integer. Scaling the whole measured
  // string instead makes the backing drift right, especially for small labels.
  double backing_x = std::trunc(x.f64), backing_y = std::trunc(y.f64);
  const auto font_word = [&](uint32_t offset) {
    return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(font.u32 + offset));
  };
  const auto glyphs = font_word(0x3C), glyph_count = font_word(0x40);
  if (!font_word(0x68) && glyph_count >= 128 && glyph_count <= 256 &&
      Readable(memory, glyphs, glyph_count * 20)) {
    double advance = 0, right = 0, bottom = 0, left = 4096, top_ink = 256;
    const bool known_ink = font.u32 == hud_readability_font;
    for (size_t i = 0; i < length; ++i) {
      unsigned ch = static_cast<unsigned char>(string[i]);
      if (ch >= glyph_count) ch = 127;
      auto* glyph = memory->TranslateVirtual<rex::be<int32_t>*>(glyphs + ch * 20);
      if (int32_t(glyph[3]) == 0) {
        ch = 127;
        glyph = memory->TranslateVirtual<rex::be<int32_t>*>(glyphs + ch * 20);
      }
      const double w = int32_t(glyph[2]) * scale_x.f64;
      const double h = int32_t(glyph[3]) * scale_y.f64;
      const auto& ink_box = aot::hud_ink_bounds[ch];
      if (known_ink && ink_box[2] > ink_box[0] && ink_box[3] > ink_box[1]) {
        left = std::min(left, advance + ink_box[0] * scale_x.f64);
        top_ink = std::min(top_ink, ink_box[1] * scale_y.f64);
        right = std::max(right, advance + ink_box[2] * scale_x.f64);
        bottom = std::max(bottom, ink_box[3] * scale_y.f64);
      } else if (!known_ink) {
        left = top_ink = 0;
        right = std::max(right, advance + w);
        bottom = std::max(bottom, h);
      }
      advance += std::trunc(w);
    }
    if (right > left && bottom > top_ink) {
      backing_x += left; backing_y += top_ink;
      draw_width = right - left; draw_height = bottom - top_ink;
    }
  }
  const auto* ink = memory->TranslateVirtual<rex::be<float>*>(color.u32);
  // Do not put another backing/shadow behind an original dark shadow pass.
  if (std::max({float(ink[0]), float(ink[1]), float(ink[2])}) < .2f) return;
  auto* shade = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  shade[0] = shade[1] = shade[2] = 0.f; shade[3] = alpha * static_cast<float>(REXCVAR_GET(aot_subtitle_background_opacity));
  write(sp + 0x5C, sp + 0x80); write(sp + 0x64, 0); write(sp + 0x6C, 1);
  frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
  const double vertical_padding = caller == 0x823A5990 ? 0 : 2;
  frame.ctx.f1.f64 = backing_x - 4; frame.ctx.f2.f64 = backing_y - vertical_padding;
  frame.ctx.f3.f64 = draw_width + 8; frame.ctx.f4.f64 = draw_height + 2 * vertical_padding;
  frame.ctx.f5.f64 = 0; frame.ctx.f6.f64 = 0; frame.ctx.f7.f64 = 1; frame.ctx.f8.f64 = 1;
  if (checkpoint_title) {
    const auto root = checkpoint_root_image.load();
    if (Readable(memory, root, 0x200) &&
        *memory->TranslateVirtual<rex::be<uint32_t>*>(root) == 0x820D9CC0) {
      const auto* bounds = memory->TranslateVirtual<rex::be<float>*>(root + 0x1F0);
      const double corner = (float(bounds[3]) - float(bounds[1])) * .6;
      const double left = float(bounds[0]) - corner + 3;
      const double top = float(bounds[1]) - corner + 3;
      const double right = float(bounds[2]) + corner - 3;
      const double bottom = float(bounds[3]) + corner - 3;
      if (corner > 0 && corner < 100 && right > left && bottom > top &&
          right - left < 1200 && bottom - top < 400) {
        // The interior image components are transparent; the retail border
        // remains, with its outer corners clipped in AotCheckpointCorner.
        // Non-overlapping strips apply the selected opacity exactly once.
        const double radius = corner * .8 - 3;
        for (double row = top; row < bottom;) {
          const double height = row >= top + radius && row < bottom - radius
              ? bottom - radius - row : std::min(1.0, bottom - row);
          const double edge = std::min(row + height / 2 - top, bottom - row - height / 2);
          const double inset = edge < radius
              ? radius - std::sqrt(std::max(0.0, radius * radius - (radius - edge) * (radius - edge))) : 0;
          frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
          frame.ctx.f1.f64 = left + inset; frame.ctx.f2.f64 = row;
          frame.ctx.f3.f64 = right - left - 2 * inset; frame.ctx.f4.f64 = height;
          frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
          frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
          sub_82641CB8(frame.ctx, base);
          row += height;
        }
      }
    }
    frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas;
    frame.ctx.f1.f64 = backing_x - 4; frame.ctx.f2.f64 = backing_y - vertical_padding;
    frame.ctx.f3.f64 = draw_width + 8; frame.ctx.f4.f64 = draw_height + 2 * vertical_padding;
    frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
    frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
  }
  // Tutorial/objective text is emitted as separate runs around controller
  // glyphs. Per-run rectangles overlap neighboring runs and subsequent lines.
  // These runs retain their native panel and only get the small letter shadow.
  if (caller != 0x823A5990 || reward_caption) sub_82641CB8(frame.ctx, base);
  shade[3] = alpha * .8f;
  frame.ctx.r1.u64 = sp; frame.ctx.r3 = canvas; frame.ctx.r6 = text;
  frame.ctx.r7 = font; frame.ctx.r8.u64 = sp + 0x80;
  frame.ctx.f1.f64 = x.f64 + 1; frame.ctx.f2.f64 = y.f64 + 1;
  frame.ctx.f3 = scale_x; frame.ctx.f4 = scale_y;
  sub_82642158(frame.ctx, base);
  static std::atomic<unsigned> reported{0};
  const unsigned bit = caller == 0x823A5990 ? 2 : 1;
  if (!(reported.fetch_or(bit) & bit))
    REXLOG_INFO("HUD text contrast: caller={:08X}, measured={}x{}", caller, width, height);
}

void AotFontTrace(PPCRegister& canvas, PPCRegister& text, PPCRegister& font,
    PPCRegister& color, PPCRegister& x, PPCRegister& y,
    PPCRegister& scale_x, PPCRegister& scale_y) {
  static const bool trace_fonts = std::getenv("AOT_TRACE_FONT") != nullptr;
  static const bool trace_text = std::getenv("AOT_TRACE_TEXT") != nullptr;
  if (!trace_fonts && !trace_text) return;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, font.u32, 0x40) || !Readable(memory, text.u32, 32)) return;
  static std::mutex mutex;
  static std::unordered_set<uint32_t> seen;
  std::lock_guard lock(mutex);
  if (trace_text) {
    // Bounded discovery only, off in normal play. Keep the draw untouched and
    // distinguish call sites: subtitles, HUD captions and UI may share a font.
    static std::unordered_set<std::string> text_seen;
    auto* ctx = thread->context();
    if (text_seen.size() < 512 && Readable(memory, text.u32, 256) &&
        Readable(memory, color.u32, 16)) {
      std::string escaped;
      const auto* data = memory->TranslateVirtual<const uint8_t*>(text.u32);
      constexpr char hex[] = "0123456789ABCDEF";
      size_t length = 0;
      for (; length < 256 && data[length]; ++length) {
        const auto ch = data[length];
        if (ch >= 32 && ch < 127 && ch != '\\' && ch != '"') escaped += char(ch);
        else { escaped += "\\x"; escaped += hex[ch >> 4]; escaped += hex[ch & 15]; }
      }
      if (length && length < 256) {
        const auto key = std::to_string(ctx->lr) + ":" + std::to_string(font.u32) + ":" + escaped;
        if (text_seen.insert(key).second) {
          const auto* rgba = memory->TranslateVirtual<const rex::be<float>*>(color.u32);
          const uint64_t name = *memory->TranslateVirtual<rex::be<uint64_t>*>(font.u32 + 0x2C);
          // The thread LR is useful for direct calls, but is not a synthetic
          // CallFrame's LR. Position/color come from the actual hook arguments.
          REXLOG_INFO("Native text trace: thread_lr={:08X}, canvas={:08X}, font={:08X}, name={:016X}, xy={}/{}, scale={}/{}, rgba={}/{}/{}/{}, text=\"{}\"",
              ctx->lr, canvas.u32, font.u32, name, x.f64, y.f64, scale_x.f64, scale_y.f64,
              float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3]), escaped);
        }
      }
    }
  }
  if (!trace_fonts) return;
  if (!seen.insert(font.u32).second) return;
  const uint32_t index = *memory->TranslateVirtual<rex::be<uint32_t>*>(font.u32 + 4);
  std::string chars;
  const auto* data = memory->TranslateVirtual<const uint8_t*>(text.u32);
  for (size_t i = 0; i < 32 && data[i]; ++i) chars += std::to_string(data[i]) + ",";
  REXLOG_INFO("Native font trace: object={:08X}, index={}, bytes={}", font.u32, index, chars);
}

bool AotKeyboardGlyph(PPCRegister& canvas, PPCRegister& text, PPCRegister& font,
    PPCRegister& color, PPCRegister& x, PPCRegister& y, PPCRegister& scale_x, PPCRegister& scale_y) {
  static thread_local bool drawing = false;
  if (drawing) return false;
  AotFontTrace(canvas, text, font, color, x, y, scale_x, scale_y);
  AotEditionLabel(canvas, text, font, color, x, y, scale_x, scale_y);
  AotHudTextContrast(canvas, text, font, color, x, y, scale_x, scale_y);
  if (!aot::KeyboardPrompts()) return false;
  auto* thread = rex::runtime::ThreadState::Get();
  auto* memory = thread->memory();
  if (!Readable(memory, text.u32, 32) || !Readable(memory, font.u32, 0x70) || !Readable(memory, color.u32, 16)) return false;
  const char* string = memory->TranslateVirtual<const char*>(text.u32);
  const bool title = std::strncmp(string, "Press START", 12) == 0;
  if (!title && (!controller_font_name || *memory->TranslateVirtual<rex::be<uint64_t>*>(font.u32 + 0x2C) != controller_font_name)) return false;
  size_t length = 0;
  auto glyph = [](unsigned char code) -> const aot::KeyboardGlyph* {
    for (const auto& item : aot::keyboard_glyphs) if (item.code == code) return &item;
    return nullptr;
  };
  if (!title) {
    for (; length < 32 && string[length]; ++length) if (!glyph(static_cast<unsigned char>(string[length]))) return false;
    if (!length || length == 32) return false;
  }
  const uint32_t parent = thread->context()->r1.u32, sp = parent - 0x500;
  if (!Readable(memory, sp - 0x2000, 0x2500)) return false;
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto write = [&](uint32_t address, uint32_t value) { *memory->TranslateVirtual<rex::be<uint32_t>*>(address) = value; };
  const auto chars = read(font.u32 + 0x3C), char_count = read(font.u32 + 0x40);
  if (!title && (char_count != 256 || !Readable(memory, chars, char_count * 20))) return false;
  rex::CallFrame frame(*thread->context()); frame.ctx.r1.u64 = sp; write(sp, parent);
  drawing = true;
  struct Reset { bool& flag; ~Reset() { flag = false; } } reset{drawing};
  if (title) {
    std::strcpy(memory->TranslateVirtual<char*>(sp + 0x200), "Press ENTER");
    frame.ctx.r3 = canvas; frame.ctx.r6.u64 = sp + 0x200; frame.ctx.r7 = font; frame.ctx.r8 = color;
    frame.ctx.f1 = x; frame.ctx.f2 = y; frame.ctx.f3 = scale_x; frame.ctx.f4 = scale_y;
    sub_82642158(frame.ctx, memory->virtual_membase()); canvas = frame.ctx.r3;
    return true;
  }
  auto* source_color = memory->TranslateVirtual<rex::be<float>*>(color.u32);
  auto* tile_color = memory->TranslateVirtual<rex::be<float>*>(sp + 0x80);
  for (unsigned i = 0; i < 3; ++i) tile_color[i] = source_color[i];
  const float alpha = source_color[3];
  write(sp + 0x5C, sp + 0x80); write(sp + 0x64, 0); write(sp + 0x6C, 1);
  int advance = 0;
  for (size_t i = 0; i < length; ++i) {
    const auto code = static_cast<unsigned char>(string[i]);
    const auto& shape = *glyph(code);
    const float width = read(chars + code * 20 + 8) * static_cast<float>(scale_x.f64);
    const float height = read(chars + code * 20 + 12) * static_cast<float>(scale_y.f64);
    for (unsigned j = 0; j < shape.count; ++j) {
      const auto& rectangle = aot::keyboard_rects[shape.offset + j];
      tile_color[3] = alpha * rectangle.alpha / 255.f;
      frame.ctx.r3 = canvas;
      frame.ctx.f1.f64 = static_cast<int>(x.f64) + advance + width * (.04f + .88f * rectangle.x / shape.width);
      frame.ctx.f2.f64 = static_cast<int>(y.f64) + height * (.05f + .90f * rectangle.y / shape.height);
      frame.ctx.f3.f64 = width * .88f * rectangle.width / shape.width;
      frame.ctx.f4.f64 = height * .90f * rectangle.height / shape.height;
      frame.ctx.f5.f64 = frame.ctx.f6.f64 = 0;
      frame.ctx.f7.f64 = frame.ctx.f8.f64 = 1;
      sub_82641CB8(frame.ctx, memory->virtual_membase());
    }
    advance += static_cast<int>(width);
  }
  canvas.u64 = advance;
  return true;
}

void AotPrepareLocalStorage(PPCRegister& profile) {
  if (!REXCVAR_GET(aot_local_storage)) return;
  // Run synchronously before the original profile query. Debug commands queued
  // by the user cannot delay or interleave this device selection.
  // Updating just DeviceID preserves the existing completion delegates.
  ExecuteGameCommand("set OnlineSubsystemLive DeviceCache (DeviceID=1)");
  ExecuteGameCommand("set UIDataProvider_AO2GameSaves DeviceID 1");
  ExecuteGameCommand("@local-device-selected", profile.u32);
  REXLOG_INFO("Prepared local storage at campaign device check");
}

void AotFrameLimit(PPCRegister& f0) {
  // 1/max_rate - delta_time becomes -delta_time, so the existing clamp skips
  // the engine sleep. Actual time measurements and simulation delta remain intact.
  if (REXCVAR_GET(aot_fps) == 60) f0.f64 = 0.0;
}
void AotGpuPollBackoff() {
#ifdef _WIN32
  if (!REXCVAR_GET(aot_gpu_poll_backoff)) return;
  static thread_local uint32_t polls = 0;
  if (++polls % 256 != 0) return;
  struct PollTimer {
    HANDLE handle = CreateWaitableTimerExW(nullptr, nullptr,
        CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE);
    PollTimer() { REXLOG_INFO("GPU poll backoff: timer={}, requested_us=100, every_polls=256", handle != nullptr); }
    ~PollTimer() { if (handle) CloseHandle(handle); }
  };
  static thread_local PollTimer timer;
  if (!timer.handle) { SwitchToThread(); return; }
  LARGE_INTEGER due; due.QuadPart = -1000;  // 100 microseconds, relative.
  if (!SetWaitableTimer(timer.handle, &due, 0, nullptr, nullptr, FALSE)) return;
  if (WaitForSingleObject(timer.handle, 10) != WAIT_OBJECT_0) CancelWaitableTimer(timer.handle);
#endif
  // The original loop still checks the GPU counter and executes its timeout
  // handling. This hook never declares pending work complete or skips it.
}
void AotPresentInterval(PPCRegister& r10) {
  // Run at the common retail join, before the interval is packed into r8.
  // At 60 FPS the PC deadline owns the cap; an additional guest refresh wait
  // can halve the achieved rate when rendering crosses a VBlank boundary.
  // The diagnostic fallback retains at most one guest refresh. At 30 FPS,
  // preserve the original branches and packed interval without modification.
  const uint32_t original = r10.u32;
  const int target = REXCVAR_GET(aot_fps);
  const bool immediate = REXCVAR_GET(aot_immediate_guest_present);
  if (target == 60) {
    if (immediate) r10.u64 = 0;
    else if (original > 1) r10.u64 = 1;
  }
  static std::atomic<uint32_t> observed[4]{};
  if (original < 16) {
    const uint32_t bit = uint32_t(1) << original;
    if (!(observed[(target == 60) + 2 * immediate].fetch_or(bit, std::memory_order_relaxed) & bit))
      REXLOG_INFO("PC present interval: retail={}, effective={}, target_fps={}, immediate_experiment={}",
                  original, r10.u32, target, immediate);
  }
}
void AotBlurRadius(PPCRegister& f1) {
  if (REXCVAR_GET(aot_disable_blur)) f1.f64 = 0.0;
}

void AotObserveConsoleActor(PPCRegister& actor, PPCRegister& stack) {
  auto* memory = rex::runtime::ThreadState::Get()->memory();
  if (IsLocalConsoleActor(memory, actor.u32)) console_actor = actor.u32;
  if (!std::getenv("AOT_TRACE_COMMANDS")) return;
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  const auto string = read(stack.u32 + 0x58);
  const auto count = read(stack.u32 + 0x5C);
  std::string text;
  if (count && count < 256) text.assign(memory->TranslateVirtual<const char*>(string), count - 1);
  REXLOG_INFO("Guest ConsoleCommand: actor={:08X}, method={:08X}, text={}", actor.u32,
      read(read(actor.u32) + 0x108), text);
}

// Correct only verified DOF source rectangles, preserving destination geometry.
// A separate opt-in, bounded trace records the resulting retail arguments.
void AotPostprocessRectangle(PPCRegister& caller, PPCRegister& stack,
    PPCRegister& f1, PPCRegister& f2, PPCRegister& f3, PPCRegister& f4,
    PPCRegister& f5, PPCRegister& f6, PPCRegister& f7, PPCRegister& f8) {
  const bool gaussian = caller.u32 == 0x8255FF84;
  const bool final_blend = caller.u32 == 0x82498678;
  if (REXCVAR_GET(aot_dof_uv_correction) && (gaussian || final_blend)) {
    auto* memory = rex::runtime::ThreadState::Get()->memory();
    if (Readable(memory, stack.u32 + 0x5C, 0x1C) && Readable(memory, 0x83118EAC + 0x14, 0x14)) {
      const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
      const aot::DofDimensions dimensions{read(0x83118EAC + 0x14), read(0x83118EAC + 0x18),
          read(0x83118EAC + 0x1C), read(0x83118EAC + 0x20), read(0x83118EAC + 0x24)};
      const aot::DofRectangle original{f5.f64, f6.f64, f7.f64, f8.f64,
          read(stack.u32 + 0x5C), read(stack.u32 + 0x64), read(stack.u32 + 0x6C), read(stack.u32 + 0x74)};
      aot::DofRectangle corrected{};
      if (aot::CorrectDofRectangle(caller.u32, dimensions, original, corrected)) {
        f5.f64 = corrected.x; f6.f64 = corrected.y;
        f7.f64 = corrected.width; f8.f64 = corrected.height;
        *memory->TranslateVirtual<rex::be<uint32_t>*>(stack.u32 + 0x6C) = corrected.source_w;
        *memory->TranslateVirtual<rex::be<uint32_t>*>(stack.u32 + 0x74) = corrected.source_h;
        static thread_local unsigned reported = 0;
        const unsigned bit = gaussian ? 1 : 2;
        if (!(reported & bit)) {
          REXLOG_INFO("DOF {} UV correction: scene={}x{}, filter={}x{}, factor={}",
                      gaussian ? "blur" : "final-blend", dimensions.scene_w, dimensions.scene_h,
                      dimensions.filter_w, dimensions.filter_h, dimensions.factor);
          reported |= bit;
        }
      }
    }
  }
  struct Trace {
    const char* path = std::getenv("AOT_RECT_LOG");
    Clock::time_point start = pc_features_started;
    std::ofstream output;
    unsigned count = 0;
    bool finished = false;
    Trace() { if (path) REXLOG_INFO("Postprocess rectangle trace armed: {}", path); }
  };
  static thread_local Trace trace;
  if (!trace.path || trace.finished) return;
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - trace.start).count();
  if (ms < 155000) return;
  if (ms >= 158000 || trace.count >= 256) {
    trace.output.close(); trace.finished = true;
    REXLOG_INFO("Postprocess rectangle capture complete: {} records", trace.count);
    return;
  }
  if (!trace.output.is_open()) {
    trace.output.open(trace.path);
    if (!trace.output) { trace.finished = true; return; }
    trace.output.precision(10);
    trace.output << "elapsed_ms,caller,f1,f2,f3,f4,f5,f6,f7,f8,target_w,target_h,source_w,source_h,scene_w,scene_h,downsample,filter_w,filter_h\n";
  }
  auto* memory = rex::runtime::ThreadState::Get()->memory();
  if (!Readable(memory, stack.u32 + 0x5C, 0x1C) || !Readable(memory, 0x83118EAC + 0x14, 0x14)) return;
  const auto read = [&](uint32_t address) { return static_cast<uint32_t>(*memory->TranslateVirtual<rex::be<uint32_t>*>(address)); };
  trace.output << ms << ',' << std::hex << caller.u32 << std::dec;
  for (double value : {f1.f64, f2.f64, f3.f64, f4.f64, f5.f64, f6.f64, f7.f64, f8.f64}) trace.output << ',' << value;
  for (uint32_t offset : {0x5Cu, 0x64u, 0x6Cu, 0x74u}) trace.output << ',' << read(stack.u32 + offset);
  for (uint32_t offset : {0x14u, 0x18u, 0x1Cu, 0x20u, 0x24u}) trace.output << ',' << read(0x83118EAC + offset);
  trace.output << '\n';
  ++trace.count;
}
