// Forward the unmodified PPC context to the loaded SDK's original exports.
// Opt-in traces cover the observed game thread and original rendering fences.
// Other worker waits and waits inside the host frame hook are excluded.
#include "wait_diagnostics.h"
#include <rex/ppc/context.h>
#ifdef _WIN32
#include <rex/hook.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <windows.h>

namespace {
using Clock = std::chrono::steady_clock;
constexpr std::array names = {"KeWaitForSingleObject", "NtWaitForSingleObjectEx",
    "KeWaitForMultipleObjects", "NtWaitForMultipleObjectsEx", "KeDelayExecutionThread"};
struct WaitStats {
  uint64_t count = 0;
  double total_ms = 0, max_ms = 0;
  uint32_t caller = 0, arg0 = 0, arg1 = 0, result = 0;
  std::array<uint32_t, 4> parents{};
};
std::ofstream output;
std::ofstream render_output;
std::mutex render_mutex;
Clock::time_point origin;
std::atomic<uint64_t> game_frame{0};
std::atomic<DWORD> game_thread{0}, render_thread{0};
thread_local bool active = false;
thread_local bool render_active = false;
thread_local uint64_t render_fence = 0;
thread_local Clock::time_point interval_start;
thread_local unsigned wait_depth = 0;
thread_local uint64_t previous_frame = 0;
thread_local std::array<WaitStats, names.size()> waits{};
double SinceStart(Clock::time_point time) {
  return std::chrono::duration<double, std::milli>(time - origin).count();
}

std::array<uint32_t, 4> SavedCallers(uint32_t stack, uint8_t* base) {
  std::array<uint32_t, 4> result{};
  const auto read = [&](uint32_t address, uint32_t& value) {
    MEMORY_BASIC_INFORMATION info{};
    if (!address || !VirtualQuery(base + address, &info, sizeof(info)) ||
        info.State != MEM_COMMIT || (info.Protect & (PAGE_NOACCESS | PAGE_GUARD)) ||
        base + address + 4 > static_cast<uint8_t*>(info.BaseAddress) + info.RegionSize) return false;
    value = *reinterpret_cast<rex::be<uint32_t>*>(base + address);
    return true;
  };
  for (auto& caller : result) {
    uint32_t previous = 0, saved = 0;
    if (!read(stack, previous) || previous <= stack || previous - stack > 65536 ||
        !read(previous - 8, saved) || saved < 0x82000000 || saved >= 0x83100000) break;
    caller = saved;
    stack = previous;
  }
  return result;
}

PPCFunc* Original(const char* name) {
  // Taking an imported function's address may yield the EXE's import thunk.
  // Use the exact linked runtime filename supplied by CMake instead.
  HMODULE module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  if (!module)
    throw std::runtime_error("Cannot locate the loaded runtime for wait forwarding");
  auto function = reinterpret_cast<PPCFunc*>(GetProcAddress(module, name));
  if (!function) throw std::runtime_error("Original runtime wait export unavailable");
  return function;
}
void Invoke(size_t kind, PPCFunc* original, PPCContext& ctx, uint8_t* base) {
  if ((!active && !render_active) || wait_depth) { original(ctx, base); return; }
  struct Depth { Depth() { ++wait_depth; } ~Depth() { --wait_depth; } } depth;
  const uint32_t caller = static_cast<uint32_t>(ctx.lr), arg0 = ctx.r3.u32, arg1 = ctx.r4.u32;
  const auto start = Clock::now();
  original(ctx, base);
  const double ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
  auto& stat = waits[kind];
  ++stat.count;
  stat.total_ms += ms;
  if (ms > stat.max_ms) {
    stat.max_ms = ms; stat.caller = caller;
    stat.arg0 = arg0; stat.arg1 = arg1; stat.result = ctx.r3.u32;
    stat.parents = SavedCallers(ctx.r1.u32, base);
  }
}
}

namespace aot {
void InitializeWaitDiagnostics() {
  origin = Clock::now();
  if (std::getenv("AOT_WAIT_LOG") || std::getenv("AOT_RENDER_WAIT_LOG")) {
    REXLOG_INFO("Wait diagnostic steady-clock origin_ms={:.6f}",
        std::chrono::duration<double, std::milli>(origin.time_since_epoch()).count());
  }
  if (const char* path = std::getenv("AOT_RENDER_WAIT_LOG")) {
    render_output.open(path);
    render_output.precision(10);
    render_output << "host_thread,fence,observed_game_frame,start_ms,end_ms,interval_ms,api,calls,total_ms,max_ms,max_caller,max_arg0,max_arg1,max_result,parent1,parent2,parent3,parent4\n";
  }
  if (const char* path = std::getenv("AOT_WAIT_LOG")) {
    for (const auto* name : names) {
      const std::string symbol = std::string("__imp__") + name;
      auto* original = Original(symbol.c_str());
      REXLOG_INFO("Wait diagnostic forwards {} to {} at {}", name, AOT_RUNTIME_DLL_NAME,
          reinterpret_cast<const void*>(original));
    }
    output.open(path);
    output.precision(10);
    output << "frame,api,calls,total_ms,max_ms,max_caller,max_arg0,max_arg1,max_result,parent1,parent2,parent3,parent4,start_ms,end_ms\n";
    REXLOG_INFO("Game-thread wait diagnostics: {}", output ? "enabled" : "could not open output");
  }
}
void FinishWaitInterval() {
  if (!active) return;
  active = false;
  const auto end = Clock::now();
  for (size_t i = 0; i < waits.size(); ++i) {
    const auto& s = waits[i];
    if (s.count) {
      output << previous_frame + 1 << ',' << names[i] << ',' << s.count << ','
          << s.total_ms << ',' << s.max_ms << ',' << s.caller << ',' << s.arg0 << ',' << s.arg1 << ',' << s.result;
      for (auto parent : s.parents) output << ',' << parent;
      output << ',' << SinceStart(interval_start) << ',' << SinceStart(end) << '\n';
    }
  }
  if (previous_frame % 60 == 0) output.flush();
  waits = {};
}
void StartWaitInterval(uint64_t frame) {
  if (render_output.is_open()) {
    game_thread.store(GetCurrentThreadId());
    game_frame.store(frame);
  }
  if (!output.is_open()) return;
  previous_frame = frame;
  interval_start = Clock::now();
  active = true;
}
}

void AotRenderFence(PPCRegister& command) {
  if (!render_output.is_open() || !game_thread.load()) return;
  auto* base = rex::runtime::ThreadState::Get()->memory()->virtual_membase();
  // The verified FenceCommandMutex at this site holds its counter/event
  // owner at +4. Follow only the main frame fence, not resource-cleanup fences.
  if (*reinterpret_cast<rex::be<uint32_t>*>(base + command.u32 + 4) != 0x831154E0) return;
  const auto tid = GetCurrentThreadId();
  if (tid == game_thread.load()) return;
  std::lock_guard lock(render_mutex);
  if (render_thread.exchange(tid) != tid)
    REXLOG_INFO("Rendering-fence wait trace: game_thread={}, render_thread={}", game_thread.load(), tid);
  const auto end = Clock::now();
  if (render_active) {
    const double elapsed = std::chrono::duration<double, std::milli>(end - interval_start).count();
    auto prefix = [&] {
      render_output << tid << ',' << render_fence << ',' << game_frame.load() << ',' << SinceStart(interval_start)
          << ',' << SinceStart(end) << ',' << elapsed << ',';
    };
    prefix(); render_output << "interval,0,0,0,0,0,0,0,0,0,0,0\n";
    for (size_t i = 0; i < waits.size(); ++i) {
      const auto& s = waits[i];
      if (!s.count) continue;
      prefix(); render_output << names[i] << ',' << s.count << ',' << s.total_ms << ','
          << s.max_ms << ',' << s.caller << ',' << s.arg0 << ',' << s.arg1 << ',' << s.result;
      for (auto parent : s.parents) render_output << ',' << parent;
      render_output << '\n';
    }
    if (render_fence % 60 == 0) render_output.flush();
  }
  waits = {};
  ++render_fence;
  interval_start = Clock::now();
  render_active = true;
}

#define AOT_WAIT_FORWARD(name, kind) \
  REX_HOOK_RAW(__imp__##name) { \
    static PPCFunc* original = Original("__imp__" #name); \
    Invoke(kind, original, ctx, base); \
  }
AOT_WAIT_FORWARD(KeWaitForSingleObject, 0)
AOT_WAIT_FORWARD(NtWaitForSingleObjectEx, 1)
AOT_WAIT_FORWARD(KeWaitForMultipleObjects, 2)
AOT_WAIT_FORWARD(NtWaitForMultipleObjectsEx, 3)
AOT_WAIT_FORWARD(KeDelayExecutionThread, 4)
#else
void AotRenderFence(PPCRegister&) {}
namespace aot {
void InitializeWaitDiagnostics() {}
void FinishWaitInterval() {}
void StartWaitInterval(uint64_t) {}
}
#endif
