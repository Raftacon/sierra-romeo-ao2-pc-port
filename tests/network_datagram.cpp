#include <winsock2.h>
#include "src/network_datagram.h"
#include <array>
#include <cstdio>
#include <cstring>
#include <stdexcept>

namespace {
using rex::system::XSocket;
using rex::system::XSOCKADDR_IN;
void Check(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
XSOCKADDR_IN Endpoint(XSocket& socket) {
  sockaddr_in host{};
  int size = sizeof(host);
  Check(getsockname(SOCKET(socket.native_handle()), reinterpret_cast<sockaddr*>(&host), &size) == 0,
        "getsockname");
  XSOCKADDR_IN guest{};
  guest.sin_family = host.sin_family;
  guest.sin_port = ntohs(host.sin_port);
  guest.sin_addr = ntohl(host.sin_addr.s_addr);
  return guest;
}
void Bind(XSocket& socket) {
  Check(socket.Initialize(XSocket::X_AF_INET, XSocket::X_SOCK_DGRAM, XSocket::X_IPPROTO_UDP) == 0,
        "socket initialization");
  rex::system::N_XSOCKADDR_IN local;
  local.sin_family = AF_INET; local.sin_port = 0; local.sin_addr = INADDR_LOOPBACK;
  std::memset(local.x_sin_zero, 0, sizeof(local.x_sin_zero));
  Check(socket.Bind(&local, sizeof(local)) == 0, "loopback bind");
  const DWORD timeout = 2000;
  Check(setsockopt(SOCKET(socket.native_handle()), SOL_SOCKET, SO_RCVTIMEO,
                   reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0, "receive timeout");
}
}
int main() {
  WSADATA wsa{};
  if (WSAStartup(MAKEWORD(2,2), &wsa)) return 1;
  struct Cleanup { ~Cleanup() { WSACleanup(); } } cleanup;
  try {
    XSocket left(nullptr), right(nullptr);
    Bind(left); Bind(right);
    auto left_address = Endpoint(left);
    const auto right_address = Endpoint(right);
    for (unsigned attempt = 0; attempt < 8 &&
         uint16_t(left_address.sin_port) == htons(uint16_t(left_address.sin_port)); ++attempt) {
      Check(left.Close() == 0, "replace symmetric-port socket");
      Bind(left);
      left_address = Endpoint(left);
    }
    Check(uint16_t(left_address.sin_port) != htons(uint16_t(left_address.sin_port)),
          "negative control requires unequal port bytes");
    constexpr std::array<uint8_t, 11> payload{0x41,0,0xFE,3,5,7,11,13,17,19,23};
    std::array<uint8_t, 64> received{};
    const auto send = [&](XSocket& source, const XSOCKADDR_IN& destination) {
      Check(aot::SendGuestDatagram(source.native_handle(), payload.data(), payload.size(), 0,
                                   &destination, sizeof(destination)) == payload.size(), "UDP send");
    };
    // Negative control calls the actual pinned DLL receiver. It reverses the
    // source port; the socket above guarantees the two port bytes differ.
    send(left, right_address);
    rex::system::N_XSOCKADDR_IN old_from;
    uint32_t old_length = sizeof(old_from);
    Check(right.RecvFrom(received.data(), received.size(), 0, &old_from, &old_length) == payload.size(),
          "original receiver negative control");
    Check(uint16_t(old_from.sin_port) == htons(uint16_t(left_address.sin_port)),
          "pinned receiver behavior differs from inspected source");
    Check(uint16_t(old_from.sin_port) != uint16_t(left_address.sin_port),
          "original receiver unexpectedly reports the correct port");
    Check(uint32_t(old_from.sin_addr) == INADDR_LOOPBACK, "original receive address");
    // Corrected production path: receive the precise sender, then reply using
    // the returned guest sockaddr. Every packet stays on owned loopback sockets.
    send(left, right_address);
    XSOCKADDR_IN from{};
    rex::be<uint32_t> from_length = sizeof(from);
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), received.size(), MSG_PEEK,
                                    &from, &from_length) == payload.size(), "peek");
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), received.size(), 0,
                                    &from, &from_length) == payload.size(), "UDP receive");
    Check(std::memcmp(received.data(), payload.data(), payload.size()) == 0, "binary payload changed");
    Check(std::memcmp(&from, &left_address, sizeof(from)) == 0 && uint32_t(from_length) == sizeof(from),
          "source address/port byte order");
    send(right, from);
    Check(aot::ReceiveGuestDatagram(left.native_handle(), received.data(), received.size(), 0,
                                    &from, &from_length) == payload.size(), "reply");
    Check(std::memcmp(&from, &right_address, sizeof(from)) == 0, "reply endpoint");
    Check(aot::SendGuestDatagram(left.native_handle(), nullptr, 0, 0, &right_address, sizeof(right_address)) == 0,
          "zero-length send");
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), received.size(), 0,
                                    nullptr, nullptr) == 0, "zero-length receive without source");
    // Empty nonblocking queue must leave metadata and the payload untouched.
    u_long nonblocking = 1;
    Check(ioctlsocket(SOCKET(right.native_handle()), FIONBIO, &nonblocking) == 0, "nonblocking");
    std::memset(&from, 0xA5, sizeof(from));
    const auto before = from;
    from_length = sizeof(from);
    received.fill(0xA5);
    const auto before_data = received;
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), received.size(), 0,
                                    &from, &from_length) == SOCKET_ERROR && WSAGetLastError() == WSAEWOULDBLOCK,
          "empty receive error");
    Check(std::memcmp(&from, &before, sizeof(from)) == 0 && uint32_t(from_length) == sizeof(from) &&
          received == before_data, "failed receive changed outputs");
    from_length = sizeof(from) - 1;
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), received.size(), 0,
                                    &from, &from_length) == SOCKET_ERROR && WSAGetLastError() == WSAEFAULT,
          "short source buffer accepted");
    Check(aot::SendGuestDatagram(left.native_handle(), payload.data(), UINT32_MAX, 0,
                                &right_address, sizeof(right_address)) == SOCKET_ERROR && WSAGetLastError() == WSAEMSGSIZE,
          "oversized buffer wrapped");
    auto wrong_family = right_address;
    wrong_family.sin_family = AF_INET6;
    Check(aot::SendGuestDatagram(left.native_handle(), payload.data(), payload.size(), 0,
                                &wrong_family, sizeof(wrong_family)) == SOCKET_ERROR && WSAGetLastError() == WSAEAFNOSUPPORT,
          "unsupported family accepted");
    send(left, right_address);
    from_length = sizeof(from);
    Check(aot::ReceiveGuestDatagram(right.native_handle(), received.data(), 1, 0,
                                    &from, &from_length) == SOCKET_ERROR && WSAGetLastError() == WSAEMSGSIZE,
          "truncated datagram status");
    Check(std::memcmp(&from, &before, sizeof(from)) == 0, "failed truncated receive changed source");
    std::puts("Guest datagrams: real loopback reply, byte order, peek, zero length and failure preservation passed");
  } catch (const std::exception& e) { std::fprintf(stderr, "%s (WSA %d)\n", e.what(), WSAGetLastError()); return 1; }
}
