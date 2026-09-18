// Compile the complete production eviction method and deletion-queue drain.
// A separate previous-method binary proves the original pending-release case.
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <unordered_map>
#include <utility>

void require(bool value, const char* why) {
  if (!value) throw std::runtime_error(why);
}
struct ID3D12Resource {
  unsigned releases = 0, unmaps = 0;
  void Release() { ++releases; }
  void Unmap(unsigned, const void*) { ++unmaps; }
};
struct Fence { uint64_t GetCompletedValue() { return 8; } };
#define REXGPU_INFO(...) ((void)0)
struct D3D12CommandProcessor {
#include "readback_retirement_members.inc"
  uint64_t frame_current_ = 100, submission_current_ = 10, submission_completed_ = 8;
  Fence fence;
  Fence* submission_fence_ = &fence;
  std::deque<std::pair<uint64_t, ID3D12Resource*>> resources_for_deletion_;
  uint64_t GetCurrentSubmission() { return submission_current_; }
  void Drain() {
#include "readback_retirement_drain.inc"
  }
};
#ifdef AOT_READBACK_PREVIOUS
#include "readback_retirement_previous.inc"
#else
#include "readback_retirement_fixed.inc"
#endif

int main() {
  try {
    using Processor = D3D12CommandProcessor;
    using Buffer = Processor::ReadbackBuffer;
    Processor p;
    ID3D12Resource first, second;
    Buffer slot;
    slot.last_used_frame = 100;
    slot.buffers[0] = &first; slot.buffers[1] = &second;
    slot.mapped_data[0] = &first;
    slot.submission_written[0] = slot.submission_written[1] = 9;
    // Every map element has a sole, distinct resource reference in production.
    // Only the first iterator is populated here; capacity evicts exactly it.
    std::unordered_map<uint64_t, Buffer> map;
    for (unsigned i = 0; i <= p.kMaxReadbackBuffers; ++i) {
      map[i].last_used_frame = p.frame_current_;
    }
    map.begin()->second = slot;
    p.EvictOldReadbackBuffers(map);
    require(map.size() == p.kMaxReadbackBuffers, "capacity not enforced");
    require(first.unmaps == 1 && second.unmaps == 0, "mapped-slot cleanup mismatch");
#ifdef AOT_READBACK_PREVIOUS
    require(first.releases == 1 && second.releases == 1 && p.resources_for_deletion_.empty(),
            "previous premature release was not reproduced");
    require(p.submission_completed_ < slot.submission_written[0], "write was not pending");
    std::cout << "Original capacity eviction released both resources before writer completion.\n";
#else
    require(!first.releases && !second.releases && p.resources_for_deletion_.size() == 2,
            "eviction released a pending resource");
    p.Drain();
    require(!first.releases && !second.releases, "drain released before completion");
    p.submission_completed_ = 9; p.Drain();
    require(!first.releases && !second.releases, "conservative retirement bound lost");
    p.submission_completed_ = 10; p.Drain(); p.Drain();
    require(first.releases == 1 && second.releases == 1 && p.resources_for_deletion_.empty(),
            "completed references not released exactly once");
    // Age boundary, no-eviction, empty cache, null and mapped slots.
    map.clear(); p.EvictOldReadbackBuffers(map);
    Buffer old;
    ID3D12Resource aged;
    old.buffers[1] = &aged; old.mapped_data[1] = &aged;
    old.last_used_frame = 40; map[1] = old;
    p.EvictOldReadbackBuffers(map);
    require(map.size() == 1 && !aged.unmaps && !aged.releases, "age boundary evicted early");
    p.frame_current_ = 101; p.EvictOldReadbackBuffers(map);
    require(map.empty() && aged.unmaps == 1 && !aged.releases, "old slot was not deferred");
    p.Drain();
    require(aged.releases == 1, "old slot was not reclaimed");
    p.frame_current_ = 1; map[2].last_used_frame = 0;
    p.EvictOldReadbackBuffers(map);
    require(map.size() == 1, "early frame age underflow");
    std::cout << "Capacity/age eviction preserves pending references and reclaims each exactly once.\n";
#endif
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n'; return 1;
  }
}
