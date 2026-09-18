// Replay the observed guest query packets through the extracted production
// handler. The original handler is a negative control for the real trace pair.
#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include "../src/occlusion_trace.h"

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
#define assert_true(value) require(value, "packet size assertion")
bool occlusion_query_enable = true;
int query_occlusion_fake_sample_count = 1000;
#define REXCVAR_GET(name) name
constexpr unsigned XE_GPU_REG_RB_SAMPLE_COUNT_ADDR = 0, XE_GPU_REG_VGT_EVENT_INITIATOR = 1;
namespace rex {
constexpr uint32_t byte_swap(uint32_t v) {
  return (v >> 24) | ((v >> 8) & 0xFF00) | ((v << 8) & 0xFF0000) | (v << 24);
}
}
namespace xenos {
struct xe_gpu_depth_sample_counts {
  uint32_t Total_A, Total_B, ZFail_A, ZFail_B, ZPass_A, ZPass_B, StencilFail_A, StencilFail_B;
};
}
constexpr uint32_t marker = rex::byte_swap(0xFFFFFEED);
constexpr uint32_t end_address = 530890752, begin_address = end_address + 32;
namespace memory {
struct RingBuffer {
  unsigned reads = 0;
  template <typename T> T ReadAndSwap() { ++reads; return 21; }
};
struct Memory {
  std::array<xenos::xe_gpu_depth_sample_counts, 8> snapshots{};
  template <typename T> T TranslatePhysical(uint32_t address) {
    if (address < end_address || address >= end_address + sizeof(snapshots) ||
        (address - end_address) % 32) return nullptr;
    return reinterpret_cast<T>(&snapshots[(address - end_address) / 32]);
  }
};
}
struct RegisterFile { uint32_t values[2]{}; };
struct CommandProcessor {
  memory::Memory mem;
  memory::Memory* memory_ = &mem;
  RegisterFile regs;
  RegisterFile* register_file_ = &regs;
  unsigned fallback = 0;
  void WriteRegister(unsigned index, uint32_t value) { regs.values[index] = value; }
  bool ExecutePacketType3_EVENT_WRITE_ZPD(memory::RingBuffer* reader, uint32_t, uint32_t) {
    ++fallback; reader->ReadAndSwap<uint32_t>(); return true;
  }
};
struct D3D12CommandProcessor : CommandProcessor {
  struct Active { bool valid = false; uint32_t sample_count_address = 0, host_index = 0; } active_occlusion_query_;
  bool occlusion_query_resources_available_ = true;
  uint64_t frame_current_ = 434;
  unsigned begins = 0, ends = 0, disables = 0;
  uint32_t host_samples = 0;
  void DisableHostOcclusionQueries() {
    ++disables; active_occlusion_query_ = {}; occlusion_query_resources_available_ = false;
  }
  bool BeginGuestOcclusionQuery(uint32_t address) {
    if (active_occlusion_query_.valid) { DisableHostOcclusionQueries(); return false; }
    ++begins; active_occlusion_query_ = {true, address, begins - 1}; return true;
  }
  bool EndGuestOcclusionQuery(uint32_t, xenos::xe_gpu_depth_sample_counts* counts) {
    ++ends; active_occlusion_query_ = {}; *counts = {};
    counts->Total_A = counts->ZPass_A = host_samples; return true;
  }
  bool ExecuteOriginal(memory::RingBuffer*, uint32_t, uint32_t);
  bool ExecuteFixed(memory::RingBuffer*, uint32_t, uint32_t);
  void Packet(uint32_t address, bool original = false) {
    regs.values[0] = address; memory::RingBuffer reader;
    require(original ? ExecuteOriginal(&reader, 0, 1) : ExecuteFixed(&reader, 0, 1), "packet failed");
    require(reader.reads == 1, "packet was not consumed exactly once");
  }
};
#include "occlusion_packet_original.inc"
#include "occlusion_packet_fixed.inc"

int main() {
  try {
    D3D12CommandProcessor old;
    std::memset(&old.mem.snapshots[1], 0x10, 32);
    old.Packet(begin_address, true);
    old.mem.snapshots[0].ZPass_A = marker;
    old.Packet(end_address, true);
    require(old.disables == 1 && old.ends == 0 && old.mem.snapshots[0].ZPass_A == 1000,
            "original handler did not reproduce the recorded false-visible fallback");
    for (uint32_t samples : {0u, 1u, 29u, 65535u}) {
      D3D12CommandProcessor fixed; fixed.host_samples = samples;
      for (int repeat = 0; repeat < 4; ++repeat) {
        std::memset(&fixed.mem.snapshots[1], 0x10, 32);
        fixed.Packet(begin_address);
        const xenos::xe_gpu_depth_sample_counts zero{};
        require(std::memcmp(&fixed.mem.snapshots[1], &zero, 32) == 0, "start snapshot was not initialized");
        fixed.mem.snapshots[0].ZPass_A = marker;
        fixed.Packet(end_address);
        const auto& end = fixed.mem.snapshots[0];
        require(end.ZPass_A + end.ZPass_B - fixed.mem.snapshots[1].ZPass_A == samples,
                "guest end-minus-start result differs from host visibility");
        require(end.Total_A == samples && end.ZFail_A == 0 && end.StencilFail_A == 0,
                "unexpected result fields");
      }
      require(fixed.ends == 4 && fixed.disables == 0 && fixed.occlusion_query_resources_available_,
              "ordinary paired queries disabled real visibility");
    }
    D3D12CommandProcessor same;
    same.host_samples = 19; same.Packet(end_address);
    same.mem.snapshots[0].ZFail_B = marker; same.Packet(end_address);
    require(same.ends == 1 && same.mem.snapshots[0].ZPass_A == 19, "same-address/ZFail protocol regressed");
    D3D12CommandProcessor mismatch;
    mismatch.Packet(begin_address + 32); mismatch.mem.snapshots[0].ZPass_A = marker;
    mismatch.Packet(end_address);
    require(mismatch.disables == 1 && mismatch.ends == 0, "unrelated end was incorrectly paired");
    D3D12CommandProcessor nested;
    nested.Packet(begin_address); nested.Packet(begin_address);
    require(nested.disables == 1, "nested begin was silently accepted");
    D3D12CommandProcessor unavailable;
    unavailable.occlusion_query_resources_available_ = false; unavailable.Packet(end_address);
    require(unavailable.fallback == 1, "unavailable resource fallback regressed");
    D3D12CommandProcessor invalid;
    invalid.Packet(0); require(invalid.disables == 1, "invalid physical address was not rejected");
    std::cout << "Production query replay passes; original reproduces mismatched-snapshot failure.\n";
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
