"""Stage an opt-in, conservative zero-stencil transfer experiment."""
import hashlib
from pathlib import Path
import sys

source,out=map(Path,sys.argv[1:])
def read(name,digest):
    data=(source/name).read_bytes().replace(b'\r\n',b'\n')
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Unexpected pinned stencil source: '+name)
    return data.decode()
def replace(code,before,after):
    if code.count(before)!=1:raise ValueError('Expected one stencil anchor: '+before[:100])
    return code.replace(before,after)
def write(path,code):
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists() or path.read_text()!=code:path.write_text(code)

text=read('src/graphics/d3d12/render_target_cache.cpp','7bc68f0891b9ec0a108a3e1b34e980619245d28ab86f201846b40230760b1dd0')
header=read('include/rex/graphics/d3d12/render_target_cache.h','7933dd49bb409393300872b88aaef6c076226a9d3bd02164ecdd8a43a1c52321')
header=replace(header,'#include <algorithm>','#include "src/zero_stencil_tiles.h"\n#include <algorithm>')
header=replace(header,'    ID3D12Resource* resource() const { return resource_.Get(); }',
    '    aot::ZeroStencilTiles zero_stencil_tiles;\n    ID3D12Resource* resource() const { return resource_.Get(); }')
header=replace(header,'  bool use_stencil_reference_output_ = false;',
    '  bool aot_zero_stencil_transfers_ = false;\n  bool aot_trace_zero_stencil_ = false;\n  bool use_stencil_reference_output_ = false;')
text=replace(text,'bool D3D12RenderTargetCache::Initialize() {', '''bool D3D12RenderTargetCache::Initialize() {
  // Latch even when an embedding application does not finalize CVar lifecycle.
  // Enabling tracking after unobserved writes would make old knowledge unsafe.
  aot_zero_stencil_transfers_ = REXCVAR_GET(aot_zero_stencil_transfers);
  aot_trace_zero_stencil_ = REXCVAR_GET(aot_trace_zero_stencil);''')
text=replace(text,'REXCVAR_DEFINE_BOOL(native_stencil_value_output_d3d12_intel,', '''REXCVAR_DEFINE_BOOL(aot_zero_stencil_transfers, false, "GPU/Sierra Romeo",
                    "Experiment: omit bit copies only for explicitly proven zero stencil tiles")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);
REXCVAR_DEFINE_BOOL(aot_trace_zero_stencil, false, "GPU/Sierra Romeo",
                    "Track and report zero-stencil candidates without enabling omission")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

REXCVAR_DEFINE_BOOL(native_stencil_value_output_d3d12_intel,''')
text=replace(text,'      SetCommandListRenderTargets(depth_and_color_render_targets);', '''      // Conservatively forget all stencil knowledge before any guest draw
      // with stencil enabled, including KEEP-only draws and rejected geometry.
      if (!use_stencil_reference_output_ &&
          (aot_zero_stencil_transfers_ || aot_trace_zero_stencil_) &&
          normalized_depth_control.stencil_enable && depth_and_color_render_targets[0]) {
        static_cast<D3D12RenderTarget*>(depth_and_color_render_targets[0])->zero_stencil_tiles.Invalidate();
      }
      SetCommandListRenderTargets(depth_and_color_render_targets);''')
text=replace(text,'  bool resolve_clear_needed = render_target_resolve_clear_values && resolve_clear_rectangle;', '''  const bool track_zero_stencil = !use_stencil_reference_output_ &&
      (aot_zero_stencil_transfers_ || aot_trace_zero_stencil_);
  bool resolve_clear_needed = render_target_resolve_clear_values && resolve_clear_rectangle;''')
text=replace(text,'''    const std::vector<Transfer>& current_transfers = render_target_transfers[i];
    if (current_transfers.empty() && !resolve_clear_needed) {''', '''    const std::vector<Transfer>& current_transfers = render_target_transfers[i];
    // Clear knowledge before potentially failing transfer setup. Never retain
    // old destination knowledge over a color/depth ownership transfer.
    if (track_zero_stencil && dest_rt->key().is_depth) {
      auto& zero = static_cast<D3D12RenderTarget*>(dest_rt)->zero_stencil_tiles;
      for (const Transfer& transfer : current_transfers)
        zero.InvalidateRange(dest_rt->key().base_tiles, transfer.start_tiles, transfer.end_tiles);
    }
    if (current_transfers.empty() && !resolve_clear_needed) {''')
text=replace(text,'''        for (uint32_t j = 0; j <= uint32_t(is_stencil_bit) * 7; ++j) {''', '''        bool known_zero_stencil = track_zero_stencil && is_stencil_bit &&
            source_d3d12_rt.key().is_depth && &source_d3d12_rt != &dest_d3d12_rt;
        if (known_zero_stencil) {
          for (auto source_it = it_merged_first; source_it != std::next(it_merged_last); ++source_it) {
            if (!source_d3d12_rt.zero_stencil_tiles.Contains(source_d3d12_rt.key().base_tiles,
                    source_it->transfer.start_tiles, source_it->transfer.end_tiles)) {
              known_zero_stencil = false;
              break;
            }
          }
        }
        const bool omit_stencil = known_zero_stencil && aot_zero_stencil_transfers_;
        if (track_zero_stencil && is_stencil_bit && aot_trace_zero_stencil_) {
          static thread_local uint64_t checks = 0, zero_candidates = 0, omitted = 0;
          ++checks; zero_candidates += known_zero_stencil; omitted += omit_stencil;
          if (checks <= 16 || checks % 512 == 0)
            REXGPU_INFO("AOT zero stencil: checks={}, candidates={}, omitted={}, submission={}",
                        checks, zero_candidates, omitted, current_submission);
        }
        // Destination rectangles have already been cleared to zero above.
        // Keep barriers/bindings/geometry and skip only the eight bit writes.
        for (uint32_t j = 0; !omit_stencil && j <= uint32_t(is_stencil_bit) * 7; ++j) {''')
text=replace(text,'''          command_list.D3DDrawInstanced(transfer_vertex_count, 1, 0, 0);
        }
      }
    }

    // Perform the clear.''', '''          command_list.D3DDrawInstanced(transfer_vertex_count, 1, 0, 0);
        }
        // A clear cutout may preserve only part of a tile. Leave these tiles
        // unknown here; the actual resolve clear below can establish knowledge.
        if (track_zero_stencil && is_stencil_bit && !resolve_clear_needed &&
            source_d3d12_rt.key().is_depth) {
          for (auto source_it = it_merged_first; source_it != std::next(it_merged_last); ++source_it)
            dest_d3d12_rt.zero_stencil_tiles.TransferFrom(source_d3d12_rt.zero_stencil_tiles,
                source_d3d12_rt.key().base_tiles, dest_rt_key.base_tiles,
                source_it->transfer.start_tiles, source_it->transfer.end_tiles);
        }
      }
    }

    // Perform the clear.''')
text=replace(text,'''                                              &clear_rect);
      } else {''', '''                                              &clear_rect);
        if (track_zero_stencil)
          dest_d3d12_rt.zero_stencil_tiles.Clear(dest_rt_key.GetPitchTiles(),
              uint32_t(dest_rt_key.msaa_samples), resolve_clear_rectangle->x_pixels,
              resolve_clear_rectangle->y_pixels, resolve_clear_rectangle->width_pixels,
              resolve_clear_rectangle->height_pixels, (UINT(clear_value) & 0xFF) == 0);
      } else {''')
write(out/'render_target_cache.cpp',text)
write(out/'include/rex/graphics/d3d12/render_target_cache.h',header)
