"""Apply opt-in rendering corrections to an exact SDK source file."""
import hashlib
from pathlib import Path
import sys

source = Path(sys.argv[1]) / 'src/graphics/d3d12/command_processor.cpp'
output = Path(sys.argv[2])
data = source.read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(data).hexdigest() != 'aeb26127de7b9dd0b797d6430187540e9b7ef970f2171e80a1819165318bdc58':
    raise ValueError('Unexpected SDK command processor revision')
text = data.decode()

def replace(before, after):
    global text
    if text.count(before) != 1:
        raise ValueError('Patch anchor must be unique')
    text = text.replace(before, after)

replace('#include <algorithm>', '#include "src/gpu_copy_diagnostics.h"\n#include "src/gpu_draw_diagnostics.h"\n#include <algorithm>')
replace('''  if (memexport_used) {
    // Make sure this memexporting draw is ordered with other work using shared
''', '''  static thread_local aot::GpuDrawDiagnostics draw_trace;
  draw_trace.Draw(frame_current_, regs, viewport_info, scissor, vertex_shader, pixel_shader,
                  draw_resolution_scale_x, draw_resolution_scale_y, normalized_color_mask,
                  used_texture_mask, memory_->physical_membase());

  if (memexport_used) {
    // Make sure this memexporting draw is ordered with other work using shared
''')
replace('''      std::memcpy(destination, static_cast<uint8_t*>(rb.mapped_data[read_index]), written_length);''', '''      aot::GpuCopyDiagnostics copy_trace(frame_current_, written_address, written_length);
      std::memcpy(destination, static_cast<uint8_t*>(rb.mapped_data[read_index]), written_length);''')
replace('''  // Write the constant buffer data.
''', '''  // The diagnostic override is applied only to this shader's uploaded
  // constants. Refresh on entry and exit so a matching register layout cannot
  // accidentally reuse the modified constant buffer in another shader.
  static thread_local bool previous_dof_override = false;
  const bool dof_override = REXCVAR_GET(aot_dof_coverage_floor) > 0 && pixel_shader &&
      pixel_shader->ucode_data_hash() == 0x17401B22D4626678ull;
  if (dof_override || previous_dof_override) cbuffer_binding_float_pixel_.up_to_date = false;
  previous_dof_override = dof_override;

  // Write the constant buffer data.
''')
replace('''              &regs[XE_GPU_REG_SHADER_CONSTANT_256_X + (i << 8) + (float_constant_index << 2)],
              4 * sizeof(float));
          float_constants += 4 * sizeof(float);
''', '''              &regs[XE_GPU_REG_SHADER_CONSTANT_256_X + (i << 8) + (float_constant_index << 2)],
              4 * sizeof(float));
          if (dof_override && i == 0 && float_constant_index == 4) {
            float far_blur;
            std::memcpy(&far_blur, float_constants + sizeof(float), sizeof(float));
            const float cap = 1.0f - float(REXCVAR_GET(aot_dof_coverage_floor));
            if (far_blur > cap && far_blur <= 1.0f) {
              std::memcpy(float_constants + sizeof(float), &cap, sizeof(float));
              static thread_local bool reported_dof_override = false;
              if (!reported_dof_override) {
                REXLOG_INFO("AOT DOF coverage diagnostic: uploaded PS c4.y {} -> {}", far_blur, cap);
                reported_dof_override = true;
              }
            }
          }
          float_constants += 4 * sizeof(float);
''')
replace('REXCVAR_DEFINE_BOOL(d3d12_bindless,', '''REXCVAR_DEFINE_DOUBLE(aot_dof_coverage_floor, 0.0, "GPU/Sierra Romeo",
                    "Diagnostic minimum sharp weight in the observed final DOF blend")
    .range(0.0, 0.1);
REXCVAR_DEFINE_BOOL(aot_resolve_readback_sync, true, "GPU/Sierra Romeo",
                    "Use completed resolve readbacks, bounding consecutive unavailable frames")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);
REXCVAR_DEFINE_BOOL(aot_trace_readback, false, "GPU/Sierra Romeo",
                    "Report completed resolve readback selection and fallback counts")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);

REXCVAR_DEFINE_BOOL(aot_viewport_depth_key, true, "GPU/Sierra Romeo",
                    "Include render-target depth format in the viewport cache key");
REXCVAR_DEFINE_BOOL(aot_trace_viewport, false, "GPU/Sierra Romeo",
                    "Compare legacy viewport cache hits with fresh viewport calculations");

REXCVAR_DEFINE_BOOL(d3d12_bindless,''')
replace('namespace rex::graphics::d3d12 {', '''namespace rex::graphics::d3d12 {
extern thread_local uint64_t aot_shared_upload_pages;
REXCVAR_DEFINE_BOOL(aot_vertex_residency_validation, true, "GPU/Sierra Romeo",
                    "Check shared-memory validity even when vertex descriptors are unchanged");
REXCVAR_DEFINE_BOOL(aot_trace_vertex_uploads, false, "GPU/Sierra Romeo",
                    "Report vertex cache hits that require dirty-page uploads");''')
replace('''  // Ensure vertex buffers are resident.
  const Shader::ConstantRegisterMap&''', '''  // Descriptor identity does not imply that its memory is still valid.
  // CPU invalidation and the frame-end page refresh can both require uploads
  // while the vertex fetch address and size remain unchanged.
  const bool validate_vertices = REXCVAR_GET(aot_vertex_residency_validation);
  struct VertexStats { uint64_t frame = 0, skipped = 0, checked = 0, dirty = 0, pages = 0; };
  static thread_local VertexStats vertex_stats;
  // Ensure vertex buffers are resident.
  const Shader::ConstantRegisterMap&''')
replace('''      if (vertex_buffers_in_sync_[vfetch_index >> 6] & vfetch_bit) {
        continue;
      }''', '''      const bool fast_vertex_hit = vertex_buffers_in_sync_[vfetch_index >> 6] & vfetch_bit;
      if (fast_vertex_hit && !validate_vertices) {
        ++vertex_stats.skipped;
        continue;
      }''')
replace('''      if (state.address == vfetch_constant.address && state.size == vfetch_constant.size) {
        vertex_buffers_in_sync_[vfetch_index >> 6] |= vfetch_bit;
        continue;
      }
      if (!shared_memory_->RequestRange''', '''      const bool descriptor_hit = state.address == vfetch_constant.address && state.size == vfetch_constant.size;
      if (descriptor_hit && !validate_vertices) {
        ++vertex_stats.skipped;
        vertex_buffers_in_sync_[vfetch_index >> 6] |= vfetch_bit;
        continue;
      }
      const auto pages_before = aot_shared_upload_pages;
      if (!shared_memory_->RequestRange''')
replace('''      state.address = vfetch_constant.address;
      state.size = vfetch_constant.size;''', '''      if (fast_vertex_hit || descriptor_hit) {
        ++vertex_stats.checked;
        const auto uploaded = aot_shared_upload_pages - pages_before;
        vertex_stats.dirty += uploaded != 0;
        vertex_stats.pages += uploaded;
      }
      state.address = vfetch_constant.address;
      state.size = vfetch_constant.size;''')
replace('''  // Gather memexport ranges and ensure the heaps for them are resident,''', '''  if (REXCVAR_GET(aot_trace_vertex_uploads) && frame_current_ >= vertex_stats.frame + 300) {
    REXLOG_INFO("AOT vertex validity: frame={}, validation={}, skipped={}, checked_cache_hits={}, dirty_cache_hits={}, uploaded_pages={}",
        frame_current_, validate_vertices, vertex_stats.skipped, vertex_stats.checked,
        vertex_stats.dirty, vertex_stats.pages);
    vertex_stats = {}; vertex_stats.frame = frame_current_;
  }

  // Gather memexport ranges and ensure the heaps for them are resident,''')
replace('''  draw_util::ViewportInfo viewport_info;
  if (viewport_cache_valid_''', '''  // GetHostViewportInfo reads RB_DEPTH_INFO to distinguish D24S8 from
  // D24FS8 even when normalized_depth_control and all viewport registers
  // are identical. A format change must invalidate the cached depth range.
  const uint32_t depth_format = uint32_t(regs.Get<reg::RB_DEPTH_INFO>().depth_format);
  if (REXCVAR_GET(aot_viewport_depth_key)) viewport_key.flags |= depth_format << 3;
  if (REXCVAR_GET(aot_trace_viewport)) {
    static uint64_t checked = 0, stale = 0, stale_used = 0, last_frame = 0, detail_count = 0;
    auto legacy_key = viewport_key;
    auto legacy_previous = previous_viewport_key_;
    legacy_key.flags &= 7;
    legacy_previous.flags &= 7;
    if (viewport_cache_valid_ && legacy_key == legacy_previous) {
      draw_util::ViewportInfo fresh;
      draw_util::GetHostViewportInfo(regs, draw_resolution_scale_x, draw_resolution_scale_y, true,
                                     D3D12_VIEWPORT_BOUNDS_MAX, D3D12_VIEWPORT_BOUNDS_MAX, false,
                                     normalized_depth_control, convert_z_to_float24,
                                     host_render_targets_used, ps_writes_depth, fresh);
      ++checked;
      if (fresh.z_min != previous_viewport_info_.z_min || fresh.z_max != previous_viewport_info_.z_max) {
        ++stale;
        stale_used += viewport_key == previous_viewport_key_;
        if (detail_count++ < 24) {
          REXLOG_INFO("AOT stale viewport depth: frame={}, format={}, old_range={}/{}, fresh_range={}/{}, corrected={}",
              frame_current_, depth_format, previous_viewport_info_.z_min, previous_viewport_info_.z_max,
              fresh.z_min, fresh.z_max, REXCVAR_GET(aot_viewport_depth_key));
        }
      }
    }
    if (frame_current_ >= last_frame + 300) {
      REXLOG_INFO("AOT viewport comparison: frame={}, legacy_hits={}, stale_legacy_depth={}, stale_actual_depth={}", frame_current_, checked, stale, stale_used);
      checked = stale = stale_used = 0;
      last_frame = frame_current_;
    }
  }
  draw_util::ViewportInfo viewport_info;
  if (viewport_cache_valid_''')
replace('''  if (is_scaled) {
    if (!resolve_downscale_pipeline_''', '''  const bool completed_fast = REXCVAR_GET(aot_resolve_readback_sync) &&
      GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve)) == ReadbackResolveMode::kFast;
  const bool initialized = rb.written_size[0] || rb.written_size[1];
  struct ReadbackStats { uint64_t copied = 0, held = 0, busy = 0, fallback = 0, max_age = 0, frame = 0; };
  static ReadbackStats stats;
  if (completed_fast) {
    // Read before recording a new write to either slot. Never dereference a
    // mapped readback allocation until the submission that wrote it completed.
    CheckSubmissionFence(0);
    auto ready = [&](uint32_t slot) {
      return rb.buffers[slot] && rb.mapped_data[slot] &&
          written_length <= rb.written_size[slot] && rb.submission_written[slot] &&
          rb.submission_written[slot] <= submission_completed_;
    };
    uint32_t completed = 1 - write_index;
    if (!ready(completed) && ready(write_index)) completed = write_index;
    if (ready(completed)) {
      std::memcpy(memory_->TranslatePhysical(written_address), rb.mapped_data[completed], written_length);
      rb.last_cpu_copy_frame = frame_current_;
      ++stats.copied;
      stats.max_age = std::max(stats.max_age, frame_current_ - rb.frame_written[completed]);
    } else if (initialized) {
      // Guest memory retains the last completed result while the GPU owns
      // both slots. Fall back after two frames without any completed copy.
      // A rarely used target can have older completed data; this is expected
      // for delayed readback and must not force a wait on each reuse.
      ++stats.held;
    }
    auto pending = [&](uint32_t slot) {
      return rb.submission_written[slot] > submission_completed_;
    };
    if (pending(write_index)) {
      if (!pending(1 - write_index)) {
        write_index = 1 - write_index;
      } else {
        // Keep both pending snapshots intact. Rewriting either slot would
        // continually replace its fence and starve CPU consumption.
        ++stats.busy;
        if (frame_current_ <= rb.last_cpu_copy_frame + 2) return true;
        if (!AwaitAllQueueOperationsCompletion()) return true;
        const uint32_t latest = rb.submission_written[0] > rb.submission_written[1] ? 0 : 1;
        if (rb.mapped_data[latest] && written_length <= rb.written_size[latest]) {
          std::memcpy(memory_->TranslatePhysical(written_address), rb.mapped_data[latest], written_length);
          rb.last_cpu_copy_frame = frame_current_;
          ++stats.fallback;
        }
      }
    }
  }

  if (is_scaled) {
    if (!resolve_downscale_pipeline_''')
replace('''  ReadbackResolveMode readback_mode = GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve));
  bool use_delayed_sync =''', '''  rb.submission_written[write_index] = submission_current_;
  rb.written_size[write_index] = written_length;
  rb.frame_written[write_index] = frame_current_;
  if (completed_fast) {
    if (!initialized || frame_current_ > rb.last_cpu_copy_frame + 2) {
      static uint32_t trace_count = 0;
      if (REXCVAR_GET(aot_trace_readback) && initialized && frame_current_ > 2400 && trace_count++ < 24) {
        REXLOG_INFO("AOT readback fallback: key={:016X}, frame={}, initialized={}, cpu={}, slots={}/{}, submissions={}/{}, completed={}",
            resolve_key, frame_current_, initialized, rb.last_cpu_copy_frame,
            rb.frame_written[0], rb.frame_written[1], rb.submission_written[0], rb.submission_written[1], submission_completed_);
      }
      if (!AwaitAllQueueOperationsCompletion()) return true;
      if (rb.mapped_data[write_index]) {
        std::memcpy(memory_->TranslatePhysical(written_address), rb.mapped_data[write_index], written_length);
        rb.last_cpu_copy_frame = frame_current_;
        ++stats.fallback;
      }
    }
    if (REXCVAR_GET(aot_trace_readback) && frame_current_ >= stats.frame + 300) {
      REXLOG_INFO("AOT completed readback: copied={}, held={}, busy={}, full_fallback={}, max_source_age={}",
          stats.copied, stats.held, stats.busy, stats.fallback, stats.max_age);
      stats = {}; stats.frame = frame_current_;
    }
    rb.current_index = 1 - write_index;
    return true;
  }
  ReadbackResolveMode readback_mode = GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve));
  bool use_delayed_sync =''')
replace('''        readback.buffers[i]->Release();
      }
      readback.buffers[i] = nullptr;''', '''        resources_for_deletion_.emplace_back(GetCurrentSubmission(), readback.buffers[i]);
      }
      readback.buffers[i] = nullptr;''')
output.parent.mkdir(parents=True, exist_ok=True)
if not output.exists() or output.read_text() != text:
    output.write_text(text)

shared = Path(sys.argv[1]) / 'src/graphics/d3d12/shared_memory.cpp'
data = shared.read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(data).hexdigest() != 'b120d07c6709d0d2a7836b532ed06b2032a418cbd4a71808cd318c7e6eb6cfbc':
    raise ValueError('Unexpected SDK D3D12 shared memory revision')
text = data.decode()
replace('namespace rex::graphics::d3d12 {', '''namespace rex::graphics::d3d12 {
// Command-thread diagnostic: RequestRange returning successfully after this
// counter advances establishes that previously invalid pages needed uploads.
thread_local uint64_t aot_shared_upload_pages = 0;''')
replace('''  if (upload_page_ranges.empty()) {
    return true;
  }
  CommitUAVWritesAndTransitionBuffer''', '''  if (upload_page_ranges.empty()) {
    return true;
  }
  for (const auto& range : upload_page_ranges) aot_shared_upload_pages += range.second;
  CommitUAVWritesAndTransitionBuffer''')
output = Path(sys.argv[2]).parent / 'shared_memory.cpp'
if not output.exists() or output.read_text() != text:
    output.write_text(text)
header = Path(sys.argv[1]) / 'include/rex/graphics/d3d12/command_processor.h'
data = header.read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(data).hexdigest() != '646460362f1b15e3a1309b669d799b582fe7bf57e050fabc2c5be65ec516bcb2':
    raise ValueError('Unexpected SDK command processor header')
text = data.decode()
replace('''    uint32_t written_size[2] = {0, 0};''', '''    uint32_t written_size[2] = {0, 0};
    uint64_t frame_written[2] = {0, 0};
    uint64_t last_cpu_copy_frame = 0;''')
output = output.parent / 'include/rex/graphics/d3d12/command_processor.h'
output.parent.mkdir(parents=True, exist_ok=True)
if not output.exists() or output.read_text() != text:
    output.write_text(text)
