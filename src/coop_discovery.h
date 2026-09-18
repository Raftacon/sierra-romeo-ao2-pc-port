#pragma once
#include "coop_lobby.h"
#include "coop_relay_route.h"

namespace aot {
// Discovery metadata is a checkpoint identifier, never an engine travel URL.
inline std::string CoopDiscoveryMap(std::string_view url) {
  auto key=std::string_view("FromCheckpoint=");
  auto start=url.find(key);
  if(start==url.npos){key="CheckpointToLoad=";start=url.find(key);}
  auto id=start==url.npos?url:url.substr(start+key.size());
  id=id.substr(0,id.find('?'));
  if(!id.empty() && id.size()<=16 && id.find_first_not_of("0123456789_")==id.npos)return std::string(id);
  return "Continue";
}
struct CoopDiscoveredRoom {
  std::string address, name, map;
  uint16_t port=0;
  uint8_t difficulty=0;
  CoopVisibility visibility=CoopVisibility::Private;
  std::optional<CoopRelayRoute> relay;
  bool operator==(const CoopDiscoveredRoom&) const = default;
};
// Nonblocking LAN discovery only. Invitations are never advertised.
class CoopDiscovery {
 public:
  CoopDiscovery();
  ~CoopDiscovery();
  bool Advertise(const CoopLobbySettings&, std::string name, uint16_t game_port,
                 uint16_t discovery_port=37003);
  bool Search(CoopVisibility, uint64_t now_ms, uint16_t discovery_port=37003);
  void Poll(uint64_t now_ms);
  void Stop();
  bool Searching() const;
  const std::vector<CoopDiscoveredRoom>& Rooms() const;
  const std::string& Error() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
