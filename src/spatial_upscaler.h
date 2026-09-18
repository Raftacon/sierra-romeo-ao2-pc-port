#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <rex/ui/d3d12/d3d12_provider.h>

namespace aot {
class FrameColorProbe;
// Runs after the game's gamma/FXAA submission, on the same direct queue.
// Own command lists keep presentation work out of the guest state cache.
class SpatialUpscaler {
 public:
  explicit SpatialUpscaler(const rex::ui::d3d12::D3D12Provider& provider);
  ~SpatialUpscaler();
  bool Prepare(uint32_t width, uint32_t height, uint32_t output_width, uint32_t output_height);
  ID3D12Resource* source() const { return source_.Get(); }
  bool Draw(ID3D12Resource* destination);
 private:
  bool Initialize();
  bool Wait(uint64_t value);
  template<class T> using Ptr = Microsoft::WRL::ComPtr<T>;
  const rex::ui::d3d12::D3D12Provider& provider_;
  std::unique_ptr<FrameColorProbe> color_probe_;
  Ptr<ID3D12RootSignature> root_;
  Ptr<ID3D12PipelineState> easu_, rcas_;
  Ptr<ID3D12Resource> source_, intermediate_, sharpened_;
  Ptr<ID3D12Fence> fence_;
  HANDLE event_ = nullptr;
  struct Slot {
    Ptr<ID3D12CommandAllocator> allocator;
    Ptr<ID3D12GraphicsCommandList> commands;
    Ptr<ID3D12DescriptorHeap> srv, rtv;
    uint64_t completion = 0;
  };
  std::array<Slot, 3> slots_;
  uint64_t sequence_ = 0;
  bool ready_ = false, failed_ = false;
  uint32_t width_ = 0, height_ = 0, output_width_ = 0, output_height_ = 0;
};
}
