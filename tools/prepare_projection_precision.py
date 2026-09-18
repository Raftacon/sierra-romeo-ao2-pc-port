"""Generate a non-retail precision snippet and hook clean pinned GPU sources."""
from pathlib import Path
import struct
import sys
from equipment_projection_variants import compile_snippet, chunks, shader_words, instructions
import equipment_projection_variants as projection
from projection_fma_snippet import candidate_source


def write(path, text):
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def snippet_header(prefix):
    snippet, messages = compile_snippet()
    if messages:
        raise ValueError('Unexpected compiler diagnostic: ' + messages)
    parts = chunks(snippet)
    words = shader_words(parts)
    ops = list(instructions(words))
    temps = next(a[1] for _, a in ops if a[0] & 2047 == 104)
    flags = next(a[0] for _, a in ops if a[0] & 2047 == 106)
    features = struct.unpack('<2I', next(p[8:] for p in parts if p[:4] == b'SFI0'))
    header = '#pragma once\n#include <cstdint>\nnamespace aot {\n'
    header += f'inline constexpr uint32_t {prefix}_snippet[] = {{\n'
    header += ''.join('  ' + ','.join(f'0x{v:08X}' for v in words[i:i+8]) + ',\n'
                      for i in range(0, len(words), 8))
    header += '};\n'
    header += f'inline constexpr uint32_t {prefix}_extra_temps = {temps};\n'
    header += f'inline constexpr uint32_t {prefix}_global_flags = 0x{flags:08X};\n'
    header += f'inline constexpr uint32_t {prefix}_feature_flags[] = {{' + ','.join(map(str, features)) + '};\n}\n'
    return header


source, output = map(Path, sys.argv[1:])
write(output / 'projection_snippet.h', snippet_header('projection'))
original = projection.HLSL
try:
    projection.HLSL = candidate_source(original)
    write(output / 'projection_fma_snippet.h', snippet_header('projection_fma'))
finally:
    projection.HLSL = original

# Every compiled DXBC translator unit must see the same generated class layout.
# Keep per-translation capture state on the object, not in process/thread globals.
header_path = output / 'projection-include/rex/graphics/pipeline/shader/dxbc_translator.h'
header_path.parent.mkdir(parents=True, exist_ok=True)
header = (source / 'include/rex/graphics/pipeline/shader/dxbc_translator.h').read_text()
anchor = '  uint32_t system_temp_position_;'
if header.count(anchor) != 1:
    raise ValueError('Missing pinned translator class anchor')
header = header.replace(anchor, anchor + '\n'
    '  aot::ProjectionSource aot_projection_source_;\n'
    '  uint32_t aot_projection_saved_world_ = UINT32_MAX;\n'
    '  uint32_t aot_projection_alu_ = 0;\n'
    '  uint32_t aot_projection_captured_mask_ = 0;')
write(header_path, '#include "src/projection_source.h"\n' + header)

for relative, filename, replacements in [
    ('pipeline/shader/translator.cpp', 'translator.cpp', []),
    ('pipeline/shader/dxbc_translator.cpp', 'dxbc_translator.cpp', [
        ('void DxbcShaderTranslator::StartTranslation() {',
         'void DxbcShaderTranslator::StartTranslation() {\n'
         '  aot_projection_source_ = {};\n'
         '  aot_projection_saved_world_ = UINT32_MAX;\n'
         '  aot_projection_alu_ = 0;\n'
         '  aot_projection_captured_mask_ = 0;\n'
         '  if (is_vertex_shader() && aot::ProjectionPrecisionEnabled() &&\n'
         '      aot::AutomaticProjectionPrecisionEnabled()) {\n'
         '    aot_projection_source_ = aot::AnalyzeProjection(current_shader(),\n'
         '        aot::ProjectionSourceSnapshotsEnabled());\n'
         '    if (aot_projection_source_.captured)\n'
         '      aot_projection_saved_world_ = PushSystemTemp();\n'
         '  }'),
        ('    // Release system_temp_position_ and\n'
         '    // system_temp_point_size_edge_flag_kill_vertex_.\n'
         '    PopSystemTemp(2);',
         '    // Release system_temp_position_ and\n'
         '    // system_temp_point_size_edge_flag_kill_vertex_.\n'
         '    PopSystemTemp(2);\n'
         '    if (aot_projection_source_.captured) PopSystemTemp();'),
        ('  return shader_object_bytes;',
         '  if (aot::ProjectionPrecisionEnabled() && is_vertex_shader()) {\n'
         '    aot::ProjectionTranslationLayout layout{system_temp_position_, system_temp_result_,\n'
         '      cbuffer_index_system_constants_, cbuffer_index_float_constants_};\n'
         '    if (aot::AutomaticProjectionPrecisionEnabled()) {\n'
         '      const auto& source=aot_projection_source_;\n'
         '      layout.world=source.world; layout.components=source.components; layout.automatic=true;\n'
         '      layout.captured=source.captured;\n'
         '      layout.captured_mask=aot_projection_captured_mask_;\n'
         '      if (source.captured) layout.world=aot_projection_saved_world_;\n'
         '    }\n'
         '    aot::TranslateProjectionPrecision(current_shader().ucode_data_hash(),\n'
         '      current_translation().modification(), layout, shader_object_bytes);\n'
         '  }\n'
         '  return shader_object_bytes;')]),
    ('pipeline/shader/dxbc_translator_alu.cpp', 'dxbc_translator_alu.cpp', [
        ('  if (instr.IsNop()) {',
         '  const uint32_t aot_alu = aot_projection_alu_++;\n'
         '  if (instr.IsNop()) {'),
        ('  UpdateInstructionPredicationAndEmitDisassembly(instr.is_predicated, instr.predicate_condition);',
         '  UpdateInstructionPredicationAndEmitDisassembly(instr.is_predicated, instr.predicate_condition);\n'
         '  if (aot_projection_source_.captured) {\n'
         '    for (uint32_t row=0; row<4; ++row) {\n'
         '      const auto& source=aot_projection_source_.captures[row];\n'
         '      if (source.alu==aot_alu) {\n'
         '        assert_false(instr.is_predicated);\n'
         '        a_.OpMov(dxbc::Dest::R(aot_projection_saved_world_, 1u<<row),\n'
         '          dxbc::Src::R(source.reg, source.component*0x55u));\n'
         '        aot_projection_captured_mask_ |= 1u<<row;\n'
         '      }\n'
         '    }\n'
         '  }')]),
    ('d3d12/pipeline_cache.cpp', 'pipeline_cache.cpp', [
        ('  bool edram_rov_used =\n',
         '  aot::InitializeProjectionPrecision(provider.GetDevice());\n\n  bool edram_rov_used =\n'),
        ('  auto shader_storage_root = cache_root / "shaders";',
         '  auto shader_storage_root = cache_root / "shaders";\n'
         '  if (aot::ProjectionPrecisionEnabled()) shader_storage_root /=\n'
         '      aot::ProjectionSourceSnapshotsEnabled() ? "aot-projection-auto-v5" :\n'
         '      aot::AutomaticProjectionPrecisionEnabled() ? "aot-projection-auto-v4" :\n'
         '      aot::WallProjectionPrecisionEnabled() ? "aot-projection-v15" : "aot-projection-v9";\n'
         '  if (aot::ProjectionFmaEnabled()) shader_storage_root /= "fma-v1";')]),
]:
    text = (source / 'src/graphics' / relative).read_text()
    for before, after in replacements:
        # Constructor anchor also occurs later; restrict initialization to the
        # constructor, before any translation workers exist.
        if before.startswith('  bool'):
            at = text.index(before)
            if not text.index('PipelineCache::PipelineCache(') < at < text.index('PipelineCache::~PipelineCache()'):
                raise ValueError('Expected provider initialization in the pinned constructor')
            text = text[:at] + text[at:].replace(before, after, 1)
        else:
            if text.count(before) != 1:
                raise ValueError('Missing unique pinned source anchor: ' + before)
            text = text.replace(before, after)
    write(output / filename, '#include "src/projection_precision_runtime.h"\n#include "src/projection_analysis.h"\n' + text)
