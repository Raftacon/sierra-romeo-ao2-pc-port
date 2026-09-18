#pragma once
#include <cstdint>

namespace aot {
// Resolve targets have an address-and-length key. Sequence numbers distinguish
// multiple writes within one submission, which fence values alone cannot do.
struct ResolveReadbackState {
  uint64_t sequence = 0, written_sequence[2]{}, written_frame[2]{};
  uint64_t copied_sequence = 0, copied_frame = 0;

  void Written(unsigned slot, uint64_t frame) {
    written_sequence[slot] = ++sequence;
    written_frame[slot] = frame;
  }
  void Copied(unsigned slot, uint64_t frame) {
    copied_sequence = written_sequence[slot];
    copied_frame = frame;
  }
  bool NeedsProgress(uint64_t frame) const {
    if (!copied_sequence) return true;
    // An idle target is not stalled. Only an outstanding newer snapshot can
    // require a progress wait; repeatedly consuming old data would hide stalls.
    for (unsigned i = 0; i < 2; ++i) {
      if (written_sequence[i] > copied_sequence && frame > written_frame[i] &&
          frame - written_frame[i] > 2) return true;
    }
    return false;
  }
};

struct ResolveReadbackSlot {
  uint64_t submission = 0;
  bool readable = false;  // allocated, mapped, and contains the required bytes
};
struct ResolveReadbackPlan {
  int read = -1, write = -1;
};
inline ResolveReadbackPlan PlanResolveReadback(const ResolveReadbackState& state,
    const ResolveReadbackSlot (&slots)[2], uint64_t completed, unsigned preferred) {
  ResolveReadbackPlan plan;
  // UINT64_MAX is D3D12's device-removal sentinel, never valid completion.
  if (completed == UINT64_MAX || preferred > 1) return plan;
  uint64_t newest = state.copied_sequence;
  for (unsigned i = 0; i < 2; ++i) {
    if (slots[i].readable && slots[i].submission && slots[i].submission <= completed &&
        state.written_sequence[i] > newest) {
      newest = state.written_sequence[i];
      plan.read = int(i);
    }
  }
  for (unsigned attempt = 0; attempt < 2; ++attempt) {
    const unsigned i = preferred ^ attempt;
    if (slots[i].submission <= completed) {
      plan.write = int(i);
      break;
    }
  }
  return plan;
}
}  // namespace aot
