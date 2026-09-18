#pragma once

#include <chrono>
#include <cstdint>
#include <fstream>
#include <filesystem>
#include <limits>
#include <set>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#endif
#include <rex/cvar.h>
#include <rex/graphics/pipeline/shader/shader.h>
#include <rex/graphics/pipeline/texture/util.h>
#include <rex/graphics/util/draw.h>
#include <rex/logging.h>
#include <rex/ui/renderdoc_api.h>

// Read-only diagnostics shared by the spatial and experimental GPU plugins.
// Inert after its first check when no capture path is configured.
REXCVAR_DEFINE_STRING(aot_draw_capture_path, "", "GPU/Sierra Romeo",
    "Capture one GPU frame's rasterizer state to CSV, with a .fetch.csv sidecar");
REXCVAR_DEFINE_STRING(aot_shader_survey_path, "", "GPU/Sierra Romeo",
    "Diagnostic CSV of first observed draw for each shader pair; bounded to 8192 pairs, empty disables");
REXCVAR_DEFINE_INT32(aot_draw_capture_after_ms, 155000, "GPU/Sierra Romeo",
    "Delay after the first draw before capturing a complete GPU frame")
    .range(0, 600000);
REXCVAR_DEFINE_STRING(aot_draw_capture_trigger_file, "", "GPU/Sierra Romeo",
    "Wait for creation of this initially absent file instead of the capture delay; one complete guest-frame capture per run");
REXCVAR_DEFINE_BOOL(aot_renderdoc_capture, false, "GPU/Sierra Romeo",
    "With draw capture enabled and RenderDoc injected, capture across the selected guest GPU frame");
REXCVAR_DEFINE_INT32(aot_renderdoc_capture_frames, 1, "GPU/Sierra Romeo",
    "Guest frames in one diagnostic RenderDoc capture; CSV describes the first frame only")
    .range(1, 120);

namespace aot {
class GpuDrawDiagnostics {
  using Clock = std::chrono::steady_clock;
  Clock::time_point start_ = Clock::now();
  std::ofstream draws_, fetches_, constants_, quads_, resolves_, packets_;
  std::ofstream survey_;
  std::set<std::pair<uint64_t,uint64_t>> surveyed_;
  bool survey_enabled_ = false;
  std::string path_;
  std::filesystem::path trigger_path_;
  uint64_t previous_frame_ = std::numeric_limits<uint64_t>::max();
  uint64_t capture_frame_ = std::numeric_limits<uint64_t>::max();
  uint64_t count_ = 0;
  uint64_t resolve_count_ = 0;
  uint64_t packet_count_ = 0;
  uint64_t snapshot_bytes_ = 0;
  int32_t delay_ = 0;
  bool done_ = false;
  RENDERDOC_API_1_0_0* renderdoc_ = nullptr;
  bool renderdoc_capturing_ = false;
  uint32_t renderdoc_frames_remaining_ = 1;
  uint32_t renderdoc_frames_completed_ = 0;
  void EndRenderDocCapture() {
    if (!renderdoc_capturing_) return;
    const auto result = renderdoc_->EndFrameCapture(nullptr, nullptr);
    renderdoc_capturing_ = false;
    REXLOG_INFO("AOT RenderDoc guest frame ended: gpu_frame={}, success={}, completed_frames={}",
                capture_frame_, result, renderdoc_frames_completed_);
  }
  void SnapshotTexture(const rex::graphics::RegisterFile& regs, uint32_t slot,
                       rex::graphics::xenos::TextureFormat format, uint32_t bpp_log2,
                       const char* suffix, const uint8_t* physical_memory) {
#ifdef _WIN32
    using namespace rex::graphics;
    const auto fetch = regs.GetTextureFetch(slot);
    if (fetch.type != xenos::FetchConstantType::kTexture ||
        fetch.dimension != xenos::DataDimension::k2DOrStacked || fetch.stacked ||
        !fetch.tiled || fetch.format != format) return;
    const uint64_t address = uint64_t(fetch.base_address) << 12;
    const auto size = texture_util::GetTiledAddressUpperBound2D(
        fetch.size_2d.width + 1, fetch.size_2d.height + 1, fetch.pitch << 5, bpp_log2);
    // Bound both each read and all snapshots in the selected frame.
    if (!size || size > 4 * 1024 * 1024 || address > 0x20000000 - size ||
        snapshot_bytes_ + size > 32 * 1024 * 1024) return;
    snapshot_bytes_ += size;
    std::vector<uint8_t> bytes(size);
    SIZE_T read = 0;
    if (ReadProcessMemory(GetCurrentProcess(), physical_memory + address,
                          bytes.data(), size, &read) && read == size) {
      std::ofstream texture(path_ + ".draw-" + std::to_string(count_) + suffix, std::ios::binary);
      texture.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
      REXLOG_INFO("AOT texture CPU snapshot: draw={}, slot={}, address={:08X}, bytes={}, suffix={}, written={}",
                  count_, slot, address, size, suffix, bool(texture));
    } else {
      REXLOG_WARN("AOT texture CPU snapshot failed: draw={}, slot={}", count_, slot);
    }
#endif
  }
 public:
  GpuDrawDiagnostics() {
    const auto survey_path = REXCVAR_GET(aot_shader_survey_path);
    if (!survey_path.empty()) {
      survey_.open(survey_path);
      survey_enabled_ = bool(survey_);
      if (survey_enabled_) {
        survey_ << "elapsed_ms,gpu_frame,vs_hash,ps_hash\n";
        survey_.flush();
      } else REXLOG_WARN("AOT shader survey could not open {}", survey_path);
    }
    const auto path = REXCVAR_GET(aot_draw_capture_path);
    if (path.empty()) { done_ = true; return; }
    path_ = path;
    const auto trigger = REXCVAR_GET(aot_draw_capture_trigger_file);
    if (!trigger.empty()) {
      trigger_path_ = std::filesystem::u8path(trigger);
      std::error_code error;
      const bool stale = std::filesystem::exists(trigger_path_, error);
      if (stale || error) {
        REXLOG_WARN("AOT draw capture requires an initially absent, accessible trigger file: {}", trigger);
        done_ = true;
        return;
      }
      REXLOG_INFO("AOT draw capture armed for trigger file: {}", trigger);
    }
#ifdef _WIN32
    if (REXCVAR_GET(aot_renderdoc_capture)) {
      const auto module = GetModuleHandleW(L"renderdoc.dll");
      const auto get_api = module ? reinterpret_cast<pRENDERDOC_GetAPI>(GetProcAddress(module, "RENDERDOC_GetAPI")) : nullptr;
      if (!get_api || !get_api(eRENDERDOC_API_Version_1_0_0, reinterpret_cast<void**>(&renderdoc_))) {
        REXLOG_WARN("AOT RenderDoc guest frame capture requested but injected API is unavailable");
        renderdoc_ = nullptr;
      }
    }
#endif
    delay_ = REXCVAR_GET(aot_draw_capture_after_ms);
    renderdoc_frames_remaining_ = static_cast<uint32_t>(REXCVAR_GET(aot_renderdoc_capture_frames));
    draws_.open(path); fetches_.open(path + ".fetch.csv");
    constants_.open(path + ".constants.bin", std::ios::binary);
    quads_.open(path + ".quads.csv");
    resolves_.open(path + ".resolves.csv");
    packets_.open(path + ".copy-packets.csv");
    if (!draws_ || !fetches_ || !constants_ || !quads_ || !resolves_ || !packets_) {
      REXLOG_WARN("AOT draw capture could not open {}", path);
      done_ = true;
      return;
    }
    draws_.precision(9);
    draws_ << "gpu_frame,draw,elapsed_ms,vs_hash,ps_hash,scale_x,scale_y,viewport_x,viewport_y,viewport_w,viewport_h,scissor_x,scissor_y,scissor_w,scissor_h,ndc_scale_x,ndc_scale_y,ndc_offset_x,ndc_offset_y,clip_control,vte_control,su_mode,vtx_control,window_offset,xscale,xoffset,yscale,yoffset,surface_info,color_info0,color_info1,color_info2,color_info3,depth_info,color_mask,texture_mask,depth_control,normalized_depth_control,color_control,alpha_ref,front_scale,front_offset,back_scale,back_offset,viewport_z_min,viewport_z_max,ndc_scale_z,ndc_offset_z\n";
    fetches_ << "gpu_frame,draw,slot,word0,word1,word2,word3,word4,word5\n";
    quads_ << "gpu_frame,draw,fetch95_word0,fetch95_word1,cpu_read_ok,bytes_hex\n";
    resolves_ << "gpu_frame,resolve,after_draw_count,copy_control,copy_dest_base,copy_dest_info,copy_dest_pitch,surface_info,color_info0,color_info1,color_info2,color_info3,depth_info,window_offset,window_scissor_tl,window_scissor_br,su_mode,vtx_control,fetch0_word0,fetch0_word1,cpu_read_ok,vertices_hex\n";
    packets_ << "capture_frame,observation,after_draw_count,packet,bin_select,bin_mask,mode,destination,viz_query\n";
  }
  ~GpuDrawDiagnostics() { EndRenderDocCapture(); }
  void CopyPacket(uint32_t packet, uint64_t bin_select, uint64_t bin_mask,
                  uint32_t mode, uint32_t destination, uint32_t viz_query) {
    if (done_ || capture_frame_ == std::numeric_limits<uint64_t>::max() ||
        !packets_.is_open() || packet_count_ >= 8192) return;
    packets_ << capture_frame_ << ',' << packet_count_++ << ',' << count_ << ','
             << packet << ',' << bin_select << ',' << bin_mask << ',' << mode
             << ',' << destination << ',' << viz_query << '\n';
  }
  // Observe copy requests before the existing resolve path. Together with the
  // closed GPU capture this distinguishes absent guest copies from rejected or
  // ineffective copies. These CPU vertices are observations, not GPU readback.
  void Resolve(uint64_t frame, const rex::graphics::RegisterFile& regs,
               const uint8_t* physical_memory) {
    using namespace rex::graphics;
    if (done_ || frame != capture_frame_ || !resolves_.is_open() || resolve_count_ >= 8192) return;
    resolves_ << frame << ',' << resolve_count_++ << ',' << count_;
    for (auto index : {XE_GPU_REG_RB_COPY_CONTROL, XE_GPU_REG_RB_COPY_DEST_BASE,
                       XE_GPU_REG_RB_COPY_DEST_INFO, XE_GPU_REG_RB_COPY_DEST_PITCH,
                       XE_GPU_REG_RB_SURFACE_INFO, XE_GPU_REG_RB_COLOR_INFO,
                       XE_GPU_REG_RB_COLOR1_INFO, XE_GPU_REG_RB_COLOR2_INFO,
                       XE_GPU_REG_RB_COLOR3_INFO, XE_GPU_REG_RB_DEPTH_INFO,
                       XE_GPU_REG_PA_SC_WINDOW_OFFSET, XE_GPU_REG_PA_SC_WINDOW_SCISSOR_TL,
                       XE_GPU_REG_PA_SC_WINDOW_SCISSOR_BR, XE_GPU_REG_PA_SU_SC_MODE_CNTL,
                       XE_GPU_REG_PA_SU_VTX_CNTL}) resolves_ << ',' << regs[index];
    const auto fetch = regs.GetVertexFetch(0);
    uint8_t bytes[24]{};
    bool ok = false;
#ifdef _WIN32
    const uint64_t address = uint64_t(fetch.address) << 2;
    if (fetch.type == xenos::FetchConstantType::kVertex && fetch.size == 6 &&
        address <= 0x20000000 - sizeof(bytes)) {
      SIZE_T read = 0;
      ok = ReadProcessMemory(GetCurrentProcess(), physical_memory + address,
                            bytes, sizeof(bytes), &read) && read == sizeof(bytes);
    }
#endif
    resolves_ << ',' << fetch.dword_0 << ',' << fetch.dword_1 << ',' << ok << ',';
    if (ok) {
      constexpr char hex[] = "0123456789abcdef";
      for (const auto byte : bytes) resolves_ << hex[byte >> 4] << hex[byte & 15];
    }
    resolves_ << '\n';
  }
  void Draw(uint64_t frame, const rex::graphics::RegisterFile& regs,
            const rex::graphics::draw_util::ViewportInfo& viewport,
            const rex::graphics::draw_util::Scissor& scissor,
            const rex::graphics::Shader* vs, const rex::graphics::Shader* ps,
            uint32_t scale_x, uint32_t scale_y, uint32_t color_mask, uint32_t texture_mask,
            const uint8_t* physical_memory) {
    using namespace rex::graphics;
    if (survey_enabled_ && vs) {
      const auto pair=std::make_pair(vs->ucode_data_hash(),ps ? ps->ucode_data_hash() : uint64_t(0));
      if (surveyed_.insert(pair).second) {
        const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start_).count();
        survey_ << elapsed << ',' << frame << ',' << std::hex << pair.first << ',' << pair.second << std::dec << '\n';
        survey_.flush();
        if (!survey_ || surveyed_.size()>=8192) {
          survey_enabled_=false;
          REXLOG_WARN("AOT shader survey stopped: pairs={}, write_ok={}",surveyed_.size(),bool(survey_));
          survey_.close();
        }
      }
    }
    if (done_) return;
    if (previous_frame_ != frame) {
      previous_frame_ = frame;
      if (capture_frame_ != std::numeric_limits<uint64_t>::max()) {
        if (draws_.is_open()) {
          draws_.close(); fetches_.close(); constants_.close(); quads_.close(); resolves_.close(); packets_.close();
          REXLOG_INFO("AOT draw capture complete: gpu_frame={}, draws={}", capture_frame_, count_);
        }
        ++renderdoc_frames_completed_;
        if (!renderdoc_capturing_ || --renderdoc_frames_remaining_ == 0) {
          EndRenderDocCapture();
          done_ = true;
        }
        return;
      }
      const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start_).count();
      bool requested = elapsed >= delay_;
      if (!trigger_path_.empty()) {
        std::error_code error;
        requested = std::filesystem::exists(trigger_path_, error) && !error;
      }
      if (requested) {
        capture_frame_ = frame;
        if (renderdoc_ && !renderdoc_->IsFrameCapturing()) {
          renderdoc_->StartFrameCapture(nullptr, nullptr);
          renderdoc_capturing_ = renderdoc_->IsFrameCapturing() != 0;
          REXLOG_INFO("AOT RenderDoc guest frame started: gpu_frame={}, capturing={}", frame, renderdoc_capturing_);
        }
        REXLOG_INFO("AOT draw capture started: gpu_frame={}, elapsed_ms={}", frame, elapsed);
      }
    }
    if (frame != capture_frame_) return;
    const double elapsed = std::chrono::duration<double, std::milli>(Clock::now() - start_).count();
    draws_ << frame << ',' << count_ << ',' << elapsed << ',' << std::hex
           << vs->ucode_data_hash() << ',' << (ps ? ps->ucode_data_hash() : 0) << std::dec
           << ',' << scale_x << ',' << scale_y;
    for (auto x : viewport.xy_offset) draws_ << ',' << x;
    for (auto x : viewport.xy_extent) draws_ << ',' << x;
    for (auto x : scissor.offset) draws_ << ',' << x;
    for (auto x : scissor.extent) draws_ << ',' << x;
    draws_ << ',' << viewport.ndc_scale[0] << ',' << viewport.ndc_scale[1]
           << ',' << viewport.ndc_offset[0] << ',' << viewport.ndc_offset[1];
    for (auto index : {XE_GPU_REG_PA_CL_CLIP_CNTL, XE_GPU_REG_PA_CL_VTE_CNTL,
                       XE_GPU_REG_PA_SU_SC_MODE_CNTL, XE_GPU_REG_PA_SU_VTX_CNTL,
                       XE_GPU_REG_PA_SC_WINDOW_OFFSET}) draws_ << ',' << regs[index];
    for (auto index : {XE_GPU_REG_PA_CL_VPORT_XSCALE, XE_GPU_REG_PA_CL_VPORT_XOFFSET,
                       XE_GPU_REG_PA_CL_VPORT_YSCALE, XE_GPU_REG_PA_CL_VPORT_YOFFSET})
      draws_ << ',' << regs.Get<float>(index);
    for (auto index : {XE_GPU_REG_RB_SURFACE_INFO, XE_GPU_REG_RB_COLOR_INFO,
                       XE_GPU_REG_RB_COLOR1_INFO, XE_GPU_REG_RB_COLOR2_INFO,
                       XE_GPU_REG_RB_COLOR3_INFO, XE_GPU_REG_RB_DEPTH_INFO}) draws_ << ',' << regs[index];
    draws_ << ',' << color_mask << ',' << texture_mask
           << ',' << regs[XE_GPU_REG_RB_DEPTHCONTROL]
           << ',' << draw_util::GetNormalizedDepthControl(regs).value
           << ',' << regs[XE_GPU_REG_RB_COLORCONTROL];
    for (auto index : {XE_GPU_REG_RB_ALPHA_REF, XE_GPU_REG_PA_SU_POLY_OFFSET_FRONT_SCALE,
                       XE_GPU_REG_PA_SU_POLY_OFFSET_FRONT_OFFSET,
                       XE_GPU_REG_PA_SU_POLY_OFFSET_BACK_SCALE, XE_GPU_REG_PA_SU_POLY_OFFSET_BACK_OFFSET})
      draws_ << ',' << regs.Get<float>(index);
    draws_ << ',' << viewport.z_min << ',' << viewport.z_max
           << ',' << viewport.ndc_scale[2] << ',' << viewport.ndc_offset[2] << '\n';
    // All 512 float constants as little-endian raw register words. Record n
    // starts at n * 8192 bytes, matching the draw column in the CSV.
    constants_.write(reinterpret_cast<const char*>(&regs[XE_GPU_REG_SHADER_CONSTANT_000_X]), 8192);
    for (uint32_t slot = 0; slot < 32; ++slot) {
      if (!(texture_mask & (uint32_t(1) << slot))) continue;
      fetches_ << frame << ',' << count_ << ',' << slot;
      for (uint32_t word = 0; word < 6; ++word)
        fetches_ << ',' << regs[XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0 + slot * 6 + word];
      fetches_ << '\n';
    }
    // The decoded final-blend, downsample and separable-blur vertex shaders
    // all fetch four 32-byte vertices
    // through vf0 (hardware fetch constant 95). This is a CPU-memory snapshot,
    // not a claim that the GPU's resident vertex buffer is identical.
    if (vs->ucode_data_hash() == 0xDA84B19697871CB2ull ||
        vs->ucode_data_hash() == 0x267A109391BF21BAull ||
        vs->ucode_data_hash() == 0x124FE38D12A00254ull) {
      const auto fetch = regs.GetVertexFetch(95);
      uint8_t bytes[128]{};
      bool ok = false;
#ifdef _WIN32
      const uint64_t address = uint64_t(fetch.address) << 2;
      if (fetch.type == xenos::FetchConstantType::kVertex && fetch.size >= 32 &&
          address <= 0x20000000 - sizeof(bytes)) {
        SIZE_T read = 0;
        ok = ReadProcessMemory(GetCurrentProcess(), physical_memory + address,
                               bytes, sizeof(bytes), &read) && read == sizeof(bytes);
      }
#endif
      quads_ << frame << ',' << count_ << ',' << fetch.dword_0 << ',' << fetch.dword_1 << ',' << ok << ',';
      if (ok) {
        constexpr char hex[] = "0123456789abcdef";
        for (const auto byte : bytes) quads_ << hex[byte >> 4] << hex[byte & 15];
      }
      quads_ << '\n';
    }
    if (ps) {
      switch (ps->ucode_data_hash()) {
        case 0x17401B22D4626678ull:
          SnapshotTexture(regs, 2, xenos::TextureFormat::k_16_16_16_16, 3, ".dof.bin", physical_memory);
          SnapshotTexture(regs, 1, xenos::TextureFormat::k_8_8_8_8, 2, ".sharp.bin", physical_memory);
          break;
        case 0x86C85D8414312072ull:
          SnapshotTexture(regs, 1, xenos::TextureFormat::k_8_8_8_8, 2, ".sharp.bin", physical_memory);
          break;
        case 0x160C967F3B0A2ED3ull:
          SnapshotTexture(regs, 0, xenos::TextureFormat::k_16_16_16_16, 3, ".blur-input.bin", physical_memory);
          break;
      }
    }
    ++count_;
    if (count_ == 8192) {
      EndRenderDocCapture();
      draws_.close(); fetches_.close(); constants_.close(); quads_.close(); resolves_.close(); packets_.close(); done_ = true;
      REXLOG_WARN("AOT draw capture truncated at 8192 draws: gpu_frame={}", capture_frame_);
    }
  }
};
inline GpuDrawDiagnostics& GetGpuDrawDiagnostics() {
  static thread_local GpuDrawDiagnostics diagnostics;
  return diagnostics;
}
}  // namespace aot
