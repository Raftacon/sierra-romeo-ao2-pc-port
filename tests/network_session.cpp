#include <winsock2.h>
#include <rex/hook.h>
#include <rex/runtime.h>
#include <rex/system/xevent.h>
#include <rex/system/xio.h>
#include <rex/system/xobject.h>
#include "src/network_keys.h"
#include <array>
#include <cstdio>
#include <cstring>
#include <stdexcept>

REX_EXTERN(sub_82B739B0);
REX_EXTERN(__imp__XamSessionRefObjByHandle);
REX_EXTERN(__imp__XMsgStartIORequest);
REX_EXTERN(__imp__NtClose);

void TestKernelSessions(rex::Runtime& runtime) {
  using rex::X_RESULT;  // SDK status macros refer to the unqualified type.
  auto require = [](bool value, const char* text) { if (!value) throw std::runtime_error(text); };
  auto* memory = runtime.memory(); auto* base = memory->virtual_membase();
  const uint32_t allocation = memory->SystemHeapAlloc(8192);
  require(allocation != 0, "session guest allocation");
  struct Free { rex::memory::Memory* memory; uint32_t address;
    ~Free() { memory->SystemHeapFree(address); }
  } free{memory, allocation};
  auto word = [&](uint32_t offset) -> rex::be<uint32_t>& {
    return *reinterpret_cast<rex::be<uint32_t>*>(base + allocation + offset);
  };
  auto event = rex::system::make_object<rex::system::XEvent>(runtime.kernel_state());
  event->Initialize(true, false);
  auto* overlapped = reinterpret_cast<rex::system::XAM_OVERLAPPED*>(base + allocation + 160);
  auto prepare = [&] {
    std::memset(overlapped, 0, sizeof(*overlapped));
    overlapped->event = event->handle(); overlapped->result = X_ERROR_IO_PENDING;
    event->Reset();
  };
  auto completed = [&] {
    uint32_t type, state; event->Query(&type, &state);
    require(state != 0, "session completion event");
    return uint32_t(overlapped->result);
  };
  auto create = [&](uint32_t flags) {
    prepare();
    PPCContext ctx{};
    ctx.r1.u32 = allocation + 7000; ctx.lr = 0x12345678;
    ctx.r3.u32 = flags; ctx.r4.u32 = 0; ctx.r5.u32 = 1; ctx.r6.u32 = 1;
    ctx.r7.u32 = allocation + 128; ctx.r8.u32 = allocation + 64;
    ctx.r9.u32 = allocation + 160; ctx.r10.u32 = allocation;
    sub_82B739B0(ctx, base);
    require(ctx.r3.u32 == X_ERROR_IO_PENDING && completed() == 0, "retail session creation");
    require(ctx.r1.u32 == allocation + 7000 && ctx.lr == 0x12345678, "session create stack/LR");
    const uint32_t handle = word(0);
    auto object = runtime.kernel_state()->object_table()->LookupObject<rex::system::XObject>(handle);
    require(object && object->type() == rex::system::XObject::Type::Session && object->guest_object(),
            "session did not create a real kernel object");
    return handle;
  };
  // Verify the original placeholder behavior independently of the new handler.
  auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  auto* old = reinterpret_cast<PPCFunc*>(GetProcAddress(module, "__imp__XamSessionCreateHandle"));
  require(old != nullptr, "session negative control export");
  PPCContext old_ctx{}; old_ctx.r3.u32 = allocation; old(old_ctx, base);
  require(word(0) == 0xCAFEDEAD, "session placeholder negative control");
  std::memset(base + allocation + 64, 0xCD, 72);
  const uint32_t host = create(0x21);
  std::array<uint8_t, 60> host_info;
  std::array<uint8_t, 8> host_nonce;
  std::memcpy(host_info.data(), base + allocation + 64, 60);
  std::memcpy(host_nonce.data(), base + allocation + 128, 8);
  std::array<uint8_t, 16> registered_key;
  require(aot::FindNetworkKey(host_info.data(), registered_key) &&
          std::memcmp(registered_key.data(), host_info.data() + 44, 16) == 0, "session key registration");
  require((host_info[0] & 0xF0) == 0, "system-link ID flags");
  const uint32_t other = create(0x21);
  require(other != host && std::memcmp(host_info.data(), base + allocation + 64, 8) != 0 &&
          std::memcmp(host_info.data() + 44, base + allocation + 108, 16) != 0,
          "session identities/keys are not distinct");
  std::memcpy(base + allocation + 64, host_info.data(), 60);
  std::memcpy(base + allocation + 128, host_nonce.data(), 8);
  const uint32_t client = create(0x20);
  require(std::memcmp(base + allocation + 64, host_info.data(), 60) == 0,
          "client session altered host descriptor");
  const uint32_t buffer = allocation + 256, details = allocation + 512;
  auto message = [&](uint32_t handle, uint32_t id, uint32_t length) {
    PPCContext reference{}; reference.r3.u32 = handle; reference.r4.u32 = allocation + 4;
    __imp__XamSessionRefObjByHandle(reference, base);
    require(reference.r3.u32 == 0, "session object reference");
    word(256) = word(4);
    prepare();
    PPCContext ctx{}; ctx.r3.u32 = 0xFB; ctx.r4.u32 = id;
    ctx.r5.u32 = allocation + 160; ctx.r6.u32 = buffer; ctx.r7.u32 = length;
    __imp__XMsgStartIORequest(ctx, base);
    require(ctx.r3.u32 == X_ERROR_IO_PENDING, "session message pending result");
    return completed();
  };
  auto get_details = [&](uint32_t handle = 0) {
    word(260) = 512; word(264) = details;
    require(message(handle ? handle : host, 0xB001D, 24) == 0, "session details");
  };
  get_details();
  require(word(512 + 16) == 1 && word(512 + 20) == 1 && word(512 + 32) == 0 &&
          word(512 + 40) == 0 && std::memcmp(base + details + 56, host_info.data(), 60) == 0 &&
          std::memcmp(base + details + 48, host_nonce.data(), 8) == 0, "initial session details");
  word(260) = 127; word(264) = details;
  require(message(host, 0xB001D, 24) == X_ERROR_INSUFFICIENT_BUFFER, "short session details buffer");
  auto member = [&](bool join, bool local, uint64_t xuid, bool private_slot) {
    word(260) = 1; word(264) = local ? 0 : allocation + 2048;
    word(268) = local ? allocation + 2064 : 0; word(272) = allocation + 2068;
    *reinterpret_cast<rex::be<uint64_t>*>(base + allocation + 2048) = xuid;
    word(2064) = 0; word(2068) = private_slot;
    return message(host, join ? 0xB0012 : 0xB0013, 20);
  };
  require(member(true, true, 0, false) == 0, "join local session member");
  require(member(true, false, 0x1122334455667788ull, true) == 0, "join remote session member");
  get_details();
  require(word(512 + 32) == 2 && word(512 + 36) == 2 && word(512 + 24) == 0 &&
          word(512 + 28) == 0 && word(512 + 124) == details + 128, "joined slot/member accounting");
  bool found_remote = false, found_local = false;
  for (uint32_t offset : {640u, 656u}) {
    const uint64_t xuid = *reinterpret_cast<rex::be<uint64_t>*>(base + allocation + offset);
    if (xuid == 0x1122334455667788ull)
      found_remote = word(offset + 8) == UINT32_MAX && word(offset + 12) == 1;
    else found_local = xuid != 0 && word(offset + 8) == 0 && word(offset + 12) == 0;
  }
  require(found_remote && found_local, "serialized member identities/flags");
  word(260) = 0x20; word(264) = 1; word(268) = 0;
  require(message(host, 0xB0018, 16) == X_ERROR_INVALID_PARAMETER, "modify dropped occupied private slot");
  require(member(true, false, 0x1122334455667788ull, true) == 0, "idempotent member join");
  require(member(true, false, 0x2233445566778899ull, false) != 0, "overfull session accepted");
  get_details(); require(word(512 + 32) == 2, "failed join changed member count");
  require(member(false, false, 0x1122334455667788ull, false) == 0, "leave remote session member");
  get_details(); require(word(512 + 32) == 1 && word(512 + 28) == 1, "leave slot accounting");
  // The first addition fits; the second does not. Neither may be committed.
  word(260) = 2; word(264) = allocation + 2048; word(268) = 0; word(272) = allocation + 2080;
  *reinterpret_cast<rex::be<uint64_t>*>(base + allocation + 2048) = 0x1122334455667788ull;
  *reinterpret_cast<rex::be<uint64_t>*>(base + allocation + 2056) = 0x2233445566778899ull;
  word(2080) = 1; word(2084) = 1;
  require(message(host, 0xB0012, 20) != 0, "overfull batch join");
  get_details(); require(word(512 + 32) == 1 && word(512 + 28) == 1, "batch join partly committed");
  word(260) = 0x20; word(264) = 2; word(268) = 1;
  require(message(host, 0xB0018, 16) == 0, "modify session capacity");
  get_details(); require(word(512 + 16) == 2 && word(512 + 24) == 1 && word(512 + 12) == 0x21,
                         "modified capacity/host flag");
  require(message(host, 0xB001E, 24) == ERROR_CALL_NOT_IMPLEMENTED, "unsupported migration pretended to succeed");
  require(message(host, 0xB0014, 16) == 0, "start session");
  get_details(); require(word(512 + 40) == 2, "in-game session state");
  require(message(host, 0xB0014, 16) != 0, "session started twice");
  require(message(host, 0xB0015, 16) == 0, "end session");
  get_details(); require(word(512 + 40) == 3, "ended session state");
  require(message(host, 0xB0011, 16) == 0, "delete session");
  require(aot::FindNetworkKey(host_info.data(), registered_key), "host deletion revoked another session's key");
  word(260) = 512; word(264) = details;
  require(message(host, 0xB001D, 24) == X_ERROR_INVALID_HANDLE, "deleted session still usable");
  for (uint32_t handle : {host, other, client}) {
    PPCContext close{}; close.r3.u32 = handle; __imp__NtClose(close, base);
    require(close.r3.u32 == 0 && !runtime.kernel_state()->object_table()->LookupObject<rex::system::XObject>(handle),
            "session references leaked after close");
  }
  require(!aot::FindNetworkKey(host_info.data(), registered_key), "session key ownership leaked after close");
  word(4) = 0x12345678;
  PPCContext invalid{}; invalid.r3.u32 = host; invalid.r4.u32 = allocation + 4;
  __imp__XamSessionRefObjByHandle(invalid, base);
  require(invalid.r3.u32 == X_ERROR_INVALID_HANDLE && word(4) == 0x12345678, "stale session handle");
  event->ReleaseHandle();
  std::puts("Retail session create: real handles, unique descriptors, members, slots, lifecycle, completions and close passed");
}
