#include <winsock2.h>
#include <rex/hook.h>
#include <rex/runtime.h>
#include <rex/system/xsocket.h>
#include "src/network_keys.h"
#include <array>
#include <cstdio>
#include <cstring>
#include <stdexcept>

REX_EXTERN(sub_82B4D110);
REX_EXTERN(sub_82B4D120);
REX_EXTERN(sub_82B4D130);
REX_EXTERN(sub_82B4D140);
REX_EXTERN(sub_82B4D150);
REX_EXTERN(sub_82B4D180);
REX_EXTERN(sub_82B4CFE8);
REX_EXTERN(__imp__NetDll_XNetCleanup);

void TestKernelKeys(rex::Runtime& runtime) {
  auto require = [](bool value, const char* text) { if (!value) throw std::runtime_error(text); };
  aot::ResetNetworkKeys();
  auto* memory = runtime.memory(); auto* base = memory->virtual_membase();
  const uint32_t allocation = memory->SystemHeapAlloc(4096);
  require(allocation != 0, "key guest memory");
  struct Free { rex::memory::Memory* memory; uint32_t address;
    ~Free() { aot::ResetNetworkKeys(); memory->SystemHeapFree(address); }
  } free{memory, allocation};
  const uint32_t id = allocation, key = allocation + 16, descriptor = allocation + 64;
  const uint32_t output_ip = allocation + 112, output_address = allocation + 128, output_id = allocation + 176;
  auto call = [&](PPCFunc* function, uint32_t a = 0, uint32_t b = 0, uint32_t c = 0) {
    PPCContext ctx{}; ctx.r3.u32 = a; ctx.r4.u32 = b; ctx.r5.u32 = c;
    function(ctx, base); return ctx.r3.u32;
  };
  auto word = [&](uint32_t address) -> rex::be<uint32_t>& {
    return *reinterpret_cast<rex::be<uint32_t>*>(base + address);
  };
  // A constant-filled RNG makes independently launched peers share supposedly random values.
  auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  auto* original = reinterpret_cast<PPCFunc*>(GetProcAddress(module, "__imp__NetDll_XNetRandom"));
  require(original != nullptr, "random negative control export");
  PPCContext old{}; old.r3.u32 = 1; old.r4.u32 = allocation + 256; old.r5.u32 = 32;
  original(old, base);
  std::array<uint8_t, 32> constant; constant.fill(0xBB);
  require(std::memcmp(base + allocation + 256, constant.data(), 32) == 0, "pinned RNG negative control");
  require(call(sub_82B4D110, allocation + 256, 32) == 0 &&
          call(sub_82B4D110, allocation + 288, 32) == 0 &&
          std::memcmp(base + allocation + 256, base + allocation + 288, 32) != 0 &&
          std::memcmp(base + allocation + 256, constant.data(), 32) != 0, "retail random bytes");
  require(call(sub_82B4D110, 0, 0) == 0 && call(sub_82B4D110, 0xFFFFFFFE, 8) == WSAEFAULT,
          "random empty/invalid span");
  require(call(sub_82B4D120, id, key) == 0 && (base[id] & 0xF0) == 0, "retail create key");
  const auto saved_id = std::array<uint8_t, 8>{base[id],base[id+1],base[id+2],base[id+3],base[id+4],base[id+5],base[id+6],base[id+7]};
  std::array<uint8_t, 16> saved_key, found_key{};
  std::memcpy(saved_key.data(), base + key, 16);
  require(!aot::FindNetworkKey(saved_id.data(), found_key), "key creation registered implicitly");
  std::memset(base + descriptor, 0, 36);
  word(descriptor) = INADDR_LOOPBACK;
  base[descriptor + 10] = 2; base[descriptor + 15] = 1;
  word(output_ip) = 0xCDCDCDCD;
  require(call(sub_82B4D150, descriptor, id, output_ip) == WSAEINVAL && word(output_ip) == 0xCDCDCDCD,
          "unregistered address accepted or output overwritten");
  require(call(sub_82B4D130, id, key) == 0 && call(sub_82B4D130, id, key) == 0 &&
          aot::FindNetworkKey(saved_id.data(), found_key) && found_key == saved_key, "registered key lookup/idempotence");
  base[key] ^= 1;
  require(call(sub_82B4D130, id, key) == WSAEALREADY &&
          aot::FindNetworkKey(saved_id.data(), found_key) && found_key == saved_key, "conflicting registration replaced key");
  base[key] ^= 1;
  require(call(sub_82B4D150, descriptor, id, output_ip) == 0 && word(output_ip) == INADDR_LOOPBACK,
          "registered peer address translation");
  require(call(sub_82B4D180, INADDR_LOOPBACK, output_address, output_id) == 0 &&
          std::memcmp(base + descriptor, base + output_address, 36) == 0 &&
          std::memcmp(base + id, base + output_id, 8) == 0, "by-value address round trip");
  // An ambiguous address must not silently relabel a packet from another peer.
  base[descriptor + 15] = 2;
  require(call(sub_82B4D150, descriptor, id, output_ip) == WSAEADDRINUSE, "ambiguous peer accepted");
  base[descriptor + 15] = 1;
  require(call(sub_82B4D180, INADDR_LOOPBACK, 0, output_id) == 0, "optional reverse address output");
  word(descriptor + 4) = 0x7F000002;
  require(call(sub_82B4D150, descriptor, id, output_ip) == 0 && word(output_ip) == 0x7F000002 &&
          call(sub_82B4D180, 0x7F000002, output_address, output_id) == 0 &&
          std::memcmp(base + descriptor, base + output_address, 36) == 0, "online address selection/round trip");
  word(descriptor + 4) = 0xE0000001;
  require(call(sub_82B4D150, descriptor, id, output_ip) == WSAEADDRNOTAVAIL && word(output_ip) == 0x7F000002,
          "multicast endpoint validation/output preservation");
  word(descriptor + 4) = 0;
  require(call(sub_82B4D150, descriptor, id, output_ip) == 0, "restore loopback peer selection");
  std::memset(base + output_address, 0xCD, 36);
  require(call(sub_82B4D180, 0x7F000003, output_address, output_id) == WSAEINVAL &&
          base[output_address] == 0xCD, "unknown address output preservation");

  // Use the translated guest IPv4 value in an actual retail sendto call.
  const SOCKET receiver = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  require(receiver != INVALID_SOCKET, "translated endpoint socket");
  struct Close { SOCKET socket; ~Close() { closesocket(socket); } } close{receiver};
  sockaddr_in bound{}; bound.sin_family = AF_INET; bound.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  require(bind(receiver, reinterpret_cast<sockaddr*>(&bound), sizeof(bound)) == 0, "translated endpoint bind");
  int address_size = sizeof(bound);
  require(getsockname(receiver, reinterpret_cast<sockaddr*>(&bound), &address_size) == 0, "translated endpoint name");
  const DWORD timeout = 2000;
  require(setsockopt(receiver, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0,
          "translated endpoint timeout");
  auto sender = rex::system::make_object<rex::system::XSocket>(runtime.kernel_state());
  require(sender->Initialize(rex::system::XSocket::X_AF_INET, rex::system::XSocket::X_SOCK_DGRAM,
                             rex::system::XSocket::X_IPPROTO_UDP) == 0, "translated sender");
  auto* to = reinterpret_cast<rex::system::XSOCKADDR_IN*>(base + allocation + 384);
  std::memset(to, 0, sizeof(*to)); to->sin_family = AF_INET;
  to->sin_addr = word(output_ip); to->sin_port = ntohs(bound.sin_port);
  constexpr std::array<uint8_t, 6> payload{0, 1, 2, 128, 254, 255};
  std::memcpy(base + allocation + 416, payload.data(), payload.size());
  PPCContext send{};
  send.r3.u32 = sender->handle(); send.r4.u32 = allocation + 416; send.r5.u32 = payload.size();
  send.r7.u32 = allocation + 384; send.r8.u32 = sizeof(*to);
  sub_82B4CFE8(send, base);
  require(send.r3.u32 == payload.size(), "send to translated guest address");
  std::array<uint8_t, 32> received{};
  require(recv(receiver, reinterpret_cast<char*>(received.data()), received.size(), 0) == payload.size() &&
          std::memcmp(received.data(), payload.data(), payload.size()) == 0, "translated endpoint payload");
  sender->ReleaseHandle();

  require(call(sub_82B4D140, id) == 0 && !aot::FindNetworkKey(saved_id.data(), found_key), "key unregistration");
  require(call(sub_82B4D180, INADDR_LOOPBACK, output_address, output_id) == WSAEINVAL,
          "unregistered peer mapping remained live");
  require(call(sub_82B4D140, id) == WSAEINVAL, "unknown key unregister result");
  require(call(sub_82B4D130, id, key) == 0 && call(sub_82B4D150, descriptor, id, output_ip) == 0,
          "key re-registration");
  require(call(__imp__NetDll_XNetCleanup, 1) == 0 && !aot::FindNetworkKey(saved_id.data(), found_key) &&
          call(sub_82B4D180, INADDR_LOOPBACK, output_address, output_id) == WSAEINVAL, "XNet cleanup retained state");
  std::puts("Retail XNet: RNG, key lifecycle, reversible IPv4 lookup and translated packet delivery passed");
}
