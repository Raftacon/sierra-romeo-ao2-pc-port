#ifdef _WIN32
#include <winsock2.h>
#include "network_datagram.h"
#include <climits>
#include <cstring>

namespace aot {
namespace {
int Fail(int error) { WSASetLastError(error); return SOCKET_ERROR; }
}
int SendGuestDatagram(uint64_t socket, const uint8_t* data, uint32_t length,
                      uint32_t flags, const rex::system::XSOCKADDR_IN* to, uint32_t to_length) {
  if (length > INT_MAX) return Fail(WSAEMSGSIZE);
  if (length && !data) return Fail(WSAEFAULT);
  sockaddr_in destination{};
  if (to) {
    if (to_length < sizeof(*to)) return Fail(WSAEFAULT);
    if (uint16_t(to->sin_family) != AF_INET) return Fail(WSAEAFNOSUPPORT);
    destination.sin_family = AF_INET;
    destination.sin_port = htons(uint16_t(to->sin_port));
    destination.sin_addr.s_addr = htonl(uint32_t(to->sin_addr));
  }
  const char empty = 0;
  return sendto(static_cast<SOCKET>(socket), data ? reinterpret_cast<const char*>(data) : &empty,
                int(length), int(flags), to ? reinterpret_cast<sockaddr*>(&destination) : nullptr,
                to ? sizeof(destination) : 0);
}

int ReceiveGuestDatagram(uint64_t socket, uint8_t* data, uint32_t length,
                         uint32_t flags, rex::system::XSOCKADDR_IN* from,
                         rex::be<uint32_t>* from_length) {
  if (length > INT_MAX) return Fail(WSAEMSGSIZE);
  if (length && !data) return Fail(WSAEFAULT);
  if (from && (!from_length || uint32_t(*from_length) < sizeof(*from))) return Fail(WSAEFAULT);
  sockaddr_in source{};
  int source_length = sizeof(source);
  char empty = 0;
  const int result = recvfrom(static_cast<SOCKET>(socket), data ? reinterpret_cast<char*>(data) : &empty,
      int(length), int(flags), from ? reinterpret_cast<sockaddr*>(&source) : nullptr,
      from ? &source_length : nullptr);
  if (result == SOCKET_ERROR) return result;
  if (from) {
    from->sin_family = source.sin_family;
    from->sin_port = ntohs(source.sin_port);
    from->sin_addr = ntohl(source.sin_addr.s_addr);
    std::memset(from->x_sin_zero, 0, sizeof(from->x_sin_zero));
    *from_length = sizeof(*from);
  }
  return result;
}
}  // namespace aot
#endif
