// Native GPU checks: constant-color preservation (including borders), resize,
// descriptor/allocator reuse, and D3D12 validation messages.
#include "src/spatial_upscaler.h"
#include <rex/cvar.h>
#include <d3d12sdklayers.h>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <stdexcept>
#include <filesystem>
#include <fstream>
#include <sstream>

template<class T> using Ptr = Microsoft::WRL::ComPtr<T>;
void Check(HRESULT result) { if (FAILED(result)) throw std::runtime_error("D3D12 operation failed"); }
void Barrier(ID3D12GraphicsCommandList* list, ID3D12Resource* resource, D3D12_RESOURCE_STATES from, D3D12_RESOURCE_STATES to) {
  D3D12_RESOURCE_BARRIER b{};
  b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
  b.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, from, to};
  list->ResourceBarrier(1, &b);
}
int main(int argc, char** argv) try {
  const bool color_probe = argc == 2 && std::string(argv[1]) == "--color-probe";
  if (argc != 1 && !color_probe) throw std::runtime_error("Unknown test mode");
  const auto probe_path = std::filesystem::temp_directory_path() /
      ("aot-frame-colors-" + std::to_string(GetCurrentProcessId()) + "-" + std::to_string(GetTickCount64()));
  if (_putenv_s("AOT_FRAME_COLOR_PROBE", color_probe ? probe_path.string().c_str() : ""))
    throw std::runtime_error("Cannot set diagnostic environment");
  rex::cvar::SetFlagByName("d3d12_debug", "true");
  auto provider = rex::ui::d3d12::D3D12Provider::Create();
  if (!provider) throw std::runtime_error("No D3D12 provider");
  auto* device = provider->GetDevice();
  Ptr<ID3D12InfoQueue> validation;
  const bool debug = SUCCEEDED(device->QueryInterface(IID_PPV_ARGS(&validation)));
  if (debug) validation->ClearStoredMessages();
  Ptr<ID3D12Fence> fence;
  Check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)));
  HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  if (!event) throw std::runtime_error("No fence event");
  uint64_t serial = 0;
  auto wait = [&] {
    Check(provider->GetDirectQueue()->Signal(fence.Get(), ++serial));
    Check(fence->SetEventOnCompletion(serial, event));
    if (WaitForSingleObject(event, 10000) != WAIT_OBJECT_0) throw std::runtime_error("GPU timeout");
  };
  auto resource = [&](D3D12_RESOURCE_DESC desc, D3D12_HEAP_TYPE type, D3D12_RESOURCE_STATES state) {
    D3D12_HEAP_PROPERTIES heap{}; heap.Type = type;
    Ptr<ID3D12Resource> result;
    Check(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc, state, nullptr, IID_PPV_ARGS(&result)));
    return result;
  };
  auto buffer = [&](UINT64 size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES state) {
    D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width = size; desc.Height = desc.DepthOrArraySize = desc.MipLevels = desc.SampleDesc.Count = 1;
    desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    return resource(desc, heap, state);
  };
  Ptr<ID3D12CommandAllocator> allocator;
  Ptr<ID3D12GraphicsCommandList> list;
  Check(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)));
  Check(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator.Get(), nullptr, IID_PPV_ARGS(&list)));
  Check(list->Close());
  auto start = [&] { Check(allocator->Reset()); Check(list->Reset(allocator.Get(), nullptr)); };
  auto submit = [&] { Check(list->Close()); ID3D12CommandList* lists[]{list.Get()}; provider->GetDirectQueue()->ExecuteCommandLists(1, lists); };
  auto upscaler = std::make_unique<aot::SpatialUpscaler>(*provider);
  for (unsigned iteration = 0; iteration < 12; ++iteration) {
    const unsigned width = iteration % 2 ? 128 : 96, height = iteration % 2 ? 72 : 64;
    const unsigned ow = width * 3 / 2, oh = height * 3 / 2;
    if (!upscaler->Prepare(width, height, ow, oh)) throw std::runtime_error("FSR preparation failed");
    auto src_desc = upscaler->source()->GetDesc();
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT src_layout{};
    UINT64 src_bytes = 0;
    device->GetCopyableFootprints(&src_desc, 0, 1, 0, &src_layout, nullptr, nullptr, &src_bytes);
    auto upload = buffer(src_bytes, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
    unsigned char* mapped;
    D3D12_RANGE empty{};
    Check(upload->Map(0, &empty, reinterpret_cast<void**>(&mapped)));
    const uint32_t channels[] = {200u + iteration * 7, 500, 800u - iteration * 5};
    const uint32_t color = channels[0] | (channels[1] << 10) | (channels[2] << 20) | 0xc0000000u;
    for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x)
      std::memcpy(mapped + src_layout.Offset + y * src_layout.Footprint.RowPitch + x * 4, &color, 4);
    upload->Unmap(0, nullptr);
    start();
    Barrier(list.Get(), upscaler->source(), D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
    D3D12_TEXTURE_COPY_LOCATION dst{upscaler->source(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
    D3D12_TEXTURE_COPY_LOCATION src{upload.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT}; src.PlacedFootprint = src_layout;
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    Barrier(list.Get(), upscaler->source(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    submit();
    auto output_desc = src_desc; output_desc.Width = ow; output_desc.Height = oh;
    auto output = resource(output_desc, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    // Wrap the three command slots repeatedly without waiting on the CPU.
    for (unsigned frame = 0; frame < 8; ++frame) {
      if (color_probe && (frame == 3 || frame == 4)) {
        // Inject exactly one pink output, then restore the ordinary image.
        // Wait before rewriting the upload buffer, not inside the probe.
        wait();
        Check(upload->Map(0, &empty, reinterpret_cast<void**>(&mapped)));
        const uint32_t injected = frame == 3 ? (900u | (64u << 10) | (850u << 20) | 0xc0000000u) : color;
        for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x)
          std::memcpy(mapped + src_layout.Offset + y * src_layout.Footprint.RowPitch + x * 4, &injected, 4);
        upload->Unmap(0, nullptr);
        start();
        Barrier(list.Get(), upscaler->source(), D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
        D3D12_TEXTURE_COPY_LOCATION injection_dst{upscaler->source(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
        D3D12_TEXTURE_COPY_LOCATION injection_src{upload.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT};
        injection_src.PlacedFootprint = src_layout;
        list->CopyTextureRegion(&injection_dst, 0, 0, 0, &injection_src, nullptr);
        Barrier(list.Get(), upscaler->source(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        submit();
      }
      if (!upscaler->Draw(output.Get())) throw std::runtime_error("FSR draw failed");
    }
    wait();
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT layout{}; UINT64 bytes = 0;
    device->GetCopyableFootprints(&output_desc, 0, 1, 0, &layout, nullptr, nullptr, &bytes);
    auto readback = buffer(bytes, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    start();
    Barrier(list.Get(), output.Get(), D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_SOURCE);
    src = {output.Get(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
    dst = {readback.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT}; dst.PlacedFootprint = layout;
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    submit(); wait();
    Check(readback->Map(0, nullptr, reinterpret_cast<void**>(&mapped)));
    unsigned mismatches = 0;
    for (unsigned y = 0; y < oh; ++y) for (unsigned x = 0; x < ow; ++x) {
      uint32_t pixel; std::memcpy(&pixel, mapped + layout.Offset + y * layout.Footprint.RowPitch + x * 4, 4);
      for (unsigned c = 0; c < 3; ++c)
        if (std::abs(int((pixel >> (c * 10)) & 1023) - int(channels[c])) > 3) ++mismatches;
    }
    readback->Unmap(0, &empty);
    if (mismatches) { std::fprintf(stderr, "FSR constant image: %u mismatched channels at iteration %u\n", mismatches, iteration); return 1; }
    if (color_probe && iteration == 11) {
      // Exercise explicit completion while the renderer remains alive, as the
      // playable runtime does not invoke destructors at its hard-exit boundary.
      std::ofstream trigger(probe_path / "finish.trigger"); trigger << "finish\n"; trigger.close();
      start();
      Barrier(list.Get(), output.Get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
      submit();
      if (!upscaler->Draw(output.Get()) || !std::filesystem::is_regular_file(probe_path / "summary.json"))
        throw std::runtime_error("Explicit frame-color drain did not finish");
      wait(); // Keep the extra draw's destination alive through queue completion.
    }
  }
  wait();
  upscaler.reset(); // Drain the final three diagnostic readbacks before inspecting coverage.
  if (color_probe) {
    std::ifstream frames(probe_path / "frames.csv");
    std::string line; std::getline(frames, line);
    unsigned count = 0, pink_frames = 0;
    while (std::getline(frames, line)) {
      std::istringstream row(line); std::vector<std::string> cells;
      for (std::string cell; std::getline(row, cell, ',');) cells.push_back(cell);
      if (cells.size() < 7 || std::stoul(cells[0]) != count + 1) throw std::runtime_error("Diagnostic frame coverage gap");
      const auto iteration = count / 8;
      const unsigned width = (iteration % 2 ? 128 : 96) * 3 / 2;
      const unsigned height = (iteration % 2 ? 72 : 64) * 3 / 2;
      const bool pink = count % 8 == 3;
      if (std::stoul(cells[2]) != width || std::stoul(cells[3]) != height ||
          std::stoul(cells[4]) != (pink ? width * height : 0) ||
          std::stoul(cells[5]) != (pink ? width : 0) || std::stoul(cells[6]) != (pink ? height : 0))
        throw std::runtime_error("Diagnostic color/row-layout mismatch");
      if (pink) {
        if (cells.size() != 8 || !std::filesystem::is_regular_file(probe_path / cells[7]))
          throw std::runtime_error("Missing wide-color image");
        ++pink_frames;
      }
      ++count;
    }
    std::ifstream summary(probe_path / "summary.json");
    const std::string result{std::istreambuf_iterator<char>(summary), {}};
    if (count != 96 || pink_frames != 12 || result.find("\"complete\":true") == std::string::npos ||
        result.find("\"processed\":96") == std::string::npos)
      throw std::runtime_error("Incomplete frame color probe");
    std::printf("Frame colors: 96 contiguous GPU outputs, 12 single-frame positives and 84 negatives; %s\n", probe_path.string().c_str());
  }
  CloseHandle(event);
  unsigned errors = 0;
  if (debug) for (UINT64 i = 0; i < validation->GetNumStoredMessagesAllowedByRetrievalFilter(); ++i) {
    SIZE_T size = 0; validation->GetMessage(i, nullptr, &size);
    std::vector<unsigned char> data(size);
    auto* message = reinterpret_cast<D3D12_MESSAGE*>(data.data());
    Check(validation->GetMessage(i, message, &size));
    if (message->Severity <= D3D12_MESSAGE_SEVERITY_ERROR) {
      std::fprintf(stderr, "%s\n", message->pDescription); ++errors;
    }
  }
  std::printf("FSR: 12 resizes, %u draws, constant RGB and borders checked; D3D12 validation %s, %u errors\n", color_probe ? 97u : 96u, debug ? "enabled" : "unavailable", errors);
  return errors ? 1 : 0;
} catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); return 1; }
