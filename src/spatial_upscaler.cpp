#include "spatial_upscaler.h"
#include "frame_color_probe.h"
#include <rex/logging.h>
#include <rex/ui/d3d12/d3d12_util.h>
#include <cmath>

namespace aot {
namespace shaders {
// Pinned SDK bytecode, built from AMD's spatial FSR implementation.
#include "ui/shaders/bytecode/d3d12_5_1/guest_output_triangle_strip_rect_vs.h"
#include "ui/shaders/bytecode/d3d12_5_1/guest_output_ffx_fsr_easu_ps.h"
#include "ui/shaders/bytecode/d3d12_5_1/guest_output_ffx_fsr_rcas_ps.h"
}
namespace {
constexpr auto format = DXGI_FORMAT_R10G10B10A2_UNORM;
constexpr auto resting = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
void Transition(ID3D12GraphicsCommandList* list, ID3D12Resource* resource,
                D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
  D3D12_RESOURCE_BARRIER barrier{};
  barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
  barrier.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
  list->ResourceBarrier(1, &barrier);
}
}
SpatialUpscaler::SpatialUpscaler(const rex::ui::d3d12::D3D12Provider& provider)
    : provider_(provider), color_probe_(FrameColorProbe::FromEnvironment()) {}
SpatialUpscaler::~SpatialUpscaler() {
  if (fence_) Wait(sequence_);
  if (color_probe_) color_probe_->Finish(fence_ ? fence_->GetCompletedValue() : 0);
  if (event_) CloseHandle(event_);
}
bool SpatialUpscaler::Wait(uint64_t value) {
  if (fence_->GetCompletedValue() >= value) return true;
  if (FAILED(fence_->SetEventOnCompletion(value, event_))) return false;
  return WaitForSingleObject(event_, 10000) == WAIT_OBJECT_0;
}
bool SpatialUpscaler::Initialize() {
  auto* device = provider_.GetDevice();
  if (!event_) event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  if (!event_ || FAILED(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence_)))) return false;
  D3D12_DESCRIPTOR_RANGE range{D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 1, 0, 0, 0};
  D3D12_ROOT_PARAMETER parameters[3]{};
  parameters[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
  parameters[0].DescriptorTable = {1, &range};
  parameters[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
  for (unsigned i = 1; i < 3; ++i) {
    parameters[i].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    parameters[i].Constants = {0, 0, 4};
    parameters[i].ShaderVisibility = i == 1 ? D3D12_SHADER_VISIBILITY_VERTEX : D3D12_SHADER_VISIBILITY_PIXEL;
  }
  D3D12_STATIC_SAMPLER_DESC sampler{};
  sampler.Filter = D3D12_FILTER_MIN_MAG_LINEAR_MIP_POINT;
  sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
  sampler.MaxAnisotropy = 1;
  sampler.ComparisonFunc = D3D12_COMPARISON_FUNC_NEVER;
  sampler.MaxLOD = D3D12_FLOAT32_MAX;
  sampler.ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
  D3D12_ROOT_SIGNATURE_DESC signature{3, parameters, 1, &sampler, D3D12_ROOT_SIGNATURE_FLAG_NONE};
  root_.Attach(rex::ui::d3d12::util::CreateRootSignature(provider_, signature));
  if (!root_) return false;
  D3D12_GRAPHICS_PIPELINE_STATE_DESC pipeline{};
  pipeline.pRootSignature = root_.Get();
  pipeline.VS = {shaders::guest_output_triangle_strip_rect_vs, sizeof(shaders::guest_output_triangle_strip_rect_vs)};
  pipeline.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
  pipeline.SampleMask = UINT_MAX;
  pipeline.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
  pipeline.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
  pipeline.RasterizerState.DepthClipEnable = TRUE;
  pipeline.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
  pipeline.NumRenderTargets = 1;
  pipeline.RTVFormats[0] = format;
  pipeline.SampleDesc.Count = 1;
  pipeline.PS = {shaders::guest_output_ffx_fsr_easu_ps, sizeof(shaders::guest_output_ffx_fsr_easu_ps)};
  if (FAILED(device->CreateGraphicsPipelineState(&pipeline, IID_PPV_ARGS(&easu_)))) return false;
  pipeline.PS = {shaders::guest_output_ffx_fsr_rcas_ps, sizeof(shaders::guest_output_ffx_fsr_rcas_ps)};
  if (FAILED(device->CreateGraphicsPipelineState(&pipeline, IID_PPV_ARGS(&rcas_)))) return false;
  for (auto& slot : slots_) {
    if (FAILED(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&slot.allocator))) ||
        FAILED(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, slot.allocator.Get(), nullptr, IID_PPV_ARGS(&slot.commands))) ||
        FAILED(slot.commands->Close())) return false;
    D3D12_DESCRIPTOR_HEAP_DESC heap{D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 2, D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE, 0};
    if (FAILED(device->CreateDescriptorHeap(&heap, IID_PPV_ARGS(&slot.srv)))) return false;
    heap = {D3D12_DESCRIPTOR_HEAP_TYPE_RTV, 2, D3D12_DESCRIPTOR_HEAP_FLAG_NONE, 0};
    if (FAILED(device->CreateDescriptorHeap(&heap, IID_PPV_ARGS(&slot.rtv)))) return false;
  }
  return true;
}
bool SpatialUpscaler::Prepare(uint32_t width, uint32_t height, uint32_t output_width, uint32_t output_height) {
  if (!width || !height || output_width < width || output_height < height ||
      output_width > width * 2 || output_height > height * 2) return false;
  if (failed_) return false;
  if (!ready_) {
    ready_ = Initialize();
    if (!ready_) { failed_ = true; REXLOG_ERROR("PC spatial FSR initialization failed; using original output"); return false; }
  }
  if (source_ && width == width_ && height == height_ && output_width == output_width_ && output_height == output_height_) return true;
  // Resize only once all previous uses of these shared textures have completed.
  if (!Wait(sequence_)) return false;
  Ptr<ID3D12Resource> source, intermediate, sharpened;
  D3D12_HEAP_PROPERTIES heap{};
  heap.Type = D3D12_HEAP_TYPE_DEFAULT;
  D3D12_RESOURCE_DESC texture{};
  texture.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
  texture.Width = width; texture.Height = height;
  texture.DepthOrArraySize = texture.MipLevels = 1;
  texture.Format = format;
  texture.SampleDesc.Count = 1;
  texture.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
  auto* device = provider_.GetDevice();
  if (FAILED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &texture, resting, nullptr, IID_PPV_ARGS(&source)))) return false;
  texture.Width = output_width; texture.Height = output_height;
  texture.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
  if (FAILED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &texture, resting, nullptr, IID_PPV_ARGS(&intermediate)))) return false;
  if (FAILED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &texture, resting, nullptr, IID_PPV_ARGS(&sharpened)))) return false;
  source_ = std::move(source); intermediate_ = std::move(intermediate); sharpened_ = std::move(sharpened);
  width_ = width; height_ = height; output_width_ = output_width; output_height_ = output_height;
  REXLOG_INFO("PC spatial FSR: {}x{} -> {}x{} (EASU + RCAS)", width_, height_, output_width_, output_height_);
  return true;
}
bool SpatialUpscaler::Draw(ID3D12Resource* destination) {
  auto& slot = slots_[sequence_ % slots_.size()];
  if (!Wait(slot.completion) || FAILED(slot.allocator->Reset()) ||
      FAILED(slot.commands->Reset(slot.allocator.Get(), nullptr))) return false;
  if (color_probe_) {
    if (color_probe_->FinishRequested()) {
      // The playable runtime hard-exits after title termination. Drain this
      // diagnostic explicitly while its owner and queue are still running.
      if (!Wait(sequence_)) return false;
      color_probe_->Finish(fence_->GetCompletedValue());
      color_probe_.reset();
    } else {
      color_probe_->Consume(fence_->GetCompletedValue());
    }
  }
  auto* device = provider_.GetDevice();
  auto* list = slot.commands.Get();
  const auto srv_step = device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
  const auto rtv_step = device->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
  auto srv_cpu = slot.srv->GetCPUDescriptorHandleForHeapStart();
  auto srv_gpu = slot.srv->GetGPUDescriptorHandleForHeapStart();
  auto rtv = slot.rtv->GetCPUDescriptorHandleForHeapStart();
  D3D12_SHADER_RESOURCE_VIEW_DESC view{};
  view.Format = format; view.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
  view.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
  view.Texture2D.MipLevels = 1;
  device->CreateShaderResourceView(source_.Get(), &view, srv_cpu);
  srv_cpu.ptr += srv_step;
  device->CreateShaderResourceView(intermediate_.Get(), &view, srv_cpu);
  device->CreateRenderTargetView(intermediate_.Get(), nullptr, rtv);
  auto output_rtv = rtv; output_rtv.ptr += rtv_step;
  // Presenter's output supports UAV, not RTV. Copy from a private RCAS target.
  device->CreateRenderTargetView(sharpened_.Get(), nullptr, output_rtv);
  ID3D12DescriptorHeap* heaps[] = {slot.srv.Get()};
  list->SetDescriptorHeaps(1, heaps);
  list->SetGraphicsRootSignature(root_.Get());
  const float rectangle[] = {-1.f, 1.f, 2.f, -2.f};
  list->SetGraphicsRoot32BitConstants(1, 4, rectangle, 0);
  D3D12_VIEWPORT viewport{0, 0, float(output_width_), float(output_height_), 0, 1};
  D3D12_RECT scissor{0, 0, LONG(output_width_), LONG(output_height_)};
  list->RSSetViewports(1, &viewport); list->RSSetScissorRects(1, &scissor);
  list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
  Transition(list, intermediate_.Get(), resting, D3D12_RESOURCE_STATE_RENDER_TARGET);
  list->OMSetRenderTargets(1, &rtv, FALSE, nullptr);
  list->SetPipelineState(easu_.Get());
  list->SetGraphicsRootDescriptorTable(0, srv_gpu);
  const float easu[] = {float(width_) / output_width_, float(height_) / output_height_, 1.f / width_, 1.f / height_};
  list->SetGraphicsRoot32BitConstants(2, 4, easu, 0);
  list->DrawInstanced(4, 1, 0, 0);
  Transition(list, intermediate_.Get(), D3D12_RESOURCE_STATE_RENDER_TARGET, resting);
  Transition(list, sharpened_.Get(), resting, D3D12_RESOURCE_STATE_RENDER_TARGET);
  list->OMSetRenderTargets(1, &output_rtv, FALSE, nullptr);
  list->SetPipelineState(rcas_.Get());
  srv_gpu.ptr += srv_step;
  list->SetGraphicsRootDescriptorTable(0, srv_gpu);
  struct { int32_t x, y; float sharpness; } rcas{0, 0, std::exp2f(-.5f)};
  list->SetGraphicsRoot32BitConstants(2, 3, &rcas, 0);
  list->DrawInstanced(4, 1, 0, 0);
  Transition(list, sharpened_.Get(), D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
  Transition(list, destination, resting, D3D12_RESOURCE_STATE_COPY_DEST);
  list->CopyResource(destination, sharpened_.Get());
  if (color_probe_) color_probe_->Record(device, list, sharpened_.Get(), sequence_ + 1);
  Transition(list, destination, D3D12_RESOURCE_STATE_COPY_DEST, resting);
  Transition(list, sharpened_.Get(), D3D12_RESOURCE_STATE_COPY_SOURCE, resting);
  if (FAILED(list->Close())) return false;
  ID3D12CommandList* lists[] = {list};
  provider_.GetDirectQueue()->ExecuteCommandLists(1, lists);
  slot.completion = ++sequence_;
  return SUCCEEDED(provider_.GetDirectQueue()->Signal(fence_.Get(), sequence_));
}
}
