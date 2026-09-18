#ifdef _WIN32
#include <winsock2.h>
#include <bcrypt.h>
#include "network_memory.h"
#include "network_session.h"
#include "network_keys.h"
#include <rex/hook.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xobject.h>
#include <rex/system/xthread.h>
#include <rex/system/xio.h>
#include <rex/system/xam/user_profile.h>
#include <algorithm>
#include <array>
#include <cstring>
#include <mutex>
#include <map>

REX_EXTERN(__imp__NetDll_XNetGetTitleXnAddr);
namespace {
using namespace rex;
using namespace rex::system;
using aot::NetworkMemorySpan;
class Session final : public XObject {
 public:
  static constexpr Type kObjectType = Type::Session;
  explicit Session(KernelState* kernel) : XObject(kernel, kObjectType) {}
  ~Session() override {
    if (key_owned) aot::ReleaseSessionNetworkKey(info.data());
    SecureZeroMemory(info.data() + 44, 16);
    SecureZeroMemory(nonce.data(), nonce.size());
  }
  bool Initialize() {
    // Keep a full dispatch header so normal ObDereferenceObject can find us.
    return CreateNative(sizeof(X_DISPATCH_HEADER)) != nullptr;
  }
  std::mutex mutex;
  bool created = false;
  bool key_owned = false;
  uint32_t flags = 0, user = 0, public_slots = 0, private_slots = 0, state = 0;
  std::array<uint8_t, 60> info{};
  std::array<uint8_t, 8> nonce{};
  struct Member { uint32_t user, flags; };
  std::map<uint64_t, Member> members;
};

u32 CreateHandle(mapped_u32 output) {
  if (!NetworkMemorySpan(REX_KERNEL_MEMORY()->virtual_membase(), output.guest_address(), 4, true))
    return X_ERROR_INVALID_PARAMETER;
  auto session = make_object<Session>(REX_KERNEL_STATE());
  if (!session->Initialize()) { session->ReleaseHandle(); return ERROR_NOT_ENOUGH_MEMORY; }
  *output = session->handle();
  return 0;
}
u32 Reference(u32 handle, mapped_u32 output) {
  if (!NetworkMemorySpan(REX_KERNEL_MEMORY()->virtual_membase(), output.guest_address(), 4, true))
    return X_ERROR_INVALID_PARAMETER;
  auto session = REX_KERNEL_OBJECTS()->LookupObject<Session>(handle);
  if (!session) return X_ERROR_INVALID_HANDLE;
  session->RetainHandle();
  *output = session->guest_object();
  return 0;
}
REX_HOOK(AotSessionCreateHandle, CreateHandle)
REX_HOOK(AotSessionReference, Reference)

uint32_t MessageLength(uint32_t message) {
  switch (message) {
    case 0xB0010: case 0xB001A: return 28;
    case 0xB0012: case 0xB0013: case 0xB0025: case 0xB0026: return 20;
    case 0xB0011: case 0xB0014: case 0xB0015: case 0xB0018: return 16;
    case 0xB001D: case 0xB001E: case 0xB001F: return 24;
    default: return 0;
  }
}

uint32_t Dispatch(uint32_t message, uint32_t buffer, uint32_t length, uint8_t* base) {
  const uint32_t expected = MessageLength(message);
  if ((length && length != expected) || !NetworkMemorySpan(base, buffer, expected, false))
    return X_ERROR_INVALID_PARAMETER;
  auto read = [&](uint32_t address) { return uint32_t(*reinterpret_cast<be<uint32_t>*>(base + address)); };
  const uint32_t object_address = read(buffer);
  if (!NetworkMemorySpan(base, object_address, sizeof(X_DISPATCH_HEADER), false))
    return X_ERROR_INVALID_HANDLE;
  // Resolve only our registered objects; never let GetNativeObject lazily create
  // an event or other object from an arbitrary session pointer.
  auto* header = reinterpret_cast<X_DISPATCH_HEADER*>(base + object_address);
  auto session = REX_KERNEL_OBJECTS()->LookupObject<Session>(header->wait_list_blink);
  if (!session || session->guest_object() != object_address) return X_ERROR_INVALID_HANDLE;
  struct ReleaseReference { Session* session; ~ReleaseReference() { session->ReleaseHandle(); } } release{session.get()};
  std::lock_guard lock(session->mutex);
  if (message == 0xB0010) {
    if (session->created) return X_ERROR_ALREADY_EXISTS;
    const uint32_t flags = read(buffer + 4), pub = read(buffer + 8), priv = read(buffer + 12);
    const uint32_t user = read(buffer + 16), info = read(buffer + 20), nonce = read(buffer + 24);
    if (user != 0) return X_ERROR_NO_SUCH_USER;
    if (uint64_t(pub) + priv > 64 ||
        !NetworkMemorySpan(base, info, 60, true) || !NetworkMemorySpan(base, nonce, 8, true))
      return X_ERROR_INVALID_PARAMETER;
    const bool host = (flags & 1) || !(flags & 0x20);
    std::array<uint8_t, 60> info_value{};
    std::array<uint8_t, 8> nonce_value{};
    if (host) {
      if (BCryptGenRandom(nullptr, info_value.data(), 8, BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
          BCryptGenRandom(nullptr, info_value.data() + 44, 16, BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
          BCryptGenRandom(nullptr, nonce_value.data(), 8, BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
        return X_ERROR_FUNCTION_FAILED;
      info_value[0] = (info_value[0] & 0x0F) | ((flags & 0x1E) ? 0x80 : 0);
      // Reuse the title-address provider. It is still loopback until the PC
      // address/discovery layer is implemented; this creates local state only.
      PPCContext address{}; address.r3.u32 = 1; address.r4.u32 = info + 8;
      __imp__NetDll_XNetGetTitleXnAddr(address, base);
      std::memcpy(info_value.data() + 8, base + info + 8, 36);
    } else {
      std::memcpy(info_value.data(), base + info, 60);
      std::memcpy(nonce_value.data(), base + nonce, 8);
      bool nonzero = false;
      for (size_t i = 0; i < 8; ++i) nonzero |= info_value[i] != 0;
      if (!nonzero) return X_ERROR_INVALID_PARAMETER;
    }
    if (aot::AcquireSessionNetworkKey(info_value.data(), info_value.data() + 44)) return X_ERROR_FUNCTION_FAILED;
    session->key_owned = true;
    session->flags = flags; session->user = host ? user : UINT32_MAX;
    session->public_slots = pub; session->private_slots = priv;
    session->info = info_value; session->nonce = nonce_value;
    session->created = true; session->state = 0;
    std::memcpy(base + info, info_value.data(), 60);
    std::memcpy(base + nonce, nonce_value.data(), 8);
    return 0;
  }
  if (!session->created || session->state == 4) return X_ERROR_INVALID_HANDLE;
  if (message == 0xB0018) {
    const uint32_t pub = read(buffer + 8), priv = read(buffer + 12);
    uint32_t used_private = 0;
    for (const auto& [id, member] : session->members) used_private += member.flags & 1;
    const uint32_t used_public = uint32_t(session->members.size()) - used_private;
    if (uint64_t(pub) + priv > 64 || pub < used_public || priv < used_private)
      return X_ERROR_INVALID_PARAMETER;
    session->flags = (read(buffer + 4) & ~1u) | (session->flags & 1u);
    session->public_slots = pub; session->private_slots = priv;
    return 0;
  }
  if (message == 0xB001A || message == 0xB001E || message == 0xB001F ||
      message == 0xB0025 || message == 0xB0026)
    return ERROR_CALL_NOT_IMPLEMENTED;  // No fabricated arbitration/migration/stats success.
  if (message == 0xB0012 || message == 0xB0013) {
    const bool join = message == 0xB0012;
    const uint32_t count = read(buffer + 4), xuids = read(buffer + 8);
    const uint32_t indices = read(buffer + 12), private_flags = read(buffer + 16);
    if (!count || count > 64 ||
        !NetworkMemorySpan(base, xuids ? xuids : indices, uint64_t(count) * (xuids ? 8 : 4), false) ||
        (join && !NetworkMemorySpan(base, private_flags, uint64_t(count) * 4, false)))
      return X_ERROR_INVALID_PARAMETER;
    // Validate and apply the whole batch to a copy, so a failure cannot partly join it.
    auto members = session->members;
    for (uint32_t i = 0; i < count; ++i) {
      uint32_t user = UINT32_MAX;
      uint64_t xuid;
      if (xuids) xuid = *reinterpret_cast<be<uint64_t>*>(base + xuids + i * 8);
      else {
        user = read(indices + i * 4);
        // The pinned runtime owns one local profile. Do not invent other users.
        if (user != 0) return X_ERROR_NO_SUCH_USER;
        xuid = REX_KERNEL_STATE()->user_profile()->xuid();
      }
      if (!xuid) return X_ERROR_INVALID_PARAMETER;
      if (!join) { members.erase(xuid); continue; }
      if (members.contains(xuid)) continue;
      uint32_t used_private = 0;
      for (const auto& [id, member] : members) used_private += member.flags & 1;
      const uint32_t used_public = uint32_t(members.size()) - used_private;
      const bool use_private = read(private_flags + i * 4) && used_private < session->private_slots;
      if (!use_private && used_public >= session->public_slots) return X_ERROR_FUNCTION_FAILED;
      members.emplace(xuid, Session::Member{user, use_private ? 1u : 0u});
    }
    session->members = std::move(members);
    return 0;
  }
  if (message == 0xB001D) {
    const uint32_t size = read(buffer + 4), output = read(buffer + 8);
    const uint32_t required = 128 + uint32_t(session->members.size()) * 16;
    if (size < required) return X_ERROR_INSUFFICIENT_BUFFER;
    if (!NetworkMemorySpan(base, output, required, true)) return X_ERROR_INVALID_PARAMETER;
    std::memset(base + output, 0, required);
    auto put = [&](uint32_t offset, uint32_t value) { *reinterpret_cast<be<uint32_t>*>(base + output + offset) = value; };
    put(0, session->user); put(12, session->flags);
    put(16, session->public_slots); put(20, session->private_slots);
    uint32_t used_private = 0, member_offset = 128;
    for (const auto& [xuid, member] : session->members) {
      used_private += member.flags & 1;
      *reinterpret_cast<be<uint64_t>*>(base + output + member_offset) = xuid;
      put(member_offset + 8, member.user); put(member_offset + 12, member.flags);
      member_offset += 16;
    }
    const uint32_t count = uint32_t(session->members.size());
    put(24, session->public_slots - (count - used_private)); put(28, session->private_slots - used_private);
    put(32, count); put(36, count); put(124, count ? output + 128 : 0);
    put(40, session->state);
    std::memcpy(base + output + 48, session->nonce.data(), 8);
    std::memcpy(base + output + 56, session->info.data(), 60);
    return 0;
  }
  if (message == 0xB0014) {
    if (session->state != 0) return X_ERROR_FUNCTION_FAILED;
    session->state = 2;
  } else if (message == 0xB0015) {
    if (session->state != 2) return X_ERROR_FUNCTION_FAILED;
    session->state = 3;
  } else {
    session->state = 4;
    if (session->key_owned) aot::ReleaseSessionNetworkKey(session->info.data());
    session->key_owned = false;
  }
  return 0;
}

PPCFunc* Original(const char* name) {
  const auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  auto* result = module ? reinterpret_cast<PPCFunc*>(GetProcAddress(module, name)) : nullptr;
  if (!result) rex::FatalError("Missing session runtime export");
  return result;
}
void Message(PPCContext& ctx, uint8_t* base, PPCFunc* original, bool async) {
  if (ctx.r3.u32 != 0xFB || !MessageLength(ctx.r4.u32)) { original(ctx, base); return; }
  const uint32_t overlap = async ? ctx.r5.u32 : 0;
  if (overlap && !NetworkMemorySpan(base, overlap, sizeof(XAM_OVERLAPPED), true)) {
    ctx.r3.u64 = X_ERROR_INVALID_PARAMETER; return;
  }
  const uint32_t result = Dispatch(ctx.r4.u32, async ? ctx.r6.u32 : ctx.r5.u32,
                                   async ? ctx.r7.u32 : ctx.r6.u32, base);
  if (overlap) REX_KERNEL_STATE()->CompleteOverlappedImmediate(overlap, result);
  XThread::SetLastError(overlap ? 0 : result);
  ctx.r3.u64 = overlap ? X_ERROR_IO_PENDING : result;
}
REX_HOOK_RAW(AotSessionMessage) {
  static auto* original = Original("__imp__XMsgStartIORequest"); Message(ctx, base, original, true);
}
REX_HOOK_RAW(AotSessionMessageEx) {
  static auto* original = Original("__imp__XMsgStartIORequestEx"); Message(ctx, base, original, true);
}
REX_HOOK_RAW(AotSessionInProcess) {
  static auto* original = Original("__imp__XMsgInProcessCall"); Message(ctx, base, original, false);
}
}
namespace aot {
PPCFunc* SessionImportOverride(uint32_t address) {
  switch (address) {
    case 0x8308B6D4: return AotSessionCreateHandle;
    case 0x8308B6C4: return AotSessionReference;
    case 0x8308AA94: return AotSessionMessage;
    case 0x8308B724: return AotSessionMessageEx;
    case 0x8308B674: return AotSessionInProcess;
    default: return nullptr;
  }
}
}
#endif
