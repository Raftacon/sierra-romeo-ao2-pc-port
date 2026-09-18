"""Build spatial output, draw diagnostics and the verified queue-wait correction."""
import hashlib
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != data:
        path.write_text(data)

def read(path, digest=None):
    data = (source / path).read_bytes().replace(b'\r\n', b'\n')
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Unexpected pinned SDK source: ' + path)
    return data.decode()

text = read('src/graphics/d3d12/command_processor.cpp', 'aeb26127de7b9dd0b797d6430187540e9b7ef970f2171e80a1819165318bdc58')
original = text

def replace(before, after):
    global text
    if text.count(before) != 1:
        raise ValueError('Expected exactly one patch anchor: ' + before[:90])
    text = text.replace(before, after)


# Test HRESULTs separately. S_OK is zero: Boolean short-circuiting it would
# skip event registration, while SUCCEEDED(bool) would always permit the wait.
replace('''      if (SUCCEEDED(direct_queue->Signal(queue_operations_since_submission_fence_, fence_value) &&
                    SUCCEEDED(queue_operations_since_submission_fence_->SetEventOnCompletion(
                        fence_value, fence_completion_event_)))) {''', '''      if (SUCCEEDED(direct_queue->Signal(queue_operations_since_submission_fence_, fence_value)) &&
          SUCCEEDED(queue_operations_since_submission_fence_->SetEventOnCompletion(
              fence_value, fence_completion_event_))) {''')


def queue_wait_block(code):
    begin = '    if (queue_operations_done_since_submission_signal_) {'
    end = '    // A submission won\'t be ended'
    if code.count(begin) != 1 or code.count(end) != 1:
        raise ValueError('Unexpected queue-wait block layout')
    start = code.index(begin)
    return code[start:code.index(end, start)]


# Compile the production block with controlled queue/event doubles. Preserve
# the unmodified SDK block as a negative control that must reproduce the bug.
write(output / 'queue_wait_fixed.inc', queue_wait_block(text))
write(output / 'queue_wait_original.inc', queue_wait_block(original))

replace('#include <algorithm>', '#include "src/spatial_upscaler.h"\n#include "src/swap_timing.h"\n#include "src/occlusion_trace.h"\n#include "src/gpu_draw_diagnostics.h"\n#include "src/msaa_alignment.h"\n#include <algorithm>')
# Read-only visibility-query lifecycle attribution. Preserve the SDK behavior.
replace('''  if (!REXCVAR_GET(occlusion_query_enable) || !occlusion_query_resources_available_) {
    return CommandProcessor::ExecutePacketType3_EVENT_WRITE_ZPD(reader, packet, count);''', '''  aot::TraceOcclusion("packet", frame_current_, register_file_->values[XE_GPU_REG_RB_SAMPLE_COUNT_ADDR],
      active_occlusion_query_.valid, occlusion_query_resources_available_, active_occlusion_query_.host_index,
      REXCVAR_GET(occlusion_query_enable));
  if (!REXCVAR_GET(occlusion_query_enable) || !occlusion_query_resources_available_) {
    return CommandProcessor::ExecutePacketType3_EVENT_WRITE_ZPD(reader, packet, count);''')

replace('''  bool is_end = is_end_via_z_pass || is_end_via_z_fail;
''', '''  bool is_end = is_end_via_z_pass || is_end_via_z_fail;
  aot::TraceOcclusion(is_end ? "guest_end" : "guest_begin", frame_current_, sample_count_addr,
      active_occlusion_query_.valid, occlusion_query_resources_available_, active_occlusion_query_.host_index,
      (uint64_t(sample_counts->ZPass_A) << 32) | sample_counts->ZPass_B,
      (uint64_t(sample_counts->ZFail_A) << 32) | sample_counts->ZFail_B);
''')

replace('''void D3D12CommandProcessor::DisableHostOcclusionQueries() {
''', '''void D3D12CommandProcessor::DisableHostOcclusionQueries() {
  aot::TraceOcclusion("disable", frame_current_, active_occlusion_query_.sample_count_address,
      active_occlusion_query_.valid, occlusion_query_resources_available_, active_occlusion_query_.host_index);
''')

replace('''    if (active_occlusion_query_.valid && occlusion_query_heap_) {
      deferred_command_list_.D3DEndQuery''', '''    if (active_occlusion_query_.valid && occlusion_query_heap_) {
      aot::TraceOcclusion("submission_closes_query", frame_current_, active_occlusion_query_.sample_count_address,
          true, occlusion_query_resources_available_, active_occlusion_query_.host_index);
      deferred_command_list_.D3DEndQuery''')

replace('''  uint64_t samples = occlusion_query_readback_mapping_[host_index];
  samples = NormalizeOcclusionSamples(samples);''', '''  uint64_t samples = occlusion_query_readback_mapping_[host_index];
  aot::TraceOcclusion("result", frame_current_, sample_count_address, false,
      occlusion_query_resources_available_, host_index, samples);
  samples = NormalizeOcclusionSamples(samples);''')

# Retail D3D writes a start snapshot at query+32 and an end snapshot at
# query+0 (82A5E690). The host query spans that pair; the guest subtracts start
# from end, so the start snapshot must be zero when using an isolated query.
replace('''    if (!BeginGuestOcclusionQuery(sample_count_addr)) {
      return write_fallback_result();
    }
    return true;''', '''    if (!BeginGuestOcclusionQuery(sample_count_addr)) {
      return write_fallback_result();
    }
    std::memset(sample_counts, 0, sizeof(xenos::xe_gpu_depth_sample_counts));
    return true;''')
replace('''  if (!active_occlusion_query_.valid ||
      active_occlusion_query_.sample_count_address != sample_count_addr) {''', '''  if (!active_occlusion_query_.valid ||
      (active_occlusion_query_.sample_count_address != sample_count_addr &&
       uint64_t(active_occlusion_query_.sample_count_address) !=
           uint64_t(sample_count_addr) + sizeof(xenos::xe_gpu_depth_sample_counts))) {''')


def query_packet_function(code, method):
    start = code.index('bool D3D12CommandProcessor::ExecutePacketType3_EVENT_WRITE_ZPD(')
    end = code.index('bool D3D12CommandProcessor::PushTransitionBarrier(', start)
    return code[start:end].replace('D3D12CommandProcessor::ExecutePacketType3_EVENT_WRITE_ZPD',
                                   'D3D12CommandProcessor::' + method)


write(output / 'occlusion_packet_original.inc', query_packet_function(original, 'ExecuteOriginal'))
write(output / 'occlusion_packet_fixed.inc', query_packet_function(text, 'ExecuteFixed'))

replace('''void D3D12CommandProcessor::IssueSwap(uint32_t frontbuffer_ptr, uint32_t frontbuffer_width,
                                      uint32_t frontbuffer_height) {
''', '''void D3D12CommandProcessor::IssueSwap(uint32_t frontbuffer_ptr, uint32_t frontbuffer_width,
                                      uint32_t frontbuffer_height) {
  aot::SwapTiming aot_swap_timing(frame_current_);
''')
replace('''  draw_util::Scissor scissor;
  draw_util::GetScissor(regs, scissor);''', '''  // Apply after the cache copy, so repeated draws never accumulate the shift.
  // Keep depth/color correspondence across shader families and tiled viewports.
  const float aot_alignment_delta = aot::MsaaAlignmentDelta(
      REXCVAR_GET(aot_msaa_alignment) && draw_resolution_scale_x == 1 && !convert_z_to_float24,
      host_render_targets_used,
      render_target_cache_->msaa_2x_supported(),
      uint32_t(regs.Get<reg::RB_SURFACE_INFO>().msaa_samples),
      regs[XE_GPU_REG_PA_CL_CLIP_CNTL], regs[XE_GPU_REG_PA_CL_VTE_CNTL],
      regs[XE_GPU_REG_PA_SU_VTX_CNTL], regs.Get<float>(XE_GPU_REG_PA_CL_VPORT_XOFFSET),
      regs.Get<float>(XE_GPU_REG_PA_CL_VPORT_YOFFSET), draw_resolution_scale_y,
      viewport_info.xy_extent[1]);
  if (aot_alignment_delta != 0.0f) viewport_info.ndc_offset[1] += aot_alignment_delta;

  draw_util::Scissor scissor;
  draw_util::GetScissor(regs, scissor);''')
# Capture observes the same draw state without enabling experimental fixes.
replace('''  if (memexport_used) {
    // Make sure this memexporting draw is ordered with other work using shared
''', '''  auto& draw_trace = aot::GetGpuDrawDiagnostics();
  draw_trace.Draw(frame_current_, regs, viewport_info, scissor, vertex_shader, pixel_shader,
                  draw_resolution_scale_x, draw_resolution_scale_y, normalized_color_mask,
                  used_texture_mask, memory_->physical_membase());

  if (memexport_used) {
    // Make sure this memexporting draw is ordered with other work using shared
''')
replace('namespace rex::graphics::d3d12 {', '''namespace aot {
void ObserveGpuCopyPacket(uint32_t packet, uint64_t bin_select,
                          uint64_t bin_mask, uint32_t mode,
                          uint32_t destination, uint32_t viz_query) {
  GetGpuDrawDiagnostics().CopyPacket(packet, bin_select, bin_mask, mode, destination, viz_query);
}
}
REXCVAR_DEFINE_BOOL(aot_spatial_upscale, true, "GPU/Sierra Romeo", "Spatial FSR output upscaling");
REXCVAR_DEFINE_STRING(aot_upscale_output_size, "1920x1080", "GPU/Sierra Romeo", "Actual PC output dimensions");
REXCVAR_DEFINE_BOOL(aot_msaa_alignment, true, "GPU/Sierra Romeo", "Align quarter-jittered 2x MSAA scene passes at 1x rendering resolution");

namespace rex::graphics::d3d12 {''')
replace('''bool D3D12CommandProcessor::IssueCopy() {
''', '''bool D3D12CommandProcessor::IssueCopy() {
  aot::GetGpuDrawDiagnostics().Resolve(frame_current_, *register_file_, memory_->physical_membase());
''')
replace('  ShutdownOcclusionQueryResources();', '  ShutdownOcclusionQueryResources();\n  spatial_upscaler_.reset();')
replace('''  presenter->RefreshGuestOutput(
      guest_output_width, guest_output_height, display_width, display_height,''', '''  uint32_t output_width = guest_output_width, output_height = guest_output_height;
  bool spatial = false;
  if (REXCVAR_GET(aot_spatial_upscale)) {
    unsigned target_width = 0, target_height = 0;
    const auto target = REXCVAR_GET(aot_upscale_output_size);
    if (std::sscanf(target.c_str(), "%ux%u", &target_width, &target_height) == 2 &&
        target_width <= 8192 && target_height <= 8192) {
      // Preserve the source aspect ratio. One EASU pass is limited to 2x per
      // dimension; larger windows receive a final presenter resample.
      const double ratio = std::min({2.0, double(target_width) / guest_output_width,
                                    double(target_height) / guest_output_height});
      if (ratio > 1.0) {
        output_width = uint32_t(std::lround(guest_output_width * ratio));
        output_height = uint32_t(std::lround(guest_output_height * ratio));
        if (!spatial_upscaler_) spatial_upscaler_ = std::make_unique<aot::SpatialUpscaler>(GetD3D12Provider());
        spatial = spatial_upscaler_->Prepare(guest_output_width, guest_output_height, output_width, output_height);
      }
    }
  }
  if (!spatial) { output_width = guest_output_width; output_height = guest_output_height; }
  aot_swap_timing.Mark(aot::SwapTiming::kPrepared);
  const bool aot_presented = presenter->RefreshGuestOutput(
      output_width, output_height, display_width, display_height,''')
replace('''       guest_output_height](ui::Presenter::GuestOutputRefreshContext& context) -> bool {''', '''       guest_output_height, spatial, &aot_swap_timing](ui::Presenter::GuestOutputRefreshContext& context) -> bool {
        aot_swap_timing.Mark(aot::SwapTiming::kCallback);''')
replace('''        ID3D12Resource* guest_output_resource =
            static_cast<ui::d3d12::D3D12Presenter::D3D12GuestOutputRefreshContext&>(context)
                .resource_uav_capable();''', '''        ID3D12Resource* presented_resource =
            static_cast<ui::d3d12::D3D12Presenter::D3D12GuestOutputRefreshContext&>(context)
                .resource_uav_capable();
        ID3D12Resource* guest_output_resource = spatial ? spatial_upscaler_->source() : presented_resource;''')
replace('''        EndSubmission(true);
        return true;
      });''', '''        aot_swap_timing.Mark(aot::SwapTiming::kCommands);
        EndSubmission(true);
        aot_swap_timing.Mark(aot::SwapTiming::kSubmitted);
        const bool aot_refreshed = !spatial || spatial_upscaler_->Draw(presented_resource);
        aot_swap_timing.Mark(aot::SwapTiming::kSpatial);
        return aot_refreshed;
      });''')
replace('''  // End the frame even if did not present for any reason (the image refresher
  // was not called), to prevent leaking per-frame resources.
  EndSubmission(true);
}''', '''  aot_swap_timing.Mark(aot::SwapTiming::kPublished);
  // End the frame even if did not present for any reason (the image refresher
  // was not called), to prevent leaking per-frame resources.
  EndSubmission(true);
  aot_swap_timing.Finish(aot_presented);
}''')
# Attribute the first (open) end-of-frame submission inside IssueSwap. The
# trailing safety EndSubmission has no open submission and must not overwrite
# the first one's stamps. Source patches remain local to the staged backend.
begin = text.index('bool D3D12CommandProcessor::EndSubmission(bool is_swap) {')
end = text.index('\nbool D3D12CommandProcessor::CanEndSubmissionImmediately()', begin)
submission = text[begin:end]
def submission_replace(before, after):
    global submission
    if submission.count(before) != 1:
        raise ValueError('Unexpected submission timing anchor: ' + before[:80])
    submission = submission.replace(before, after)
submission_replace('''  const ui::d3d12::D3D12Provider& provider = GetD3D12Provider();''', '''  const bool aot_trace_submission = is_swap && submission_open_;
  const ui::d3d12::D3D12Provider& provider = GetD3D12Provider();''')
for before, stage in [
    ('  bool is_closing_frame = is_swap && frame_open_;', 'kAllocator'),
    ('  if (submission_open_) {', 'kEndFrame'),
    ('    pipeline_cache_->EndSubmission();', 'kPipelines'),
    ('    SubmitBarriers();', 'kBarriers'),
    ('    command_list_->Reset(command_allocator, nullptr);', 'kReset'),
    ('    deferred_command_list_.Execute(command_list_, command_list_1_);', 'kDeferred'),
    ('    command_list_->Close();', 'kClosed'),
    ('    direct_queue->ExecuteCommandLists(1, execute_command_lists);', 'kQueue'),
    ('    direct_queue->Signal(submission_fence_, submission_current_++);', 'kSignal'),
    ('  return true;', 'kRetired'),
]:
    stamp = '  if (aot_trace_submission) aot::SwapTiming::SubmissionMark(aot::SwapTiming::' + stage + ');'
    if stage in ('kAllocator', 'kEndFrame', 'kRetired'):
        submission_replace(before, stamp + '\n' + before)
    else:
        submission_replace(before, before + '\n  ' + stamp)
text = text[:begin] + submission + text[end:]
write(output / 'command_processor.cpp', text)
header = read('include/rex/graphics/d3d12/command_processor.h')
header = header.replace('#pragma once', '#pragma once\nnamespace aot { class SpatialUpscaler; }', 1)
anchor = '  Microsoft::WRL::ComPtr<ID3D12Resource> fxaa_source_texture_;'
if header.count(anchor) != 1:
    raise ValueError('Missing private texture anchor')
header = header.replace(anchor, '  std::unique_ptr<aot::SpatialUpscaler> spatial_upscaler_;\n' + anchor)
destination = output / 'include/rex/graphics/d3d12/command_processor.h'
write(destination, header)
