#pragma once
#include "coop_relay_route.h"
#include "coop_tls_stream.h"

namespace aot {
// Relay framing over an already verified native TLS stream. Worker-owned.
class CoopRelaySession {
 public:
  explicit CoopRelaySession(CoopTlsStream& stream):stream_(stream){}
  CoopRelayRoute RegisterHost(const std::string& endpoint);
  void Join(const CoopRelayRoute& route);
  void WaitPaired();
  void Write(std::span<const uint8_t> bytes);
  bool Read(std::vector<uint8_t>& bytes);
  bool HasBufferedInput() const{return !pending_.empty()||stream_.HasBufferedInput();}
 private:
  enum class State { Initial, Registered, Paired, Failed };
  State state_=State::Initial;
  CoopTlsStream& stream_;
  std::vector<uint8_t> pending_;
  std::string Line();
  void PairResponse();
};
}
