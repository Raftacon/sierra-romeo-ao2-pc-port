#pragma once
#include "coop_relay_native_bridge.h"
#include <cstdint>

namespace aot {
// Retry only a waiting host. A joined campaign must use normal disconnect
// handling, never silently replace the transport underneath its simulation.
class CoopRelayHostRetry {
 public:
  bool Poll(bool waiting,CoopRelayBridgeState state,uint64_t now) {
    if(!waiting || (state!=CoopRelayBridgeState::Idle &&
        state!=CoopRelayBridgeState::Failed && state!=CoopRelayBridgeState::Stopped)) {
      Reset();return false;
    }
    if(!scheduled_){scheduled_=true;retry_at_=now+5000;return false;}
    if(now<retry_at_)return false;
    retry_at_=now+5000;return true;
  }
  void Reset(){scheduled_=false;retry_at_=0;}
 private:
  bool scheduled_=false;
  uint64_t retry_at_=0;
};
}
