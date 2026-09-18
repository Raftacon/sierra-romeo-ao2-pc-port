#pragma once
#include <cstdint>
#include <rex/system/xsocket.h>
#include <rex/ppc/function.h>

namespace aot {
// Guest sockaddr fields are big-endian. These routines explicitly translate
// to native Winsock layout, leaving output metadata untouched on failure.
int SendGuestDatagram(uint64_t socket, const uint8_t* data, uint32_t length,
                      uint32_t flags, const rex::system::XSOCKADDR_IN* to, uint32_t to_length);
int ReceiveGuestDatagram(uint64_t socket, uint8_t* data, uint32_t length,
                         uint32_t flags, rex::system::XSOCKADDR_IN* from,
                         rex::be<uint32_t>* from_length);
PPCFunc* DatagramImportOverride(uint32_t guest_import);
}
