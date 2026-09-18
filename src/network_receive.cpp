#ifdef _WIN32
#include <winsock2.h>
#include "network_receive.h"
#include "network_memory.h"
#include <rex/hook.h>
#include <rex/system/xevent.h>
#include <rex/system/xsocket.h>
#include <rex/system/xthread.h>
#include <rex/kernel/xboxkrnl/error.h>
#include <array>
#include <condition_variable>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

namespace {
using namespace rex;
using namespace rex::system;
struct GuestBuffer { be<uint32_t> length, address; };
struct GuestOverlapped { be<uint32_t> status, bytes, offset_low, offset_high, event; };
static_assert(sizeof(GuestBuffer) == 8 && sizeof(GuestOverlapped) == 20);

bool Span(uint8_t* base, uint32_t address, uint64_t length, bool write) {
  return aot::NetworkMemorySpan(base, address, length, write);
}

int Error(int code) { XThread::SetLastError(code); return SOCKET_ERROR; }

struct Receive {
  object_ref<XSocket> socket;
  object_ref<XEvent> event;
  OVERLAPPED native{};
  std::vector<WSABUF> buffers;
  sockaddr_in source{};
  int source_length = sizeof(source);
  DWORD flags = 0;
  GuestOverlapped* guest = nullptr;
  XSOCKADDR_IN* from = nullptr;
  be<uint32_t>* from_length = nullptr;
  std::mutex mutex;
  std::condition_variable completed;
  bool done = false;
  DWORD error = 0, bytes = 0;
  std::thread worker;
  ~Receive() {
    if (worker.joinable()) worker.join();
    if (native.hEvent) CloseHandle(native.hEvent);
  }
  void Finish(DWORD result_error, DWORD result_bytes, DWORD result_flags) {
    std::lock_guard lock(mutex);
    error = result_error; bytes = result_bytes; flags = result_flags;
    if (!error && from) {
      from->sin_family = source.sin_family;
      from->sin_port = ntohs(source.sin_port);
      from->sin_addr = ntohl(source.sin_addr.s_addr);
      std::memset(from->x_sin_zero, 0, sizeof(from->x_sin_zero));
      *from_length = sizeof(*from);
    }
    if (guest) {
      guest->bytes = bytes;
      guest->status = uint32_t(native.Internal);
    }
    done = true;
    if (event) event->Set(0, false);
    completed.notify_all();
  }
  void AwaitNative() {
    DWORD result_bytes = 0, result_flags = 0;
    const BOOL ok = WSAGetOverlappedResult(SOCKET(socket->native_handle()), &native,
                                          &result_bytes, TRUE, &result_flags);
    const DWORD result_error = ok ? 0 : WSAGetLastError();
    Finish(result_error, result_bytes, result_flags);
  }
  bool Done() { std::lock_guard lock(mutex); return done; }
  void CancelAndDrain() {
    if (!Done()) {
      CancelIoEx(reinterpret_cast<HANDLE>(socket->native_handle()), &native);
      std::unique_lock lock(mutex);
      completed.wait(lock, [&] { return done; });
    }
    if (worker.joinable()) worker.join();
  }
};

std::mutex receives_mutex;
std::map<uint32_t, std::shared_ptr<Receive>> receives;
bool stopping = false;

int StartReceive(uint32_t handle, uint32_t buffers_address, uint32_t count,
                  uint32_t bytes_address, uint32_t flags_address, uint32_t from_address,
                  uint32_t from_length_address, uint32_t overlapped_address, uint32_t callback,
                  bool with_source, uint8_t* base) {
  // The pinned retail caller uses an event/polling completion, not an APC.
  if (callback) return Error(WSAEOPNOTSUPP);
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) return Error(WSAENOTSOCK);
  if (!count || count > 64) return Error(WSAEINVAL);
  if (!Span(base, buffers_address, uint64_t(count) * sizeof(GuestBuffer), false) ||
      !Span(base, flags_address, 4, true) ||
      ((!overlapped_address || bytes_address) && !Span(base, bytes_address, 4, true)) ||
      (overlapped_address && !Span(base, overlapped_address, sizeof(GuestOverlapped), true)) ||
      (from_address && (!Span(base, from_address, sizeof(XSOCKADDR_IN), true) ||
                        !Span(base, from_length_address, 4, true)))) return Error(WSAEFAULT);
  auto* from_length = from_address ? reinterpret_cast<be<uint32_t>*>(base + from_length_address) : nullptr;
  if (from_length && uint32_t(*from_length) < sizeof(XSOCKADDR_IN)) return Error(WSAEFAULT);
  auto request = std::make_shared<Receive>();
  request->socket = socket;
  request->flags = *reinterpret_cast<be<uint32_t>*>(base + flags_address);
  request->from = from_address ? reinterpret_cast<XSOCKADDR_IN*>(base + from_address) : nullptr;
  request->from_length = from_length;
  auto* descriptions = reinterpret_cast<const GuestBuffer*>(base + buffers_address);
  uint64_t capacity = 0;
  for (uint32_t i = 0; i < count; ++i) {
    const uint32_t length = descriptions[i].length, address = descriptions[i].address;
    capacity += length;
    if (capacity > UINT32_MAX) return Error(WSAEMSGSIZE);
    if (!Span(base, address, length, true)) return Error(WSAEFAULT);
    request->buffers.push_back({length, reinterpret_cast<char*>(base + address)});
  }
  if (overlapped_address) {
    request->guest = reinterpret_cast<GuestOverlapped*>(base + overlapped_address);
    const uint32_t event = request->guest->event;
    if (event) {
      request->event = REX_KERNEL_OBJECTS()->LookupObject<XEvent>(event);
      if (!request->event) return Error(WSAEINVAL);
    }
    request->native.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!request->native.hEvent) return Error(WSAENOBUFS);
  }
  std::unique_lock lock(receives_mutex);
  if (stopping) return Error(WSA_OPERATION_ABORTED);
  // A close holds this lock through cancellation and handle removal.
  if (REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle).get() != socket.get()) return Error(WSAENOTSOCK);
  if (overlapped_address) {
    auto old = receives.find(overlapped_address);
    if (old != receives.end() && !old->second->Done()) return Error(WSAEINVAL);
    size_t pending = 0;
    for (const auto& [address, item] : receives) if (!item->Done()) ++pending;
    if (pending >= 64) return Error(WSAENOBUFS);
    if (old != receives.end()) receives.erase(old);
    receives.emplace(overlapped_address, request);
  }
  DWORD bytes = 0;
  // Non-overlapped receives may block. Do not hold the lifecycle mutex during
  // them, so another thread can close the socket and release the OS wait.
  if (!overlapped_address) lock.unlock();
  const int result = with_source ?
      WSARecvFrom(SOCKET(socket->native_handle()), request->buffers.data(), count, &bytes,
                  &request->flags, request->from ? reinterpret_cast<sockaddr*>(&request->source) : nullptr,
                  request->from ? &request->source_length : nullptr,
                  overlapped_address ? &request->native : nullptr, nullptr) :
      WSARecv(SOCKET(socket->native_handle()), request->buffers.data(), count, &bytes,
              &request->flags, overlapped_address ? &request->native : nullptr, nullptr);
  const DWORD error = result == SOCKET_ERROR ? WSAGetLastError() : 0;
  if (error && error != WSA_IO_PENDING) {
    if (overlapped_address) receives.erase(overlapped_address);
    return Error(error);
  }
  if (request->guest) {
    request->guest->status = 0x103;  // STATUS_PENDING, not the Win32 error 997.
    request->guest->bytes = 0;
    if (request->event) request->event->Reset();
  }
  if (!error) {
    request->Finish(0, bytes, request->flags);
    if (bytes_address) *reinterpret_cast<be<uint32_t>*>(base + bytes_address) = bytes;
    *reinterpret_cast<be<uint32_t>*>(base + flags_address) = request->flags;
    return 0;
  }
  try {
    request->worker = std::thread([item = request.get()] { item->AwaitNative(); });
  } catch (...) {
    CancelIoEx(reinterpret_cast<HANDLE>(socket->native_handle()), &request->native);
    request->AwaitNative();
    receives.erase(overlapped_address);
    return Error(WSAENOBUFS);
  }
  return Error(WSA_IO_PENDING);
}

u32 GetResult(u32, u32 handle, u32 overlapped, mapped_u32 bytes, u32 wait, mapped_u32 flags) {
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(handle);
  if (!socket) { Error(WSAENOTSOCK); return 0; }
  auto* base = REX_KERNEL_MEMORY()->virtual_membase();
  if (!Span(base, bytes.guest_address(), 4, true) || !Span(base, flags.guest_address(), 4, true)) {
    Error(WSAEFAULT); return 0;
  }
  std::shared_ptr<Receive> request;
  {
    std::lock_guard lock(receives_mutex);
    auto found = receives.find(overlapped);
    if (found != receives.end() && found->second->socket.get() == socket.get()) request = found->second;
  }
  if (!request) { Error(WSAEINVAL); return 0; }
  std::unique_lock lock(request->mutex);
  if (!request->done && !wait) { Error(WSA_IO_INCOMPLETE); return 0; }
  request->completed.wait(lock, [&] { return request->done; });
  if (request->error) { Error(request->error); return 0; }
  *bytes = request->bytes; *flags = request->flags;
  return 1;
}
REX_HOOK(AotReceiveGetResult, GetResult)

u32 WaitEvents(u32 count, mapped_u32 handles, u32 wait_all, u32 milliseconds, u32 alertable) {
  if (!count || count > WSA_MAXIMUM_WAIT_EVENTS) return uint32_t(Error(WSA_INVALID_PARAMETER));
  auto* base = REX_KERNEL_MEMORY()->virtual_membase();
  if (!Span(base, handles.guest_address(), uint64_t(count) * 4, false))
    return uint32_t(Error(WSAEFAULT));
  std::array<object_ref<XObject>, WSA_MAXIMUM_WAIT_EVENTS> owners;
  std::array<XObject*, WSA_MAXIMUM_WAIT_EVENTS> objects{};
  for (uint32_t i = 0; i < count; ++i) {
    owners[i] = REX_KERNEL_OBJECTS()->LookupObject<XObject>(handles[i]);
    if (!owners[i]) return uint32_t(Error(WSA_INVALID_HANDLE));
    // Avoid the SDK WaitMultiple assertion for objects with no wait handle.
    switch (owners[i]->type()) {
      case XObject::Type::Event: case XObject::Type::File:
      case XObject::Type::Mutant: case XObject::Type::NotifyListener:
      case XObject::Type::Semaphore: case XObject::Type::Thread:
      case XObject::Type::Timer: break;
      default: return uint32_t(Error(WSA_INVALID_HANDLE));
    }
    objects[i] = owners[i].get();
    if (wait_all) {
      for (uint32_t j = 0; j < i; ++j)
        if (objects[j] == objects[i]) return uint32_t(Error(WSA_INVALID_PARAMETER));
    }
  }
  // The kernel expects relative 100 ns ticks and WaitAny=1, WaitAll=0.
  // Winsock instead supplies milliseconds and a TRUE-for-all boolean.
  uint64_t timeout = uint64_t(-int64_t(milliseconds) * 10000);
  const auto status = XObject::WaitMultiple(count, objects.data(), wait_all ? 0 : 1,
      6, 1, alertable != 0, milliseconds == WSA_INFINITE ? nullptr : &timeout);
  if (status == X_STATUS_USER_APC) XThread::GetCurrentThread()->DeliverAPCs();
  if (XFAILED(status)) return uint32_t(Error(rex::kernel::xboxkrnl::xeRtlNtStatusToDosError(status)));
  // Preserve the event index, timeout (258) and APC completion (192).
  return status;
}
REX_HOOK(AotReceiveWaitEvents, WaitEvents)

PPCFunc* OriginalClose() {
  auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  return module ? reinterpret_cast<PPCFunc*>(GetProcAddress(module, "__imp__NetDll_closesocket")) : nullptr;
}
REX_HOOK_RAW(AotReceiveCloseSocket) {
  static auto* original = OriginalClose();
  if (!original) rex::FatalError("Missing runtime socket close export");
  auto socket = REX_KERNEL_OBJECTS()->LookupObject<XSocket>(ctx.r4.u32);
  std::lock_guard lock(receives_mutex);
  if (socket) {
    for (auto it = receives.begin(); it != receives.end();) {
      if (it->second->socket.get() == socket.get()) {
        it->second->CancelAndDrain();
        it = receives.erase(it);
      } else ++it;
    }
  }
  original(ctx, base);
}
REX_HOOK_RAW(AotReceiveFromBuffers) {
  // Ten arguments: from-length is r10, overlapped and callback are stack args.
  using rex::ppc::ArgTranslator;
  ctx.r3.u64 = uint32_t(StartReceive(ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32,
      ctx.r8.u32, ctx.r9.u32, ctx.r10.u32,
      uint32_t(ArgTranslator::GetIntegerArgumentValue(ctx, base, 8)),
      uint32_t(ArgTranslator::GetIntegerArgumentValue(ctx, base, 9)), true, base));
}
REX_HOOK_RAW(AotReceiveBuffers) {
  ctx.r3.u64 = uint32_t(StartReceive(ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32,
      ctx.r8.u32, 0, 0, ctx.r9.u32, ctx.r10.u32, false, base));
}
}  // namespace

namespace aot {
PPCFunc* ReceiveImportOverride(uint32_t guest_import) {
  switch (guest_import) {
    case 0x8308B4A4: return AotReceiveFromBuffers;
    case 0x8308B484: return AotReceiveBuffers;
    case 0x8308B464: return AotReceiveGetResult;
    case 0x8308B3A4: return AotReceiveCloseSocket;
    case 0x8308B534: return AotReceiveWaitEvents;
    default: return nullptr;
  }
}
void ShutdownNetworkReceive() {
  std::lock_guard lock(receives_mutex);
  stopping = true;
  for (auto& [address, request] : receives) request->CancelAndDrain();
  receives.clear();
}
void InitializeNetworkReceive() {
  ShutdownNetworkReceive();
  std::lock_guard lock(receives_mutex);
  stopping = false;
}
}
#else
namespace aot { void InitializeNetworkReceive() {} void ShutdownNetworkReceive() {} }
#endif
