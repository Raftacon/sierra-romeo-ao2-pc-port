"""Stage fence-safe, non-repeating resolve reads as an isolated runtime option."""
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
path = output / 'command_processor.cpp'
header_path = output / 'include/rex/graphics/d3d12/command_processor.h'
text, header = path.read_text(), header_path.read_text()


def replace(code, before, after):
    if code.count(before) != 1:
        raise ValueError('Expected one completed-readback anchor: ' + before[:90])
    return code.replace(before, after)


def write(path, code):
    if not path.exists() or path.read_text() != code:
        path.write_text(code)


header = replace(header, '#pragma once', '#pragma once\n#include "src/resolve_readback_schedule.h"')
text = replace(text, '#include <algorithm>', '#include <algorithm>\n#include <chrono>')
header = replace(header, '    uint32_t written_size[2] = {0, 0};',
                 '    uint32_t written_size[2] = {0, 0};\n    aot::ResolveReadbackState resolve_schedule;')
text = replace(text, 'namespace rex::graphics::d3d12 {', '''REXCVAR_DEFINE_BOOL(aot_completed_resolve_readback, false, "GPU/Sierra Romeo",
    "Read completed resolve snapshots without overwriting pending slots");
REXCVAR_DEFINE_BOOL(aot_trace_completed_readback, false, "GPU/Sierra Romeo",
    "Report completed resolve snapshot copies, holds, and waits");

namespace rex::graphics::d3d12 {''')
text = replace(text, '''  uint32_t write_index = rb.current_index;
  uint32_t size = AlignReadbackBufferSize(written_length);''', '''  uint32_t write_index = rb.current_index;
  const bool completed_fast = REXCVAR_GET(aot_completed_resolve_readback) &&
      GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve)) == ReadbackResolveMode::kFast;
  const bool trace_completed = completed_fast && REXCVAR_GET(aot_trace_completed_readback);
  struct CompletedStats {
    uint64_t frame = 0, copies = 0, bytes = 0, held = 0, initial_waits = 0,
             progress_waits = 0, max_age = 0, polls = 0;
    double fence_ms = 0, copy_ms = 0, wait_ms = 0;
  };
  static CompletedStats stats;
  if (trace_completed && frame_current_ >= stats.frame + 300) {
    REXGPU_INFO("Completed resolve readback: frame={}, copies={}, bytes={}, held={}, initial_waits={}, progress_waits={}, max_source_age={}",
        frame_current_, stats.copies, stats.bytes, stats.held, stats.initial_waits,
        stats.progress_waits, stats.max_age);
    REXGPU_INFO("Completed resolve timings: frame={}, fence_polls={}, fence_ms={}, copy_ms={}, wait_ms={}",
        frame_current_, stats.polls, stats.fence_ms, stats.copy_ms, stats.wait_ms);
    stats = {}; stats.frame = frame_current_;
  }
  const auto timed_call = [&](double& total, auto&& call) {
    if (!trace_completed) return call();
    const auto start = std::chrono::steady_clock::now();
    const bool result = call();
    total += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    return result;
  };
  const auto await_readback = [&]() {
    return timed_call(stats.wait_ms, [&]() { return AwaitAllQueueOperationsCompletion(); });
  };
  const auto plan_readback = [&]() {
    aot::ResolveReadbackSlot slots[2];
    for (unsigned i = 0; i < 2; ++i) {
      slots[i].submission = rb.submission_written[i];
      slots[i].readable = rb.buffers[i] && rb.mapped_data[i] &&
          written_length <= rb.sizes[i] && written_length <= rb.written_size[i];
    }
    return aot::PlanResolveReadback(rb.resolve_schedule, slots, submission_completed_, rb.current_index);
  };
  const auto copy_completed = [&](int slot) {
    if (slot < 0) return;
    // The plan is formed only after successful fence validation. No new write
    // to either slot is recorded until after this CPU copy has returned.
    timed_call(stats.copy_ms, [&]() {
      std::memcpy(memory_->TranslatePhysical(written_address), rb.mapped_data[slot], written_length);
      return true;
    });
    stats.max_age = std::max(stats.max_age, frame_current_ - rb.resolve_schedule.written_frame[slot]);
    ++stats.copies; stats.bytes += written_length;
    rb.resolve_schedule.Copied(unsigned(slot), frame_current_);
  };
  if (completed_fast) {
    ++stats.polls;
    if (!timed_call(stats.fence_ms, [&]() { return CheckSubmissionFence(0); })) return false;
    auto plan = plan_readback();
    copy_completed(plan.read);
    if (plan.write < 0) {
      // Preserve pending snapshots so they can complete and be consumed.
      // Re-recording writes into both slots can otherwise starve readback.
      ++stats.held;
      if (rb.resolve_schedule.NeedsProgress(frame_current_)) {
        if (!await_readback()) return false;
        ++stats.progress_waits;
        plan = plan_readback();
        if (plan.read < 0) return false;
        copy_completed(plan.read);
      }
      // Resolve() already updated GPU shared memory for this guest command.
      return true;
    }
    write_index = unsigned(plan.write);
  }
  // Allocate AFTER choosing the write slot: switching to an unused slot may
  // require a different resource, even when the preferred slot was allocated.
  uint32_t size = AlignReadbackBufferSize(written_length);''')
text = replace(text, '''  rb.submission_written[write_index] = submission_current_;
  ReadbackResolveMode readback_mode =''', '''  rb.submission_written[write_index] = submission_current_;
  rb.written_size[write_index] = written_length;
  rb.resolve_schedule.Written(write_index, frame_current_);
  if (completed_fast) {
    if (rb.resolve_schedule.NeedsProgress(frame_current_)) {
      const bool initial = rb.resolve_schedule.copied_sequence == 0;
      if (!await_readback()) return false;
      if (initial) ++stats.initial_waits; else ++stats.progress_waits;
      const auto plan = plan_readback();
      if (plan.read < 0) return false;
      copy_completed(plan.read);
    }
    rb.current_index = 1 - write_index;
    return true;
  }
  ReadbackResolveMode readback_mode =''')
write(path, text)
write(header_path, header)
