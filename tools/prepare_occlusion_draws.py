"""Count only guest draws, retaining logical query intervals across submissions."""
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
root = Path(__file__).resolve().parents[1]
opcodes = (source / 'include/rex/graphics/xenos.h').read_text()
begin = opcodes.index('enum Type3Opcode {')
opcodes = opcodes[begin:opcodes.index('};', begin) + 2] + '\n'
opcode_path = output / 'occlusion_opcodes.inc'
if not opcode_path.exists() or opcode_path.read_text() != opcodes:
    opcode_path.write_text(opcodes)
path = output / 'command_processor.cpp'
text = path.read_text()


def replace(before, after):
    global text
    if text.count(before) != 1:
        raise ValueError('Expected one occlusion draw anchor: ' + before[:90])
    text = text.replace(before, after)


start = text.index('void D3D12CommandProcessor::DisableHostOcclusionQueries() {')
end = text.index('uint64_t D3D12CommandProcessor::NormalizeOcclusionSamples(', start)
text = text[:start] + '#include "src/occlusion_query_draws.inc"\n\n' + text[end:]
replace('namespace rex::graphics::d3d12 {', '''REXCVAR_DEFINE_BOOL(aot_batch_occlusion_queries, true, "Sierra Romeo",
    "Batch real visibility-query readbacks at guest synchronization boundaries")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace rex::graphics::d3d12 {''')
replace('void D3D12CommandProcessor::OnPrimaryBufferEnd() {',
        'void D3D12CommandProcessor::OnPrimaryBufferEnd() {\n  aot::OcclusionBarrierScope reason(0x100);\n  PrepareForWait();')
replace('bool D3D12CommandProcessor::IssueCopy() {',
        'bool D3D12CommandProcessor::IssueCopy() {\n  aot::OcclusionBarrierScope reason(0x102);\n  PrepareForWait();')
replace('''                                          const uint32_t* host_address, uint32_t dword_count) {
  return pipeline_cache_->LoadShader''', '''                                          const uint32_t* host_address, uint32_t dword_count) {
  aot::OcclusionBarrierScope reason(xenos::PM4_IM_LOAD);
  FlushOcclusionQueriesForMemory(host_address, size_t(dword_count) * sizeof(uint32_t));
  return pipeline_cache_->LoadShader''')
# Before normal rendering resumes, all earlier occlusion draws are visible to
# guest memory consumers. Query-only draw groups can still share one wait.
replace('''                                      bool major_mode_explicit) {
#if XE_GPU_FINE_GRAINED_DRAW_SCOPES''', '''                                      bool major_mode_explicit) {
  // Flush before acquiring scratch buffers or setting draw bindings. A flush
  // can close the command list and invalidate those bindings.
  if (!active_occlusion_query_.valid ||
      aot_pending_occlusion_draws_ + aot_occlusion_draw_indices_.size() >= kMaxOcclusionQueries) {
    aot::OcclusionBarrierScope reason(0x101);
    PrepareForWait();
  }
#if XE_GPU_FINE_GRAINED_DRAW_SCOPES''')
start = text.index('    if (active_occlusion_query_.valid && occlusion_query_heap_) {')
end = text.index('    pipeline_cache_->EndSubmission();', start)
text = text[:start] + '''    // Logical guest queries may span submissions. Every host query is already
    // ended and resolved immediately after its guest draw, in the same list.
    if (active_occlusion_query_.valid) {
      aot::TraceOcclusion("submission_preserves_query", frame_current_,
          active_occlusion_query_.sample_count_address, true, occlusion_query_resources_available_,
          active_occlusion_query_.host_index, aot_occlusion_draw_indices_.size());
    }

''' + text[end:]
for draw in (
    '''    deferred_command_list_.D3DDrawInstanced(primitive_processing_result.host_draw_vertex_count, 1,
                                            0, 0);''',
    '''    deferred_command_list_.D3DDrawIndexedInstanced(
        primitive_processing_result.host_draw_vertex_count, 1, 0, 0, 0);''',
):
    replace(draw, '    const uint32_t aot_query = BeginOcclusionQueryDraw();\n' + draw +
            '\n    EndOcclusionQueryDraw(aot_query);')
header_path = output / 'include/rex/graphics/d3d12/command_processor.h'
header = header_path.read_text()
anchor = '  bool BeginGuestOcclusionQuery(uint32_t sample_count_address);'
if header.count(anchor) != 1:
    raise ValueError('Missing query declaration')
header = header.replace(anchor, '  uint32_t BeginOcclusionQueryDraw();\n'
                       '  void EndOcclusionQueryDraw(uint32_t index);\n' + anchor)
anchor = '  uint32_t occlusion_query_cursor_ = 0;'
if header.count(anchor) != 1:
    raise ValueError('Missing query cursor')
header = header.replace(anchor, anchor + '\n  std::vector<uint32_t> aot_occlusion_draw_indices_;')
header = header.replace(anchor, anchor + '\n' + (root / 'src/occlusion_pending_members.inc').read_text())
for target, content in ((path, text), (header_path, header)):
    if target.read_text() != content:
        target.write_text(content)
