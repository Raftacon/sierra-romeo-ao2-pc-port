#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace aot {
enum class CoopVisibility : uint8_t { Public, Private };
enum class CoopLobbyPhase { Idle, Hosting, Connecting, Joined, Disconnected, Failed };
struct CoopMember {
  uint64_t id = 0;
  std::string name;
  uint8_t character = 0;
  bool ready = false;
  bool operator==(const CoopMember&) const = default;
};
struct CoopLobbySettings {
  CoopVisibility visibility = CoopVisibility::Private;
  std::string map;
  uint8_t difficulty = 0;
  bool operator==(const CoopLobbySettings&) const = default;
};
struct CoopWeapon {
  std::string archetype, class_name;
  std::array<uint32_t,7> upgrades{};
  bool operator==(const CoopWeapon&) const = default;
};
struct CoopLoadout {
  uint32_t armor=0, mask=0;
  std::vector<CoopWeapon> weapons;
  bool operator==(const CoopLoadout&) const = default;
};
struct CoopViewport {
  uint32_t width=0, height=0;
  bool operator==(const CoopViewport&) const = default;
};
struct CoopLobbySnapshot {
  CoopLobbyPhase phase = CoopLobbyPhase::Idle;
  CoopLobbySettings settings;
  std::array<uint8_t, 16> room{};
  std::vector<CoopMember> members;
  uint64_t local_id = 0;
  uint64_t revision = 0;
  std::string error;
  uint64_t preparation=0;
  // Agreed for this preparation; stays fixed throughout the loaded map.
  uint8_t input_delay_frames=0;
  std::array<std::optional<CoopLoadout>,2> loadouts;
  bool prepared=false;
  std::array<bool,2> imported{};
  std::array<CoopViewport,2> viewports{};
  bool load_authorized=false;
  bool checkpoint_ready=false;
  uint32_t checkpoint_bytes=0;
  std::array<bool,2> checkpoint_imported{};
  uint64_t barrier_sent=0, barrier_received=0;
  uint64_t input_sent=0, input_received=0;
  bool shopping_done_sent=false, shopping_done_received=false;
  uint64_t cash_sent=0, cash_received=0;
  // Local-clock request/echo round trip, including application scheduling.
  uint32_t round_trip_ms=0;
  uint64_t timing_samples=0;
};

// PC lobby control channel. Owns native nonblocking sockets; all methods run on
// one owning thread. Native input transport is experimental; admission alone
// does not establish a synchronized campaign.
class CoopLobby {
 public:
  CoopLobby();
  ~CoopLobby();
  CoopLobby(const CoopLobby&) = delete;
  CoopLobby& operator=(const CoopLobby&) = delete;
  bool Host(const CoopLobbySettings& settings, const std::string& name,
            const std::string& bind_ipv4, uint16_t port, uint64_t now_ms,
            bool experimental_input_buffer=false, uint8_t fixed_probe_frames=0);
  bool Join(const std::string& ipv4, uint16_t port, CoopVisibility visibility,
            const std::string& invite, const std::string& name, uint64_t now_ms);
  void Poll(uint64_t now_ms);
  bool SetReady(bool ready);
  bool SetHostCharacter(uint8_t character);
  bool Kick(uint64_t member_id);
  bool RequestPreparation();
  // Host-authorized travel inside an already loaded campaign session.
  bool RequestTransition(const CoopLobbySettings& settings);
  bool SubmitLoadout(uint64_t preparation, const CoopLoadout& loadout);
  bool AcknowledgeImport(uint64_t preparation, const CoopViewport& viewport);
  bool SubmitCheckpoint(uint64_t preparation, std::span<const uint8_t> checkpoint);
  std::span<const uint8_t> Checkpoint() const;
  bool AcknowledgeCheckpoint(uint64_t preparation);
  bool SignalNativeBarrier(uint64_t preparation);
  bool SendNativeInput(uint64_t preparation, std::span<const uint8_t> packet);
  std::optional<std::vector<uint8_t>> TakeNativeInput();
  bool SignalShoppingDone(uint64_t preparation);
  bool TakeShoppingDone();
  bool SendCheckpointCash(uint64_t preparation, uint32_t total);
  std::optional<uint32_t> TakeCheckpointCash();
  void Stop();
  uint16_t Port() const;
  std::string InviteCode() const;
  const CoopLobbySnapshot& Snapshot() const;
  static bool ValidSettings(const CoopLobbySettings& settings);
  static bool ValidLoadout(const CoopLoadout& loadout);
  static bool ValidViewport(const CoopViewport& viewport);
  static bool RequiresCheckpoint(const CoopLobbySettings& settings);
  static constexpr size_t kMaxCheckpointBytes=4*1024*1024;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
