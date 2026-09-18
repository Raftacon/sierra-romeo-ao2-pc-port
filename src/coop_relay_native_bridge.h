#pragma once
#include "coop_relay_route.h"
#include <memory>

namespace aot {
enum class CoopRelayBridgeState { Idle, Connecting, WaitingForPeer, WaitingForGame, Connected, Stopped, Failed };
struct CoopRelayBridgeStatus {
  CoopRelayBridgeState state=CoopRelayBridgeState::Idle;
  CoopRelayRoute route;
  uint16_t local_port=0;
  std::string error;
};
// Start/Cancel/Snapshot are for a single controlling thread. Socket work is owned
// by the worker. A supplied test trust engine must outlive this object.
class CoopRelayNativeBridge {
 public:
  CoopRelayNativeBridge();
  ~CoopRelayNativeBridge();
  bool Host(const std::string& endpoint,uint16_t game_port,void* trust_engine=nullptr);
  bool Peer(const CoopRelayRoute& route,void* trust_engine=nullptr);
  void Cancel();
  CoopRelayBridgeStatus Snapshot() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
