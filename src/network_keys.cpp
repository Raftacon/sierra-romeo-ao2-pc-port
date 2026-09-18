#ifdef _WIN32
#include <winsock2.h>
#include <bcrypt.h>
#include "network_keys.h"
#include "network_memory.h"
#include <rex/hook.h>
#include <algorithm>
#include <cstring>
#include <map>
#include <mutex>

namespace {
using namespace rex;
using aot::NetworkMemorySpan;
using Id = std::array<uint8_t, 8>;
using Key = std::array<uint8_t, 16>;
using Address = std::array<uint8_t, 36>;
struct Registration {
  Key key{};
  uint32_t owners = 0;
  bool explicit_registration = false;
  ~Registration() { SecureZeroMemory(key.data(), key.size()); }
};
struct Peer { Id id; Address address; };
std::mutex registry_mutex;
std::map<Id, Registration> keys;
// Direct IPv4 destinations only. Ambiguous peers sharing an IP are rejected
// until port-aware endpoint routing exists, rather than returning another peer.
std::map<uint32_t, Peer> peers;
template<size_t N> std::array<uint8_t, N> Bytes(const uint8_t* data) {
  std::array<uint8_t, N> value; std::memcpy(value.data(), data, N); return value;
}
bool Zero(const Id& id) { return std::all_of(id.begin(), id.end(), [](uint8_t b) { return !b; }); }
int Register(const uint8_t* id_data, const uint8_t* key_data, bool owned) {
  const auto id = Bytes<8>(id_data);
  if (Zero(id)) return WSAEINVAL;
  std::lock_guard lock(registry_mutex);
  auto found = keys.find(id);
  if (found != keys.end()) {
    if (std::memcmp(found->second.key.data(), key_data, 16)) return WSAEALREADY;
  } else {
    if (keys.size() >= 256) return WSAENOBUFS;
    found = keys.try_emplace(id).first;
    std::memcpy(found->second.key.data(), key_data, 16);
  }
  if (owned) ++found->second.owners;
  else found->second.explicit_registration = true;
  return 0;
}
void RemoveUnused(std::map<Id, Registration>::iterator found) {
  if (found->second.owners || found->second.explicit_registration) return;
  const auto id = found->first;
  keys.erase(found);
  std::erase_if(peers, [&](const auto& item) { return item.second.id == id; });
}
uint8_t* Base() { return REX_KERNEL_MEMORY()->virtual_membase(); }
u32 Random(u32, mapped_void output, u32 length) {
  if (!NetworkMemorySpan(Base(), output.guest_address(), length, true)) return WSAEFAULT;
  if (!length) return 0;
  return BCryptGenRandom(nullptr, static_cast<uint8_t*>(output), length,
                         BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ? WSAENOBUFS : 0;
}
u32 CreateKey(u32, mapped_void id_output, mapped_void key_output) {
  if (!NetworkMemorySpan(Base(), id_output.guest_address(), 8, true) ||
      !NetworkMemorySpan(Base(), key_output.guest_address(), 16, true)) return WSAEFAULT;
  Id id{}; Key key{};
  if (BCryptGenRandom(nullptr, id.data(), id.size(), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
      BCryptGenRandom(nullptr, key.data(), key.size(), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
    return WSAENOBUFS;
  id[0] &= 0x0F;  // System-link key ID type, independently of peer transport.
  if (Zero(id)) id[7] = 1;
  std::memcpy(static_cast<void*>(id_output), id.data(), id.size());
  std::memcpy(static_cast<void*>(key_output), key.data(), key.size());
  SecureZeroMemory(key.data(), key.size());
  return 0;
}
u32 RegisterKey(u32, mapped_void id, mapped_void key) {
  if (!NetworkMemorySpan(Base(), id.guest_address(), 8, false) ||
      !NetworkMemorySpan(Base(), key.guest_address(), 16, false)) return WSAEFAULT;
  return Register(static_cast<uint8_t*>(id), static_cast<uint8_t*>(key), false);
}
u32 UnregisterKey(u32, mapped_void id) {
  if (!NetworkMemorySpan(Base(), id.guest_address(), 8, false)) return WSAEFAULT;
  std::lock_guard lock(registry_mutex);
  const auto found = keys.find(Bytes<8>(static_cast<uint8_t*>(id)));
  if (found == keys.end() || !found->second.explicit_registration) return WSAEINVAL;
  found->second.explicit_registration = false;
  RemoveUnused(found);
  return 0;
}
u32 ToIpv4(u32, mapped_void address, mapped_void id, mapped_u32 output) {
  if (!NetworkMemorySpan(Base(), address.guest_address(), 36, false) ||
      !NetworkMemorySpan(Base(), id.guest_address(), 8, false) ||
      !NetworkMemorySpan(Base(), output.guest_address(), 4, true)) return WSAEFAULT;
  const auto descriptor = Bytes<36>(static_cast<uint8_t*>(address));
  const auto session_id = Bytes<8>(static_cast<uint8_t*>(id));
  auto ipv4 = [&](size_t offset) {
    return uint32_t(descriptor[offset]) << 24 | uint32_t(descriptor[offset+1]) << 16 |
           uint32_t(descriptor[offset+2]) << 8 | descriptor[offset+3];
  };
  const uint32_t ip = ipv4(4) ? ipv4(4) : ipv4(0);
  if (!(ip >> 24) || (ip >> 28) >= 14) return WSAEADDRNOTAVAIL;
  std::lock_guard lock(registry_mutex);
  if (!keys.contains(session_id)) return WSAEINVAL;
  auto found = peers.find(ip);
  if (found != peers.end() && (found->second.id != session_id || found->second.address != descriptor))
    return WSAEADDRINUSE;
  if (found == peers.end() && peers.size() >= 1024) return WSAENOBUFS;
  peers.insert_or_assign(ip, Peer{session_id, descriptor});
  *output = ip;
  return 0;
}
u32 FromIpv4(u32, u32 ip, mapped_void address, mapped_void id) {
  // IN_ADDR is passed by value by the retail adapter, not a guest pointer.
  if ((address.guest_address() && !NetworkMemorySpan(Base(), address.guest_address(), 36, true)) ||
      (id.guest_address() && !NetworkMemorySpan(Base(), id.guest_address(), 8, true))) return WSAEFAULT;
  std::lock_guard lock(registry_mutex);
  const auto found = peers.find(ip);
  if (found == peers.end()) return WSAEINVAL;
  if (address.guest_address()) std::memcpy(static_cast<void*>(address), found->second.address.data(), 36);
  if (id.guest_address()) std::memcpy(static_cast<void*>(id), found->second.id.data(), 8);
  return 0;
}
u32 Cleanup(u32) { aot::ResetNetworkKeys(); return 0; }
REX_HOOK(AotNetworkRandom, Random)
REX_HOOK(AotNetworkCreateKey, CreateKey)
REX_HOOK(AotNetworkRegisterKey, RegisterKey)
REX_HOOK(AotNetworkUnregisterKey, UnregisterKey)
REX_HOOK(AotNetworkToIpv4, ToIpv4)
REX_HOOK(AotNetworkFromIpv4, FromIpv4)
REX_HOOK(AotNetworkCleanup, Cleanup)
}
namespace aot {
int AcquireSessionNetworkKey(const uint8_t* id, const uint8_t* key) { return Register(id, key, true); }
void ReleaseSessionNetworkKey(const uint8_t* id) {
  std::lock_guard lock(registry_mutex);
  const auto found = keys.find(Bytes<8>(id));
  if (found == keys.end()) return;
  if (found->second.owners) --found->second.owners;
  RemoveUnused(found);
}
bool FindNetworkKey(const uint8_t* id, std::array<uint8_t, 16>& output) {
  std::lock_guard lock(registry_mutex);
  const auto found = keys.find(Bytes<8>(id));
  if (found == keys.end()) return false;
  output = found->second.key;
  return true;
}
void ResetNetworkKeys() {
  std::lock_guard lock(registry_mutex); peers.clear(); keys.clear();
}
PPCFunc* KeyImportOverride(uint32_t address) {
  switch (address) {
    case 0x8308B554: return AotNetworkCleanup;
    case 0x8308B564: return AotNetworkRandom;
    case 0x8308B574: return AotNetworkCreateKey;
    case 0x8308B584: return AotNetworkRegisterKey;
    case 0x8308B594: return AotNetworkUnregisterKey;
    case 0x8308B5A4: return AotNetworkToIpv4;
    case 0x8308B5C4: return AotNetworkFromIpv4;
    default: return nullptr;
  }
}
}
#else
namespace aot { void ResetNetworkKeys() {} }
#endif
