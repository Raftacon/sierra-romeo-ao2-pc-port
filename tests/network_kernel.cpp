#include <winsock2.h>
#include <rex/hook.h>
#include <rex/runtime.h>
#include <rex/system/xsocket.h>
#include <rex/system/xthread.h>
#include <rex/system/xevent.h>
#include "src/network_receive.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <thread>

REX_EXTERN(__imp__NetDll_sendto);
REX_EXTERN(__imp__NetDll_recvfrom);
REX_EXTERN(__imp__NetDll_WSAGetLastError);
REX_EXTERN(__imp__NetDll_WSAGetOverlappedResult);
REX_EXTERN(__imp__NetDll_closesocket);
REX_EXTERN(__imp__NetDll_getsockname);
REX_EXTERN(__imp__NetDll_getpeername);
REX_EXTERN(__imp__NetDll_getsockopt);
REX_EXTERN(__imp__NetDll_setsockopt);
REX_EXTERN(__imp__XMsgInProcessCall);
REX_EXTERN(__imp__XMsgStartIORequest);
REX_EXTERN(sub_82B4CF80);  // Original nine-argument WSARecvFrom adapter.
REX_EXTERN(sub_82B4CF38);  // Original WSARecv adapter.
REX_EXTERN(sub_82B4D038);  // Original WSAWaitForMultipleEvents adapter.
REX_EXTERN(sub_83012E40);  // Original network object's receive submission routine.
REX_EXTERN(sub_82B4CF00);  // Original WSAGetOverlappedResult adapter.
REX_EXTERN(sub_82B4CFE8);  // Original sendto adapter.
void TestKernelSessions(rex::Runtime& runtime);
void TestKernelKeys(rex::Runtime& runtime);

namespace {
void Require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
using rex::system::XSocket;
using rex::system::XSOCKADDR_IN;
XSOCKADDR_IN BindLoopback(XSocket& socket) {
  Require(socket.Initialize(XSocket::X_AF_INET, XSocket::X_SOCK_DGRAM, XSocket::X_IPPROTO_UDP) == 0,
          "kernel socket initialization");
  rex::system::N_XSOCKADDR_IN local;
  local.sin_family = AF_INET; local.sin_addr = INADDR_LOOPBACK; local.sin_port = 0;
  std::memset(local.x_sin_zero, 0, sizeof(local.x_sin_zero));
  Require(socket.Bind(&local, sizeof(local)) == 0, "kernel loopback bind");
  sockaddr_in bound{};
  int length = sizeof(bound);
  Require(getsockname(SOCKET(socket.native_handle()), reinterpret_cast<sockaddr*>(&bound), &length) == 0,
          "kernel endpoint lookup");
  const DWORD timeout = 2000;
  Require(setsockopt(SOCKET(socket.native_handle()), SOL_SOCKET, SO_RCVTIMEO,
      reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0, "kernel receive timeout");
  XSOCKADDR_IN guest{};
  guest.sin_family = AF_INET; guest.sin_port = ntohs(bound.sin_port); guest.sin_addr = INADDR_LOOPBACK;
  return guest;
}

void TestSocketMetadata(rex::Runtime& runtime) {
  auto* memory = runtime.memory();
  auto* base = memory->virtual_membase();
  const auto allocation = memory->SystemHeapAlloc(256);
  Require(allocation != 0, "socket metadata memory");
  struct Free { rex::memory::Memory* memory; uint32_t ptr; ~Free() { memory->SystemHeapFree(ptr); } } free{memory, allocation};
  auto left = rex::system::make_object<XSocket>(runtime.kernel_state());
  auto right = rex::system::make_object<XSocket>(runtime.kernel_state());
  const auto left_address = BindLoopback(*left), right_address = BindLoopback(*right);
  auto word = [&](uint32_t offset) -> rex::be<uint32_t>& {
    return *reinterpret_cast<rex::be<uint32_t>*>(base + allocation + offset);
  };
  auto query = [&](PPCFunc* function) {
    PPCContext ctx{}; ctx.r3.u32 = 1; ctx.r4.u32 = left->handle();
    ctx.r5.u32 = allocation; ctx.r6.u32 = allocation + 32;
    function(ctx, base); return ctx.r3.u32;
  };
  word(32) = 16;
  Require(query(__imp__NetDll_getsockname) == 0 && word(32) == 16 &&
      !std::memcmp(base + allocation, &left_address, 16), "guest bound address/ephemeral port");
  word(32) = 15;
  Require(query(__imp__NetDll_getsockname) == UINT32_MAX &&
      rex::system::XThread::GetLastError() == WSAEFAULT && word(32) == 15 &&
      !std::memcmp(base + allocation, &left_address, 16), "short socket name preserves output");
  word(32) = 16;
  Require(query(__imp__NetDll_getpeername) == UINT32_MAX &&
      rex::system::XThread::GetLastError() == WSAENOTCONN &&
      !std::memcmp(base + allocation, &left_address, 16), "unconnected peer error preserves output");
  sockaddr_in peer{}; peer.sin_family = AF_INET;
  peer.sin_port = htons(uint16_t(right_address.sin_port)); peer.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  Require(connect(SOCKET(left->native_handle()), reinterpret_cast<sockaddr*>(&peer), sizeof(peer)) == 0, "native UDP peer connect");
  Require(query(__imp__NetDll_getpeername) == 0 &&
      !std::memcmp(base + allocation, &right_address, 16), "guest connected peer address");
  PPCContext ctx{};
  auto option = [&](PPCFunc* function, bool set) {
    ctx = {}; ctx.r3.u32 = 1; ctx.r4.u32 = left->handle(); ctx.r5.u32 = SOL_SOCKET;
    ctx.r6.u32 = SO_RCVBUF; ctx.r7.u32 = allocation + 64;
    ctx.r8.u32 = set ? 4 : allocation + 68; function(ctx, base); return ctx.r3.u32;
  };
  word(64) = 131072;
  Require(option(__imp__NetDll_setsockopt, true) == 0, "guest socket buffer setter");
  int native_value = 0, native_length = 4;
  Require(getsockopt(SOCKET(left->native_handle()), SOL_SOCKET, SO_RCVBUF,
      reinterpret_cast<char*>(&native_value), &native_length) == 0 && native_value == 131072,
      "socket option setter converts guest byte order");
  word(64) = 0xABCDEF01; word(68) = 16;
  Require(option(__imp__NetDll_getsockopt, false) == 0 && word(64) == uint32_t(native_value) && word(68) == 4,
      "socket option getter converts native byte order and length");
  word(64) = 0xABCDEF01; word(68) = 3;
  Require(option(__imp__NetDll_getsockopt, false) == UINT32_MAX && word(64) == 0xABCDEF01 && word(68) == 3,
      "short socket option preserves output");
  // Exhaust a single message budget before real session messages execute.
  std::memset(base + allocation, 0, 32);
  for (int i = 0; i < 300; ++i) {
    ctx = {}; ctx.r3.u32 = 0xFB; ctx.r4.u32 = 0xB0006;
    ctx.r5.u32 = allocation; ctx.r6.u32 = 24;
    __imp__XMsgInProcessCall(ctx, base);
    ctx = {}; ctx.r3.u32 = 0xFB; ctx.r4.u32 = 0xB0006;
    ctx.r6.u32 = allocation; ctx.r7.u32 = 24;
    __imp__XMsgStartIORequest(ctx, base);
  }
  left->ReleaseHandle(); right->ReleaseHandle();
}

void TestAsyncReceives(rex::Runtime& runtime) {
  auto* memory = runtime.memory();
  auto* base = memory->virtual_membase();
  const uint32_t allocation = memory->SystemHeapAlloc(4096);
  Require(allocation != 0, "async guest memory");
  struct Free {
    rex::memory::Memory* memory; uint32_t address;
    ~Free() { aot::ShutdownNetworkReceive(); memory->SystemHeapFree(address); }
  } free{memory, allocation};
  aot::InitializeNetworkReceive();
  auto left = rex::system::make_object<XSocket>(runtime.kernel_state());
  auto right = rex::system::make_object<XSocket>(runtime.kernel_state());
  auto event = rex::system::make_object<rex::system::XEvent>(runtime.kernel_state());
  event->Initialize(true, false);
  const auto left_address = BindLoopback(*left), right_address = BindLoopback(*right);
  const uint32_t buffers = allocation, received = allocation + 32, flags = allocation + 36;
  const uint32_t from_length = allocation + 40, overlapped = allocation + 48;
  const uint32_t from = allocation + 158;  // Retail's unaligned sockaddr field.
  const uint32_t data1 = allocation + 256, data2 = allocation + 320;
  const uint32_t output_bytes = allocation + 400, output_flags = allocation + 404;
  const uint32_t stack = allocation + 3072;
  auto word = [&](uint32_t address) -> rex::be<uint32_t>& {
    return *reinterpret_cast<rex::be<uint32_t>*>(base + address);
  };
  constexpr std::array<uint8_t, 9> payload{0,1,4,9,16,25,36,49,0xFF};
  auto prepare = [&] {
    word(buffers) = 4; word(buffers + 4) = data1;
    word(buffers + 8) = 60; word(buffers + 12) = data2;
    word(received) = 0xAABBCCDD; word(flags) = 0;
    word(from_length) = 16;
    std::memset(base + overlapped, 0, 20);
    word(overlapped + 16) = event->handle();
    std::memset(base + from, 0xCD, 16);
    std::memset(base + data1, 0, 64); std::memset(base + data2, 0, 64);
  };
  auto receive = [&](bool source, uint32_t callback = 0, bool async = true) {
    PPCContext ctx{};
    ctx.r1.u32 = stack; ctx.lr = 0x12345678;
    ctx.r3.u32 = right->handle(); ctx.r4.u32 = buffers; ctx.r5.u32 = 2;
    ctx.r6.u32 = received; ctx.r7.u32 = flags;
    if (source) {
      ctx.r8.u32 = from; ctx.r9.u32 = from_length; ctx.r10.u32 = async ? overlapped : 0;
      word(stack + 84) = callback;
      sub_82B4CF80(ctx, base);
    } else {
      ctx.r8.u32 = async ? overlapped : 0; ctx.r9.u32 = callback;
      sub_82B4CF38(ctx, base);
    }
    Require(ctx.r1.u32 == stack && ctx.lr == 0x12345678, "retail adapter stack/LR preservation");
    return ctx.r3.u32;
  };
  auto last_error = [&] {
    PPCContext ctx{}; __imp__NetDll_WSAGetLastError(ctx, base); return ctx.r3.u32;
  };
  auto result = [&](bool wait) {
    PPCContext ctx{};
    ctx.r3.u32 = 1; ctx.r4.u32 = right->handle(); ctx.r5.u32 = overlapped;
    ctx.r6.u32 = output_bytes; ctx.r7.u32 = wait; ctx.r8.u32 = output_flags;
    __imp__NetDll_WSAGetOverlappedResult(ctx, base); return ctx.r3.u32;
  };
  auto send = [&] {
    std::memcpy(base + allocation + 512, &right_address, 16);
    std::memcpy(base + allocation + 544, payload.data(), payload.size());
    PPCContext ctx{};
    ctx.r3.u32 = left->handle(); ctx.r4.u32 = allocation + 544; ctx.r5.u32 = payload.size();
    ctx.r7.u32 = allocation + 512; ctx.r8.u32 = 16;
    sub_82B4CFE8(ctx, base);
    Require(ctx.r3.u32 == payload.size(), "async peer send");
  };
  auto check_payload = [&] {
    Require(std::memcmp(base + data1, payload.data(), 4) == 0 &&
            std::memcmp(base + data2, payload.data() + 4, 5) == 0, "scatter/gather payload");
  };
  auto event_state = [&] {
    uint32_t type = 0, state = 0; event->Query(&type, &state); return state;
  };
  auto wait_event = [&] {
    word(allocation + 600) = event->handle();
    PPCContext ctx{};
    ctx.r3.u32 = 1; ctx.r4.u32 = allocation + 600; ctx.r5.u32 = 0; ctx.r6.u32 = 2000;
    sub_82B4D038(ctx, base);
    Require(ctx.r3.u32 == WSA_WAIT_EVENT_0, "guest completion event timeout");
  };
  prepare(); event->Set(0, false);
  Require(receive(true) == UINT32_MAX && last_error() == WSA_IO_PENDING, "pending retail receive");
  Require(word(overlapped) == 0x103 && word(overlapped + 4) == 0 && !event_state(),
          "pending status/event reset");
  Require(word(received) == 0xAABBCCDD && word(flags) == 0 && base[from] == 0xCD,
          "pending receive changed caller outputs");
  word(output_bytes) = 0xABCDEF01; word(output_flags) = 0xABCDEF02;
  Require(result(false) == 0 && last_error() == WSA_IO_INCOMPLETE &&
          word(output_bytes) == 0xABCDEF01 && word(output_flags) == 0xABCDEF02,
          "pending result polling");
  Require(receive(true) == UINT32_MAX && last_error() == WSAEINVAL,
          "duplicate pending overlapped accepted");
  // Descriptions may live on the caller's stack: the implementation must copy them.
  word(buffers) = 0; word(buffers + 4) = 0;
  send(); wait_event();
  Require(result(true) == 1 && word(output_bytes) == payload.size() && word(output_flags) == 0,
          "completed result");
  Require(word(received) == 0xAABBCCDD && word(flags) == 0, "async completion wrote synchronous outputs");
  Require(word(overlapped) == 0 && word(overlapped + 4) == payload.size() &&
          std::memcmp(base + from, &left_address, 16) == 0 && word(from_length) == 16,
          "async status/source publication");
  check_payload(); Require(result(false) == 1, "repeat completion result");

  // Reusing the same completed request and a packet already queued covers immediate I/O.
  prepare(); send();
  const auto immediate = receive(true);
  Require(immediate == 0 || (immediate == UINT32_MAX && last_error() == WSA_IO_PENDING),
          "queued receive result");
  wait_event(); Require(result(true) == 1, "queued receive completion"); check_payload();
  Require(word(received) == (immediate == 0 ? payload.size() : 0xAABBCCDD),
          "immediate bytes publication");

  prepare(); send(); Require(receive(true, 0, false) == 0, "synchronous scatter receive");
  Require(word(received) == payload.size(), "synchronous receive length"); check_payload();
  prepare();
  Require(receive(true, 0x1234) == UINT32_MAX && last_error() == WSAEOPNOTSUPP,
          "unsupported callback silently accepted");
  word(from_length) = 15;
  Require(receive(true) == UINT32_MAX && last_error() == WSAEFAULT && word(from_length) == 15,
          "async short source buffer");
  prepare(); word(buffers + 4) = 0xFFFFFFFE;
  Require(receive(true) == UINT32_MAX && last_error() == WSAEFAULT,
          "wrapped guest buffer accepted");

  // WSARecv has no source-address arguments. Exercise its original retail wrapper too.
  prepare();
  Require(receive(false) == UINT32_MAX && last_error() == WSA_IO_PENDING, "WSARecv pending");
  send(); wait_event(); Require(result(true) == 1, "WSARecv completion"); check_payload();

  // Enter the blocking result path before a delayed peer supplies data. The peer
  // uses native Winsock, since a plain host thread has no guest last-error TLS.
  prepare();
  Require(receive(true) == UINT32_MAX && last_error() == WSA_IO_PENDING, "blocking result pending");
  std::atomic<bool> peer_sent{false};
  std::jthread peer([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    sockaddr_in destination{};
    destination.sin_family = AF_INET;
    destination.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    destination.sin_port = htons(uint16_t(right_address.sin_port));
    peer_sent = sendto(SOCKET(left->native_handle()), reinterpret_cast<const char*>(payload.data()),
        int(payload.size()), 0, reinterpret_cast<const sockaddr*>(&destination), sizeof(destination)) == payload.size();
  });
  Require(result(true) == 1 && word(output_bytes) == payload.size(), "blocking result completion");
  peer.join(); Require(peer_sent, "delayed peer send"); check_payload();

  // Exercise the actual title receive-submission routine, not just API adapters.
  // Its stack WSABUF and pending/error fields must survive the asynchronous return.
  auto control_event = rex::system::make_object<rex::system::XEvent>(runtime.kernel_state());
  control_event->Initialize(true, false);
  const uint32_t object = allocation + 1024;
  for (uint32_t socket_kind : {2u, 3u}) {
    std::memset(base + object, 0, 1444);
    word(object + 12) = socket_kind; word(object + 24) = right->handle();
    word(object + 100) = event->handle();
    PPCContext submit{};
    submit.r1.u32 = stack; submit.r3.u32 = object; submit.lr = 0x12345678;
    sub_83012E40(submit, base);
    Require(submit.r3.u32 == 0 && submit.r1.u32 == stack && submit.lr == 0x12345678 &&
            word(object + 144) == WSA_IO_PENDING && base[object + 156] == 1 &&
            word(object + 176) == UINT32_MAX, "retail submission pending fields");
    word(allocation + 600) = control_event->handle(); word(allocation + 604) = event->handle();
    send();
    PPCContext wait{};
    wait.r3.u32 = 2; wait.r4.u32 = allocation + 600; wait.r6.u32 = 2000;
    sub_82B4D038(wait, base);
    Require(wait.r3.u32 == 1, "retail packet woke wrong event");
    PPCContext complete{};
    complete.r3.u32 = right->handle(); complete.r4.u32 = object + 84;
    complete.r5.u32 = object + 176; complete.r6.u32 = 0; complete.r7.u32 = object + 152;
    sub_82B4CF00(complete, base);
    Require(complete.r3.u32 == 1 && word(object + 176) == payload.size() &&
            std::memcmp(base + object + 180, payload.data(), payload.size()) == 0,
            "retail network object packet completion");
    if (socket_kind == 2)
      Require(std::memcmp(base + object + 158, &left_address, 16) == 0,
              "retail network object source address");
  }
  control_event->ReleaseHandle();

  // Shutdown must release pending native I/O before freeing any guest memory.
  prepare(); Require(receive(true) == UINT32_MAX && last_error() == WSA_IO_PENDING, "shutdown pending");
  aot::ShutdownNetworkReceive();
  Require(event_state() && word(overlapped) != 0x103 && word(overlapped) != 0,
          "shutdown did not cancel/publish pending receive");
  aot::InitializeNetworkReceive();
  prepare(); Require(receive(true) == UINT32_MAX && last_error() == WSA_IO_PENDING, "close pending");
  PPCContext close{}; close.r3.u32 = 1; close.r4.u32 = right->handle();
  __imp__NetDll_closesocket(close, base);
  Require(close.r3.u32 == 0 && event_state() && word(overlapped) != 0x103,
          "socket close did not drain pending receive");
  Require(result(false) == 0 && last_error() == WSAENOTSOCK, "closed socket result");
  aot::ShutdownNetworkReceive();
  left->ReleaseHandle(); event->ReleaseHandle();
  std::puts("Async retail adapters: scatter receive, polling, events, reuse, errors, close and shutdown passed");
}

void TestNetworkEventWaits(rex::Runtime& runtime) {
  auto* memory = runtime.memory();
  auto* base = memory->virtual_membase();
  const uint32_t allocation = memory->SystemHeapAlloc(512);
  Require(allocation != 0, "event array allocation");
  struct Free { rex::memory::Memory* memory; uint32_t address;
    ~Free() { memory->SystemHeapFree(address); }
  } free{memory, allocation};
  auto first = rex::system::make_object<rex::system::XEvent>(runtime.kernel_state());
  auto second = rex::system::make_object<rex::system::XEvent>(runtime.kernel_state());
  first->Initialize(true, false); second->Initialize(true, false);
  auto* handles = reinterpret_cast<rex::be<uint32_t>*>(base + allocation);
  handles[0] = first->handle(); handles[1] = second->handle();
  auto context = [&](uint32_t count, bool all, uint32_t ms, bool alertable = false) {
    PPCContext ctx{};
    ctx.r3.u32 = count; ctx.r4.u32 = allocation; ctx.r5.u32 = all;
    ctx.r6.u32 = ms; ctx.r7.u32 = alertable;
    return ctx;
  };
  auto wait = [&](uint32_t count, bool all, uint32_t ms, bool alertable = false) {
    auto ctx = context(count, all, ms, alertable); sub_82B4D038(ctx, base); return ctx.r3.u32;
  };
  auto last_error = [&] {
    PPCContext ctx{}; __imp__NetDll_WSAGetLastError(ctx, base); return ctx.r3.u32;
  };
  // Deterministic negative control: the pinned DLL reports success for an empty poll.
  auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  auto* original = reinterpret_cast<PPCFunc*>(GetProcAddress(module, "__imp__NetDll_WSAWaitForMultipleEvents"));
  Require(original != nullptr, "SDK wait export");
  auto old = context(2, false, 0); original(old, base);
  Require(old.r3.u32 == 0, "pinned wait negative control changed");
  Require(wait(2, false, 0) == WSA_WAIT_TIMEOUT, "empty wait must time out");
  second->Set(0, false);
  Require(wait(2, false, 0) == 1, "wait-any event index");
  Require(wait(2, true, 0) == WSA_WAIT_TIMEOUT, "wait-all completed with only one event");
  first->Set(0, false);
  Require(wait(2, false, 0) == 0 && wait(2, true, 0) == 0, "first-index/all-event success");
  // Manual-reset events stay signaled until explicitly reset.
  Require(wait(2, true, 0) == 0, "wait consumed a manual-reset event");
  first->Reset(); second->Reset();
  const auto start = std::chrono::steady_clock::now();
  Require(wait(2, false, 60) == WSA_WAIT_TIMEOUT, "finite wait timeout result");
  Require(std::chrono::steady_clock::now() - start >= std::chrono::milliseconds(40),
          "milliseconds were not converted to relative kernel ticks");
  {
    std::jthread signal([&] {
      std::this_thread::sleep_for(std::chrono::milliseconds(20)); second->Set(0, false);
    });
    Require(wait(2, false, WSA_INFINITE) == 1, "delayed infinite wait-any");
  }
  {
    std::jthread signal([&] {
      std::this_thread::sleep_for(std::chrono::milliseconds(20)); first->Set(0, false);
    });
    Require(wait(2, true, 2000) == 0, "delayed wait-all");
  }
  first->Reset(); second->Reset();
  bool callback_ran = false;
  rex::system::XThread::GetCurrentThread()->thread()->QueueUserCallback([&] { callback_ran = true; });
  Require(wait(2, false, 0, false) == WSA_WAIT_TIMEOUT && !callback_ran,
          "non-alertable wait delivered an APC");
  Require(wait(2, false, 2000, true) == WSA_WAIT_IO_COMPLETION && callback_ran,
          "alertable completion result");
  Require(wait(0, false, 0) == WSA_WAIT_FAILED && last_error() == WSA_INVALID_PARAMETER,
          "zero event count");
  Require(wait(65, false, 0) == WSA_WAIT_FAILED && last_error() == WSA_INVALID_PARAMETER,
          "excess event count");
  handles[1] = 0xFFFFFFFF;
  Require(wait(2, false, 0) == WSA_WAIT_FAILED && last_error() == WSA_INVALID_HANDLE,
          "invalid event handle");
  handles[1] = first->handle();
  Require(wait(2, true, 0) == WSA_WAIT_FAILED && last_error() == WSA_INVALID_PARAMETER,
          "duplicate wait-all handle");
  second->Set(0, false);
  for (int i = 0; i < 63; ++i) handles[i] = first->handle();
  handles[63] = second->handle();
  Require(wait(64, false, 0) == 63, "maximum event count or last event index");
  first->ReleaseHandle(); second->ReleaseHandle();
  std::puts("Retail event waits: SDK negative control, any/all, indices, timing, APC and validation passed");
}
}

int TestKernelDatagrams(const std::filesystem::path& output) {
  try {
    WSADATA data{};
    Require(WSAStartup(MAKEWORD(2,2), &data) == 0, "WSA startup");
    struct Cleanup { ~Cleanup() { WSACleanup(); } } cleanup;
    std::filesystem::create_directories(output);
    _putenv_s("AOT_NETWORK_LOG", (output / "network.csv").string().c_str());
    rex::Runtime runtime({}, output);
    rex::RuntimeConfig config;
    config.tool_mode = true;  // No graphics, input or audio factories.
    Require(runtime.Setup(std::move(config)) == 0, "headless runtime setup");
    Require(runtime.is_tool_mode() && !runtime.graphics_system() && !runtime.audio_system() &&
            !runtime.input_system(), "unexpected interactive subsystem");
    std::atomic<bool> passed{false};
    auto worker = rex::system::make_object<rex::system::XHostThread>(runtime.kernel_state(), 65536, 0, [&] {
      try {
        auto* memory = runtime.memory();
        auto* base = memory->virtual_membase();
        const uint32_t allocation = memory->SystemHeapAlloc(512);
        Require(allocation != 0, "guest test memory");
        struct Free {
          rex::memory::Memory* memory; uint32_t address;
          ~Free() { memory->SystemHeapFree(address); }
        } free{memory, allocation};
        auto left = rex::system::make_object<XSocket>(runtime.kernel_state());
        auto right = rex::system::make_object<XSocket>(runtime.kernel_state());
        const auto left_address = BindLoopback(*left), right_address = BindLoopback(*right);
        const uint32_t payload_address = allocation;
        const uint32_t receive_address = allocation + 64;
        const uint32_t endpoint_address = allocation + 160;
        const uint32_t source_address = allocation + 192;
        const uint32_t length_address = allocation + 224;
        constexpr std::array<uint8_t, 9> payload{0,1,4,9,16,25,36,49,0xFF};
        std::memcpy(base + payload_address, payload.data(), payload.size());
        std::memcpy(base + endpoint_address, &right_address, sizeof(right_address));
        auto* guest_length = memory->TranslateVirtual<rex::be<uint32_t>*>(length_address);
        *guest_length = sizeof(XSOCKADDR_IN);
        PPCContext send{}, receive{};
        send.r3.u32 = 1; send.r4.u32 = left->handle(); send.r5.u32 = payload_address;
        send.r6.u32 = payload.size(); send.r7.u32 = 0; send.r8.u32 = endpoint_address;
        send.r9.u32 = sizeof(XSOCKADDR_IN);
        __imp__NetDll_sendto(send, base);
        Require(send.r3.u32 == payload.size(), "guest send import");
        receive.r3.u32 = 1; receive.r4.u32 = right->handle(); receive.r5.u32 = receive_address;
        receive.r6.u32 = 64; receive.r7.u32 = 0; receive.r8.u32 = source_address;
        receive.r9.u32 = length_address;
        __imp__NetDll_recvfrom(receive, base);
        Require(receive.r3.u32 == payload.size(), "guest receive import");
        Require(std::memcmp(base + receive_address, payload.data(), payload.size()) == 0,
                "guest payload mismatch");
        Require(std::memcmp(base + source_address, &left_address, sizeof(left_address)) == 0 &&
                uint32_t(*guest_length) == sizeof(left_address), "guest sockaddr/length mismatch");
        send.r3.u32 = 1; send.r4.u32 = right->handle(); send.r8.u32 = source_address;
        __imp__NetDll_sendto(send, base);
        Require(send.r3.u32 == payload.size(), "guest reply send");
        receive.r3.u32 = 1; receive.r4.u32 = left->handle();
        __imp__NetDll_recvfrom(receive, base);
        Require(receive.r3.u32 == payload.size() &&
                std::memcmp(base + source_address, &right_address, sizeof(right_address)) == 0,
                "guest reply receive");
        // Error result and guest last-error must both survive the typed bridge.
        const auto before = right_address;
        receive.r3.u32 = 1; receive.r4.u32 = 0xFFFFFFFF;
        __imp__NetDll_recvfrom(receive, base);
        Require(receive.r3.u32 == UINT32_MAX, "invalid guest handle result");
        PPCContext error{};
        __imp__NetDll_WSAGetLastError(error, base);
        Require(error.r3.u32 == WSAENOTSOCK, "invalid handle guest last-error");
        Require(std::memcmp(base + source_address, &before, sizeof(before)) == 0,
                "invalid handle changed metadata");
        u_long nonblocking = 1;
        Require(ioctlsocket(SOCKET(right->native_handle()), FIONBIO, &nonblocking) == 0, "kernel nonblocking");
        for (int i = 0; i < 300; ++i) {
          receive.r3.u32 = 1; receive.r4.u32 = right->handle();
          __imp__NetDll_recvfrom(receive, base);
        }
        Require(receive.r3.u32 == UINT32_MAX, "empty guest receive result");
        __imp__NetDll_WSAGetLastError(error, base);
        Require(error.r3.u32 == WSAEWOULDBLOCK, "nonblocking guest last-error");
        send.r3.u32 = 1; send.r4.u32 = left->handle(); send.r8.u32 = endpoint_address;
        __imp__NetDll_sendto(send, base);
        Require(send.r3.u32 == payload.size(), "send after idle poll saturation");
        receive.r3.u32 = 1; receive.lr = 0x82ABC100;
        __imp__NetDll_recvfrom(receive, base);
        Require(receive.r3.u32 == payload.size(), "receive after idle poll saturation");
        *guest_length = sizeof(XSOCKADDR_IN) - 1;
        receive.r3.u32 = 1;
        __imp__NetDll_recvfrom(receive, base);
        Require(receive.r3.u32 == UINT32_MAX, "guest short address buffer result");
        __imp__NetDll_WSAGetLastError(error, base);
        Require(error.r3.u32 == WSAEFAULT && uint32_t(*guest_length) == sizeof(XSOCKADDR_IN) - 1,
                "guest short address error/preservation");
        // Release object-table ownership before the local references go away.
        left->ReleaseHandle(); right->ReleaseHandle();
        TestAsyncReceives(runtime);
        TestNetworkEventWaits(runtime);
        TestKernelKeys(runtime);
        TestSocketMetadata(runtime);
        TestKernelSessions(runtime);
        passed = true;
      } catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); }
      return passed ? 0 : 1;
    });
    Require(worker->Create() == 0, "headless kernel worker");
    uint64_t timeout = uint64_t(-100000000ll);  // Ten seconds, relative 100 ns units.
    Require(worker->Wait(0, 0, 0, &timeout) == 0, "headless kernel worker wait");
    Require(passed, "guest network imports failed");
    std::ifstream trace(output / "network.csv");
    const std::string csv((std::istreambuf_iterator<char>(trace)), {});
    Require(csv.find("0000000082ABC100") != std::string::npos, "successful packet lost after idle trace saturation");
    Require(csv.find(",00000000000B0010,") != std::string::npos &&
        csv.find(",00000000000B0012,") != std::string::npos, "session messages lost after context trace saturation");
    std::puts("Headless kernel datagrams: real guest memory, handles, imports and error state passed");
    return 0;
  } catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); return 1; }
}
