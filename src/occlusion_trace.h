#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace aot {
inline thread_local uint32_t occlusion_flush_reason = 0;
struct OcclusionBarrierScope {
  uint32_t previous;
  explicit OcclusionBarrierScope(uint32_t reason) : previous(occlusion_flush_reason) {
    occlusion_flush_reason = reason;
  }
  ~OcclusionBarrierScope() { occlusion_flush_reason = previous; }
};
// GPU command-processor thread only. Opt-in, bounded diagnostics; never changes
// query state or samples. Flush each row so a normally hard-exiting game retains it.
inline void TraceOcclusion(const char* event, uint64_t frame, uint32_t address,
                           bool active, bool available, uint32_t index,
                           uint64_t value = 0, uint64_t extra = 0) {
  struct Trace {
    FILE* file = nullptr;
    unsigned rows = 0;
    unsigned failures = 0;
    Trace() {
      if (const char* path = std::getenv("AOT_OCCLUSION_LOG")) {
        file = std::fopen(path, "w");
        if (file) std::fputs("event,frame,address,active,available,index,value,extra\n", file);
      }
    }
    ~Trace() { if (file) std::fclose(file); }
  };
  static Trace trace;
  if (!trace.file) return;
  const bool failure = std::strcmp(event, "disable") == 0 ||
                       std::strcmp(event, "submission_closes_query") == 0;
  if (failure) {
    // Reserve failure coverage even after ordinary samples hit their cap.
    if (trace.failures++ >= 256) return;
  } else if (trace.rows >= 65536) return;
  // Keep startup detail, then complete query groups on every 300th GPU frame.
  // Always retain failure transitions; a long menu must not exhaust the trace
  // before the checkpoint loads. This is sampled coverage, not a query census.
  if (trace.rows >= 256 && frame % 300 != 0 && !failure) return;
  std::fprintf(trace.file, "%s,%llu,%u,%u,%u,%u,%llu,%llu\n", event,
      static_cast<unsigned long long>(frame), address, active ? 1u : 0u,
      available ? 1u : 0u, index, static_cast<unsigned long long>(value),
      static_cast<unsigned long long>(extra));
  ++trace.rows;
  std::fflush(trace.file);
}
} // namespace aot
