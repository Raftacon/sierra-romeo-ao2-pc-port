#ifdef _WIN32
#include <winsock2.h>
#include "network_datagram.h"
#include "network_memory.h"
#include <rex/hook.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xthread.h>

namespace {
using namespace rex;
using namespace rex::system;
u32 FailSocket(int error) { XThread::SetLastError(error); return UINT32_MAX; }
bool Span(uint32_t address, uint32_t size, bool write) {
  return aot::NetworkMemorySpan(REX_KERNEL_MEMORY()->virtual_membase(), address, size, write);
}
u32 SocketName(u32 handle, ppc_ptr_t<XSOCKADDR_IN> output, mapped_u32 length, bool peer) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) return FailSocket(WSAENOTSOCK);
  if (!Span(length.guest_address(), 4, true) || uint32_t(*length) < sizeof(XSOCKADDR_IN) ||
      !Span(output.guest_address(), sizeof(XSOCKADDR_IN), true)) return FailSocket(WSAEFAULT);
  sockaddr_in native{};
  int size = sizeof(native);
  const int result = peer ? getpeername(SOCKET(socket->native_handle()), reinterpret_cast<sockaddr*>(&native), &size) :
                           getsockname(SOCKET(socket->native_handle()), reinterpret_cast<sockaddr*>(&native), &size);
  if (result == SOCKET_ERROR) return FailSocket(WSAGetLastError());
  if (native.sin_family != AF_INET) return FailSocket(WSAEAFNOSUPPORT);
  XSOCKADDR_IN guest{};
  guest.sin_family = AF_INET;
  guest.sin_port = ntohs(native.sin_port);
  guest.sin_addr = ntohl(native.sin_addr.s_addr);
  std::memcpy(static_cast<XSOCKADDR_IN*>(output), &guest, sizeof(guest));
  *length = sizeof(guest);
  return 0;
}
u32 GetSocketName(u32, u32 handle, ppc_ptr_t<XSOCKADDR_IN> output, mapped_u32 length) {
  return SocketName(handle, output, length, false);
}
u32 GetPeerName(u32, u32 handle, ppc_ptr_t<XSOCKADDR_IN> output, mapped_u32 length) {
  return SocketName(handle, output, length, true);
}
bool IntegerOption(uint32_t level, uint32_t option) {
  if (level == IPPROTO_TCP) return option == TCP_NODELAY;
  if (level != SOL_SOCKET) return false;
  switch (option) {
    case SO_ACCEPTCONN: case SO_BROADCAST: case SO_DEBUG: case SO_DONTROUTE:
    case SO_ERROR: case SO_KEEPALIVE: case SO_OOBINLINE: case SO_RCVBUF:
    case SO_REUSEADDR: case SO_SNDBUF: case SO_TYPE: case SO_RCVTIMEO:
    case SO_SNDTIMEO: case uint32_t(SO_DONTLINGER): case uint32_t(SO_EXCLUSIVEADDRUSE): return true;
    default: return false;
  }
}
u32 GetOption(u32, u32 handle, u32 level, u32 option, mapped_u32 output, mapped_u32 length) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) return FailSocket(WSAENOTSOCK);
  if (!IntegerOption(level, option)) return FailSocket(WSAENOPROTOOPT);
  if (!Span(length.guest_address(), 4, true) || uint32_t(*length) < 4 ||
      !Span(output.guest_address(), 4, true)) return FailSocket(WSAEFAULT);
  int value = 0, size = sizeof(value);
  if (getsockopt(SOCKET(socket->native_handle()), level, option, reinterpret_cast<char*>(&value), &size) == SOCKET_ERROR)
    return FailSocket(WSAGetLastError());
  *output = uint32_t(value); *length = sizeof(value);
  return 0;
}
u32 SetOption(u32, u32 handle, u32 level, u32 option, mapped_u32 input, u32 length) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) return FailSocket(WSAENOTSOCK);
  // Retain the runtime's VDP/security option bookkeeping.
  if (level == SOL_SOCKET && (option == 0x5801 || option == 0x5802)) {
    if (!Span(input.guest_address(), length, false)) return FailSocket(WSAEFAULT);
    return socket->SetOption(level, option, static_cast<be<uint32_t>*>(input), length) == 0 ? 0 : FailSocket(WSAGetLastError());
  }
  if (!IntegerOption(level, option)) return FailSocket(WSAENOPROTOOPT);
  if (length < 4 || !Span(input.guest_address(), 4, false)) return FailSocket(WSAEFAULT);
  int value = int(uint32_t(*input));
  if (socket->SetOption(level, option, &value, sizeof(value)) != 0)
    return FailSocket(WSAGetLastError());
  return 0;
}
u32 SendTo(u32, u32 handle, mapped_void data, u32 length, u32 flags,
           ppc_ptr_t<XSOCKADDR_IN> to, u32 to_length) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) { XThread::SetLastError(WSAENOTSOCK); return UINT32_MAX; }
  const int result = aot::SendGuestDatagram(socket->native_handle(), data, length, flags, to, to_length);
  if (result == SOCKET_ERROR) XThread::SetLastError(WSAGetLastError());
  return uint32_t(result);
}
u32 ReceiveFrom(u32, u32 handle, mapped_void data, u32 length, u32 flags,
                ppc_ptr_t<XSOCKADDR_IN> from, mapped_u32 from_length) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) { XThread::SetLastError(WSAENOTSOCK); return UINT32_MAX; }
  const int result = aot::ReceiveGuestDatagram(socket->native_handle(), data, length, flags, from, from_length);
  if (result == SOCKET_ERROR) XThread::SetLastError(WSAGetLastError());
  return uint32_t(result);
}
REX_HOOK(AotSendGuestDatagram, SendTo)
REX_HOOK(AotReceiveGuestDatagram, ReceiveFrom)
REX_HOOK(AotGetSocketName, GetSocketName)
REX_HOOK(AotGetPeerName, GetPeerName)
REX_HOOK(AotGetSocketOption, GetOption)
REX_HOOK(AotSetSocketOption, SetOption)
}
namespace aot {
PPCFunc* DatagramImportOverride(uint32_t guest_import) {
  switch (guest_import) {
    case 0x8308B4C4: return AotSendGuestDatagram;
    case 0x8308B494: return AotReceiveGuestDatagram;
    case 0x8308B3D4: return AotSetSocketOption;
    case 0x8308B3E4: return AotGetSocketOption;
    case 0x8308B3F4: return AotGetSocketName;
    case 0x8308B404: return AotGetPeerName;
    default: return nullptr;
  }
}
}
#endif
