#include "src/resolve_readback_schedule.h"
#include <iostream>
#include <stdexcept>

void require(bool value, const char* why) {
  if (!value) throw std::runtime_error(why);
}
int main() {
  try {
    aot::ResolveReadbackState state;
    aot::ResolveReadbackSlot slots[2]{};
    auto plan = aot::PlanResolveReadback(state, slots, 0, 0);
    require(plan.read == -1 && plan.write == 0 && state.NeedsProgress(0), "first use");
    // Both snapshots written in the same submission, with the newest in slot 0.
    state.Written(1, 10); state.Written(0, 10);
    slots[0] = {7, true}; slots[1] = {7, true};
    plan = aot::PlanResolveReadback(state, slots, 6, 1);
    require(plan.read == -1 && plan.write == -1, "pending GPU writes must be preserved");
    plan = aot::PlanResolveReadback(state, slots, 7, 1);
    require(plan.read == 0 && plan.write == 1, "same-submission newest snapshot");
    state.Copied(0, 11);
    plan = aot::PlanResolveReadback(state, slots, 7, 1);
    require(plan.read == -1, "must not replay older or already consumed data");
    require(!state.NeedsProgress(100), "idle target must not trigger fallback");
    state.Written(1, 12); slots[1].submission = 8;
    require(!state.NeedsProgress(14) && state.NeedsProgress(15), "bounded pending-copy fallback");
    plan = aot::PlanResolveReadback(state, slots, 7, 1);
    require(plan.read == -1 && plan.write == 0, "switch away from pending preferred slot");
    slots[0].readable = false;
    plan = aot::PlanResolveReadback(state, slots, UINT64_MAX, 0);
    require(plan.read == -1 && plan.write == -1, "device removal is not completion");
    // Exhaustively check the selection invariants across two-slot submission
    // orderings, mapping availability, previous consumption and preferred slot.
    unsigned checked = 0;
    for (unsigned a = 0; a < 5; ++a) for (unsigned b = 0; b < 5; ++b)
    for (unsigned done = 0; done < 5; ++done) for (unsigned mask = 0; mask < 4; ++mask)
    for (unsigned consumed = 0; consumed < 3; ++consumed) for (unsigned preferred = 0; preferred < 2; ++preferred) {
      aot::ResolveReadbackState sample;
      sample.Written(0, 2); sample.Written(1, 2); sample.copied_sequence = consumed;
      aot::ResolveReadbackSlot data[2]{{a, bool(mask & 1)}, {b, bool(mask & 2)}};
      auto choice = aot::PlanResolveReadback(sample, data, done, preferred);
      uint64_t chosen_sequence = consumed;
      if (choice.read >= 0) {
        const auto& slot = data[choice.read];
        require(slot.readable && slot.submission && slot.submission <= done, "read safety");
        chosen_sequence = sample.written_sequence[choice.read];
        require(chosen_sequence > consumed, "duplicate or stale copy");
      }
      for (unsigned i = 0; i < 2; ++i) {
        if (data[i].readable && data[i].submission && data[i].submission <= done)
          require(chosen_sequence >= sample.written_sequence[i], "newer completed result was skipped");
      }
      if (choice.write >= 0) require(data[choice.write].submission <= done, "overwriting pending snapshot");
      else require(a > done && b > done, "free write slot ignored");
      ++checked;
    }
    std::cout << checked << " readback schedules satisfy fence, freshness and reuse invariants.\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
