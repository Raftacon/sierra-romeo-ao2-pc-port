// Compile production query lifecycle with a command-list model that rejects
// queries crossing submissions and delays readback publication until a fence.
#include <array>
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>
#include "../src/occlusion_trace.h"

void check(bool ok, const char* what) { if (!ok) throw std::runtime_error(what); }
bool occlusion_query_enable = true;
bool aot_batch_occlusion_queries = false;
#define REXCVAR_GET(name) name
constexpr int D3D12_QUERY_TYPE_OCCLUSION = 0;
namespace xenos { struct xe_gpu_depth_sample_counts { uint32_t samples = 0; uint32_t other[7]{}; }; }
struct Resource { void* Get() { return this; } explicit operator bool() const { return true; } };
namespace rex { [[noreturn]] void FatalError(const char* message) { throw std::runtime_error(message); } }
struct Base { virtual void PrepareForWait() {} };
struct TextureCache {
  uint32_t scale = 1;
  uint32_t draw_resolution_scale_x() const { return scale; }
  uint32_t draw_resolution_scale_y() const { return scale; }
};
#include "occlusion_opcodes.inc"
struct Commands {
  std::array<uint64_t, 8> pending{};
  uint32_t active = UINT32_MAX;
  uint64_t samples = 0;
  unsigned began = 0, ended = 0;
  void D3DBeginQuery(void*, int, uint32_t index) {
    check(active == UINT32_MAX, "nested host query"); active = index; samples = 0; ++began;
  }
  void Draw(uint64_t count) { if (active != UINT32_MAX) samples += count; }
  void D3DEndQuery(void*, int, uint32_t index) {
    check(active == index, "unmatched host end"); pending[index] = samples; active = UINT32_MAX; ++ended;
  }
  void D3DResolveQueryData(void*, int, uint32_t index, unsigned n, void*, uint64_t offset) {
    check(active == UINT32_MAX && n == 1 && offset == index * 8, "bad query resolve");
  }
};
struct D3D12CommandProcessor : Base {
  static constexpr unsigned kMaxOcclusionQueries = 8;
  struct Active { uint32_t sample_count_address = 0, host_index = UINT32_MAX; bool valid = false; } active_occlusion_query_;
  Resource occlusion_query_heap_, occlusion_query_readback_;
  bool occlusion_query_resources_available_ = true;
  uint32_t occlusion_query_cursor_ = 0;
  std::vector<uint32_t> aot_occlusion_draw_indices_;
#include "../src/occlusion_pending_members.inc"
  TextureCache texture;
  TextureCache* texture_cache_ = &texture;
  uint64_t frame_current_ = 1, submission_current_ = 1, submission_completed_ = 0;
  Commands deferred_command_list_;
  std::array<uint64_t, 8> visible{};
  uint64_t* occlusion_query_readback_mapping_ = visible.data();
  unsigned awaits = 0;
  bool fail_fence = false, fail_submission = false;
  bool AcquireOcclusionQueryIndex(uint32_t& index) {
    if (occlusion_query_cursor_ == kMaxOcclusionQueries) occlusion_query_cursor_ = 0;
    index = occlusion_query_cursor_++; return true;
  }
  bool EndSubmission(bool) {
    check(deferred_command_list_.active == UINT32_MAX, "host query crossed command-list boundary");
    if (fail_submission) return false;
    ++submission_current_; return true;
  }
  bool CheckSubmissionFence(uint64_t submission) {
    ++awaits;
    if (fail_fence) return false;
    submission_completed_ = submission; visible = deferred_command_list_.pending; return true;
  }
  uint64_t NormalizeOcclusionSamples(uint64_t samples) { return samples; }
  void WriteGuestOcclusionResult(xenos::xe_gpu_depth_sample_counts* counts, uint64_t samples) {
    counts->samples = uint32_t(samples > UINT32_MAX ? UINT32_MAX : samples);
  }
  void DisableHostOcclusionQueries();
  bool BeginGuestOcclusionQuery(uint32_t);
  uint32_t BeginOcclusionQueryDraw();
  void EndOcclusionQueryDraw(uint32_t);
  bool EndGuestOcclusionQuery(uint32_t, xenos::xe_gpu_depth_sample_counts*);
  void GuestDraw(uint64_t samples) {
    const uint32_t index = BeginOcclusionQueryDraw();
    deferred_command_list_.Draw(samples); EndOcclusionQueryDraw(index);
  }
  void BeforePacket(uint32_t opcode) {
#include "../src/occlusion_packet_barrier.inc"
  }
};
#include "../src/occlusion_query_draws.inc"

int main() {
  try {
    D3D12CommandProcessor p;
    xenos::xe_gpu_depth_sample_counts result{123};
    // A hidden guest draw surrounded by visible renderer transfer work must
    // remain hidden. The old interval-wide query would incorrectly count 300.
    check(p.BeginGuestOcclusionQuery(64), "begin");
    p.deferred_command_list_.Draw(100);
    p.EndSubmission(false);
    p.GuestDraw(0);
    p.EndSubmission(false);
    p.deferred_command_list_.Draw(200);
    check(p.EndGuestOcclusionQuery(32, &result) && result.samples == 0, "internal draws polluted visibility");
    check(p.awaits == 1, "one logical query must await once");
    // Multiple actual draws across submissions aggregate; heap wrap between
    // completed intervals must not reuse an unresolved index within one.
    for (unsigned cycle = 0; cycle < 5; ++cycle) {
      check(p.BeginGuestOcclusionQuery(64), "repeat begin");
      for (unsigned i = 0; i < 8; ++i) { p.GuestDraw(i); p.EndSubmission(false); }
      check(p.EndGuestOcclusionQuery(32, &result) && result.samples == 28, "draw sum/heap wrap");
    }
    const unsigned before = p.awaits;
    check(p.BeginGuestOcclusionQuery(64), "empty begin");
    check(p.EndGuestOcclusionQuery(32, &result) && result.samples == 0 && p.awaits == before, "empty interval");
    check(p.deferred_command_list_.began == p.deferred_command_list_.ended, "unbalanced host lifecycle");
    check(p.BeginGuestOcclusionQuery(64), "capacity begin");
    for (unsigned i = 0; i < 9; ++i) p.GuestDraw(1);
    check(!p.occlusion_query_resources_available_ && !p.active_occlusion_query_.valid, "unresolved heap overwrite");
    check(p.deferred_command_list_.active == UINT32_MAX, "capacity failure left host query open");
    for (bool submission_failure : {false, true}) {
      D3D12CommandProcessor failure;
      failure.BeginGuestOcclusionQuery(64); failure.GuestDraw(9);
      failure.fail_fence = !submission_failure; failure.fail_submission = submission_failure;
      result.samples = 123;
      check(!failure.EndGuestOcclusionQuery(32, &result) && result.samples == 123, "failed fence published stale samples");
    }
    D3D12CommandProcessor nested;
    check(nested.BeginGuestOcclusionQuery(64) && !nested.BeginGuestOcclusionQuery(128) &&
          !nested.occlusion_query_resources_available_, "nested guest query accepted");
    aot_batch_occlusion_queries = true;
    D3D12CommandProcessor batch;
    std::array<xenos::xe_gpu_depth_sample_counts, 9> results;
    for (auto& r : results) r.samples = 123;
    for (unsigned i = 0; i < 3; ++i) {
      batch.BeginGuestOcclusionQuery(4096 + i * 128 + 32);
      batch.GuestDraw(i * 17);
      check(batch.EndGuestOcclusionQuery(4096 + i * 128, &results[i]), "enqueue");
      check(results[i].samples == 123 && batch.awaits == 0, "premature publication/wait");
    }
    batch.BeforePacket(PM4_DRAW_INDX);
    check(batch.awaits == 0, "query group was not batched");
    // The same barrier precedes both long and short memory-wait handlers.
    batch.BeforePacket(PM4_WAIT_REG_MEM);
    check(batch.awaits == 1 && results[0].samples == 0 && results[1].samples == 17 &&
          results[2].samples == 34, "batched hidden/visible results");
    // Shader loads from unrelated memory must preserve batching, while any
    // overlap with an unfinished snapshot must publish it before consumption.
    for (int offset : {-4, 0, 28}) {
      D3D12CommandProcessor memory;
      result.samples = 123;
      memory.BeginGuestOcclusionQuery(64); memory.GuestDraw(57);
      memory.EndGuestOcclusionQuery(32, &result);
      const uintptr_t snapshot = reinterpret_cast<uintptr_t>(&result);
      memory.BeforePacket(PM4_IM_LOAD);
      memory.FlushOcclusionQueriesForMemory(reinterpret_cast<void*>(snapshot - 4), 4);
      memory.FlushOcclusionQueriesForMemory(reinterpret_cast<void*>(snapshot + sizeof(result)), 128);
      memory.FlushOcclusionQueriesForMemory(&result, 0);
      check(memory.awaits == 0 && result.samples == 123, "unrelated shader read forced a wait");
      memory.FlushOcclusionQueriesForMemory(reinterpret_cast<void*>(snapshot + offset), 8);
      check(memory.awaits == 1 && result.samples == 57, "overlapping shader read saw pending memory");
    }
    for (auto opcode : {PM4_EVENT_WRITE_SHD, PM4_MEM_WRITE, PM4_COND_WRITE, PM4_INTERRUPT,
                        PM4_XE_SWAP, PM4_WAIT_FOR_IDLE, PM4_INDIRECT_BUFFER, PM4_LOAD_ALU_CONSTANT}) {
      batch.BeginGuestOcclusionQuery(4128); batch.GuestDraw(41);
      results[0].samples = 123; batch.EndGuestOcclusionQuery(4096, &results[0]);
      check(results[0].samples == 123, "early signal result");
      batch.BeforePacket(opcode);
      check(results[0].samples == 41, "memory/signal barrier did not publish preceding query");
    }
    D3D12CommandProcessor heap;
    for (unsigned i = 0; i < 9; ++i) {
      heap.BeginGuestOcclusionQuery(4096 + i * 128 + 32); heap.GuestDraw(i + 1);
      heap.EndGuestOcclusionQuery(4096 + i * 128, &results[i]);
    }
    check(heap.awaits == 1 && heap.occlusion_query_resources_available_, "heap pressure did not flush earlier intervals");
    heap.PrepareForWait();
    for (unsigned i = 0; i < 9; ++i) check(results[i].samples == i + 1, "heap reused unresolved index");
    D3D12CommandProcessor reuse;
    reuse.BeginGuestOcclusionQuery(64); reuse.GuestDraw(64); reuse.texture.scale = 2;
    reuse.EndGuestOcclusionQuery(32, &result); reuse.texture.scale = 3;
    reuse.BeginGuestOcclusionQuery(64);
    check(result.samples == 16 && reuse.awaits == 1, "address reuse/scale snapshot");
    D3D12CommandProcessor broken;
    broken.BeginGuestOcclusionQuery(64); broken.GuestDraw(7);
    result.samples = 123; broken.EndGuestOcclusionQuery(32, &result); broken.fail_fence = true;
    check(!broken.FlushOcclusionQueries() && result.samples == 123 &&
          broken.aot_pending_occlusion_draws_ == 1, "failed batch published/reclaimed unfinished query");
    bool aborted = false;
    try { broken.BeforePacket(PM4_EVENT_WRITE_SHD); } catch (const std::runtime_error&) { aborted = true; }
    check(aborted, "failed batch permitted a completion signal");
    std::cout << "Production guest-draw queries exclude transfers, survive submissions, and await valid results.\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
