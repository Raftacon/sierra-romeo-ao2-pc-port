#pragma once
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace aot {
// Blocking worker-only TLS stream over a connected, caller-owned Winsock socket.
// One owning thread; the caller closes its socket after destroying the stream.
class CoopTlsStream {
 public:
  CoopTlsStream();
  ~CoopTlsStream();
  void Handshake(uintptr_t socket,const std::wstring& hostname,void* trust_engine=nullptr);
  void Write(std::span<const uint8_t> bytes);
  // false means an authenticated TLS close; a truncated TCP stream is an error.
  bool Read(std::vector<uint8_t>& bytes);
  bool HasBufferedInput() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
