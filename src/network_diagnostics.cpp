// Observe guest network/session imports and the corrected PC datagram path.
// No payload or pointed-to guest memory is read by this diagnostic.
#ifdef _WIN32
#include <rex/hook.h>
#include "network_datagram.h"
#include "network_receive.h"
#include "network_session.h"
#include "network_keys.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <stdexcept>
#include <windows.h>

namespace {
enum Import {
#define AOT_NETWORK_IMPORT(address, name) k##name,
#include "network_imports.inc"
#undef AOT_NETWORK_IMPORT
  kCount
};
using Clock = std::chrono::steady_clock;
struct NetworkTrace {
  FILE* file = nullptr;
  Clock::time_point origin = Clock::now();
  // Successful packets remain observable after a long idle receive poll.
  // Each XGI message gets its own budget: audio/status traffic shares XMsg.
  std::array<std::atomic<uint32_t>, kCount * 2 + 3 * 256> counts{};
  std::mutex mutex;
  NetworkTrace() {
    if (const char* path = std::getenv("AOT_NETWORK_LOG"); path && *path) {
      file = std::fopen(path, "w");
      if (file) {
        std::fputs("api,thread,call,caller,r3,r4,r5,r6,r7,r8,result,start_ms,duration_ms\n", file);
        std::fflush(file);
      }
    }
  }
  ~NetworkTrace() { if (file) std::fclose(file); }
};
PPCFunc* Original(const char* name) {
  const HMODULE module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  auto* original = module ? reinterpret_cast<PPCFunc*>(GetProcAddress(module, name)) : nullptr;
  if (!original) throw std::runtime_error("Original runtime network export unavailable");
  return original;
}
void Invoke(Import kind, const char* name, PPCFunc* original, PPCContext& ctx,
            uint8_t* base, DWORD entry_error) {
  static NetworkTrace trace;
  if (!trace.file) {
    SetLastError(entry_error);
    original(ctx, base);
    return;
  }
  const uint64_t caller = ctx.lr;
  const std::array<uint64_t, 6> args{ctx.r3.u64, ctx.r4.u64, ctx.r5.u64,
                                    ctx.r6.u64, ctx.r7.u64, ctx.r8.u64};
  const DWORD thread = GetCurrentThreadId();
  const auto start = Clock::now();
  SetLastError(entry_error);
  original(ctx, base);
  const DWORD exit_error = GetLastError();
  const auto end = Clock::now();
  size_t bucket = size_t(kind) * 2 + (ctx.r3.u32 == UINT32_MAX);
  if (args[0] == 0xFB && args[1] >= 0xB0000 && args[1] < 0xB0100) {
    const int message_api = kind == kXMsgInProcessCall ? 0 :
        kind == kXMsgStartIORequest ? 1 : kind == kXMsgStartIORequestEx ? 2 : -1;
    if (message_api >= 0) bucket = kCount * 2 + message_api * 256 + (args[1] & 255);
  }
  auto& counter = trace.counts[bucket];
  uint32_t call = counter.load(std::memory_order_relaxed);
  do {
    if (call >= 256) { SetLastError(exit_error); return; }
  } while (!counter.compare_exchange_weak(call, call + 1, std::memory_order_relaxed));
  {
    std::lock_guard lock(trace.mutex);
    std::fprintf(trace.file, "%s,%lu,%u,%016llX", name, thread, call + 1,
                 static_cast<unsigned long long>(caller));
    for (auto arg : args) std::fprintf(trace.file, ",%016llX", static_cast<unsigned long long>(arg));
    std::fprintf(trace.file, ",%016llX,%.6f,%.6f\n", static_cast<unsigned long long>(ctx.r3.u64),
        std::chrono::duration<double, std::milli>(start - trace.origin).count(),
        std::chrono::duration<double, std::milli>(end - start).count());
    std::fflush(trace.file);
  }
  SetLastError(exit_error);
}
}  // namespace

#define AOT_NETWORK_IMPORT(address, name) \
  REX_HOOK_RAW(__imp__##name) { \
    const DWORD entry_error = GetLastError(); \
    static PPCFunc* original = aot::KeyImportOverride(address) ? aot::KeyImportOverride(address) : \
        (aot::SessionImportOverride(address) ? aot::SessionImportOverride(address) : \
        (aot::ReceiveImportOverride(address) ? aot::ReceiveImportOverride(address) : \
        (aot::DatagramImportOverride(address) ? aot::DatagramImportOverride(address) : Original("__imp__" #name)))); \
    Invoke(k##name, #name, original, ctx, base, entry_error); \
  }
#include "network_imports.inc"
#undef AOT_NETWORK_IMPORT
#endif
