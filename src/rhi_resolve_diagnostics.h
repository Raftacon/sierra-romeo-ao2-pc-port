#pragma once
#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <rex/ppc/context.h>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
#ifdef _WIN32
// Read-only observations around the existing GPU capture trigger. This class
// never changes the context; the caller owns any explicit rendering override.
class RhiResolveDiagnostics {
  using Clock = std::chrono::steady_clock;
  std::mutex mutex_;
  std::ofstream output_;
  std::filesystem::path trigger_;
  Clock::time_point next_check_{}, start_{};
  uint32_t count_ = 0;
  bool configured_ = false, active_ = false, done_ = false;
 public:
  RhiResolveDiagnostics() {
    const auto path = std::getenv("AOT_RHI_RESOLVE_LOG");
    const auto trigger = std::getenv("AOT_RHI_RESOLVE_TRIGGER");
    if (!path || !trigger || !*path || !*trigger) return;
    trigger_ = std::filesystem::u8path(trigger);
    std::error_code error;
    if (std::filesystem::exists(trigger_, error) || error) return;
    output_.open(path);
    if (!output_) return;
    output_ << "call,site,clock_ms,caller,sp,r3,r4,r5,r6,r7,r8,r9,r10,surface0,surface1,surface2";
    for (unsigned i = 0; i < 6; ++i) output_ << ",color_fetch" << i;
    for (unsigned i = 0; i < 6; ++i) output_ << ",depth_fetch" << i;
    for (unsigned i = 0; i < 6; ++i) output_ << ",stack_lr" << i;
    output_ << ",failed_reads\n";
    output_.precision(16);
    configured_ = true;
  }
  void Observe(uint32_t site, const PPCContext& ctx, const uint8_t* base) {
    if (!configured_) return;
    std::lock_guard lock(mutex_);
    if (done_) return;
    const auto now = Clock::now();
    if (!active_) {
      if (now < next_check_) return;
      next_check_ = now + std::chrono::milliseconds(20);
      std::error_code error;
      if (!std::filesystem::exists(trigger_, error) || error) return;
      start_ = now; active_ = true;
    }
    if (count_ >= 8192 || now - start_ >= std::chrono::seconds(2)) {
      output_.close(); done_ = true; return;
    }
    unsigned failures = 0;
    const auto read = [&](uint64_t address) -> uint32_t {
      uint32_t value = 0; SIZE_T received = 0;
      if (address < 0x10000 || address > 0xFFFFFFFC ||
          !ReadProcessMemory(GetCurrentProcess(), base + address, &value, 4, &received) || received != 4) {
        ++failures; return 0;
      }
      return _byteswap_ulong(value);
    };
    std::array<uint32_t, 3> surface{};
    std::array<uint32_t, 6> color{}, depth{}, callers{};
    const auto fetch = [&](uint32_t texture, auto& words) {
      if (texture) for (unsigned i = 0; i < 6; ++i) words[i] = read(uint64_t(texture) + 0x1C + i * 4);
    };
    if (site == 1) {
      for (unsigned i = 0; i < 3; ++i) surface[i] = read(uint64_t(ctx.r3.u32) + i * 4);
      if (surface[1]) fetch(read(uint64_t(surface[1]) + 8), color);
      if (surface[2]) fetch(read(uint64_t(surface[2]) + 8), depth);
    } else if (site == 2) {
      fetch(ctx.r6.u32, color);
    }
    uint32_t sp = ctx.r1.u32;
    for (auto& caller : callers) {
      const uint32_t parent = read(sp);
      if (parent <= sp || uint64_t(parent) > uint64_t(sp) + 0x100000 || (parent & 15)) break;
      caller = read(uint64_t(parent) - 8); sp = parent;
    }
    output_ << count_++ << ',' << site << ','
            << std::chrono::duration<double, std::milli>(now.time_since_epoch()).count()
            << ',' << uint32_t(ctx.lr) << ',' << ctx.r1.u32;
    for (auto v : {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, ctx.r8.u32, ctx.r9.u32, ctx.r10.u32}) output_ << ',' << v;
    for (auto v : surface) output_ << ',' << v;
    for (auto v : color) output_ << ',' << v;
    for (auto v : depth) output_ << ',' << v;
    for (auto v : callers) output_ << ',' << v;
    output_ << ',' << failures << '\n';
  }
};
inline void ObserveRhiResolve(uint32_t site, const PPCContext& ctx, const uint8_t* base) {
  static RhiResolveDiagnostics trace;
  trace.Observe(site, ctx, base);
}
#else
inline void ObserveRhiResolve(uint32_t, const PPCContext&, const uint8_t*) {}
#endif
}
