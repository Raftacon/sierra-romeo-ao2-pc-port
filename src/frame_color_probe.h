#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <d3d12.h>
#include <wrl/client.h>
#include <rex/logging.h>

namespace aot {
// Diagnostic only: inspect each FSR guest-output refresh, not sampled desktop
// frames. The owner supplies its actual submission fence and COPY_SOURCE state.
// It must finish after waiting for all submissions, before releasing this object.
class FrameColorProbe {
 public:
  static std::unique_ptr<FrameColorProbe> FromEnvironment() {
    const char* value = std::getenv("AOT_FRAME_COLOR_PROBE");
    if (!value || !*value) return {};
    try {
      return std::unique_ptr<FrameColorProbe>(new FrameColorProbe(value));
    } catch (const std::exception& error) {
      REXLOG_ERROR("Frame color probe initialization failed: {}", error.what());
      return {};
    }
  }

  bool FinishRequested() {
    std::error_code error;
    const bool exists = std::filesystem::exists(directory_ / "finish.trigger", error);
    if (error) Fail("cannot inspect completion trigger");
    return failed_ || exists;
  }

  void Consume(uint64_t completed) {
    if (failed_) return;
    if (completed == UINT64_MAX) { Fail("device removed"); return; }
    // Sort by submitted sequence so final drain and resolution changes preserve
    // an unambiguous contiguous timeline, even when all three slots completed.
    std::array<Slot*, 3> ordered{&slots_[0], &slots_[1], &slots_[2]};
    std::sort(ordered.begin(), ordered.end(), [](auto* a, auto* b) { return a->sequence < b->sequence; });
    for (auto* slot : ordered) {
      if (!slot->sequence || slot->sequence > completed) continue;
      if (slot->sequence != processed_ + 1) { Fail("noncontiguous completed output"); return; }
      uint8_t* data = nullptr;
      D3D12_RANGE range{0, static_cast<SIZE_T>(slot->bytes)};
      if (FAILED(slot->readback->Map(0, &range, reinterpret_cast<void**>(&data)))) {
        Fail("readback map failed"); return;
      }
      try { Scan(*slot, data); }
      catch (const std::exception& error) { Fail(error.what()); }
      D3D12_RANGE empty{};
      slot->readback->Unmap(0, &empty);
      if (failed_) return;
      ++processed_; slot->sequence = 0;
    }
  }

  void Record(ID3D12Device* device, ID3D12GraphicsCommandList* commands,
              ID3D12Resource* source, uint64_t sequence) {
    if (failed_) return;
    if (sequence != requested_ + 1 || sequence > 60000) { Fail("output sequence or frame limit"); return; }
    auto& slot = slots_[(sequence - 1) % slots_.size()];
    if (slot.sequence) { Fail("attempt to reuse pending readback"); return; }
    const auto desc = source->GetDesc();
    if (desc.Dimension != D3D12_RESOURCE_DIMENSION_TEXTURE2D ||
        desc.Format != DXGI_FORMAT_R10G10B10A2_UNORM || desc.SampleDesc.Count != 1 ||
        desc.DepthOrArraySize != 1 || desc.MipLevels != 1 || !desc.Width || !desc.Height ||
        desc.Width > 8192 || desc.Height > 8192) { Fail("unsupported output layout"); return; }
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT layout{}; UINT64 bytes = 0;
    device->GetCopyableFootprints(&desc, 0, 1, 0, &layout, nullptr, nullptr, &bytes);
    if (!slot.readback || slot.bytes != bytes) {
      D3D12_RESOURCE_DESC buffer{};
      buffer.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
      buffer.Width = bytes; buffer.Height = buffer.DepthOrArraySize = buffer.MipLevels = buffer.SampleDesc.Count = 1;
      buffer.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
      D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_READBACK;
      Microsoft::WRL::ComPtr<ID3D12Resource> readback;
      if (FAILED(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &buffer,
          D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&readback)))) {
        Fail("readback allocation failed"); return;
      }
      slot.readback = std::move(readback); slot.bytes = bytes;
    }
    slot.layout = layout; slot.sequence = sequence;
    slot.microseconds = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started_).count();
    D3D12_TEXTURE_COPY_LOCATION destination{slot.readback.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT};
    destination.PlacedFootprint = layout;
    D3D12_TEXTURE_COPY_LOCATION origin{source, D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
    commands->CopyTextureRegion(&destination, 0, 0, 0, &origin, nullptr);
    ++requested_;
  }

  void Finish(uint64_t completed) {
    Consume(completed);
    frames_.flush();
    const bool complete = !failed_ && bool(frames_) && requested_ && processed_ == requested_;
    const auto temporary = directory_ / "summary.json.tmp";
    std::ofstream summary(temporary);
    summary << "{\"complete\":" << (complete ? "true" : "false")
            << ",\"requested\":" << requested_ << ",\"processed\":" << processed_
            << ",\"wide_frames\":" << wide_frames_ << ",\"images_saved\":" << images_
            << ",\"steady_clock_start_us\":" << std::chrono::duration_cast<std::chrono::microseconds>(started_.time_since_epoch()).count()
            << ",\"scope\":\"FSR guest-output refreshes only; diagnostic readback changes performance; color heuristic, not parity\"}\n";
    summary.flush();
    const bool written = bool(summary);
    summary.close();
    std::error_code error;
    if (written) std::filesystem::rename(temporary, directory_ / "summary.json", error);
    REXLOG_INFO("Frame color probe: complete={}, outputs={}/{}, wide_frames={}, images={}",
                complete && written && !error, processed_, requested_, wide_frames_, images_);
  }

 private:
  struct Slot {
    Microsoft::WRL::ComPtr<ID3D12Resource> readback;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT layout{};
    uint64_t bytes = 0, sequence = 0, microseconds = 0;
  };
  explicit FrameColorProbe(const char* path) : directory_(path) {
    if (!std::filesystem::create_directory(directory_)) throw std::runtime_error("require new diagnostic directory");
    frames_.open(directory_ / "frames.csv");
    frames_ << "sequence,microseconds,width,height,magenta_pixels,max_row_pixels,wide_rows,image\n";
    if (!frames_) throw std::runtime_error("cannot create frame timeline");
    REXLOG_INFO("Frame color probe enabled: {}", path);
  }
  void Fail(const char* message) {
    failed_ = true;
    REXLOG_ERROR("Frame color probe incomplete: {}", message);
  }
  static uint8_t Channel(uint32_t pixel, unsigned shift) {
    return static_cast<uint8_t>((((pixel >> shift) & 1023) * 255 + 511) / 1023);
  }
  void Scan(const Slot& slot, const uint8_t* data) {
    const auto width = slot.layout.Footprint.Width, height = slot.layout.Footprint.Height;
    const auto* pixels = data + slot.layout.Offset;
    uint64_t magenta = 0; uint32_t maximum_row = 0, wide_rows = 0;
    // Include the entire image: the source has no desktop title bar to exclude.
    for (uint32_t y = 0; y < height; ++y) {
      uint32_t row = 0;
      for (uint32_t x = 0; x < width; ++x) {
        uint32_t pixel; std::memcpy(&pixel, pixels + y * slot.layout.Footprint.RowPitch + x * 4, 4);
        const int red = Channel(pixel, 0), green = Channel(pixel, 10), blue = Channel(pixel, 20);
        row += red > 175 && blue > 140 && green < 140 && red - green > 60;
      }
      magenta += row; maximum_row = std::max(maximum_row, row);
      wide_rows += uint64_t(row) * 10 >= uint64_t(width) * 3;
    }
    std::string image;
    if (wide_rows >= 5) {
      ++wide_frames_;
      // Bound disk usage while retaining metrics for every output refresh.
      if (images_ < 32) {
        image = "wide-" + std::to_string(slot.sequence) + ".ppm";
        std::ofstream file(directory_ / image, std::ios::binary);
        file << "P6\n" << width << ' ' << height << "\n255\n";
        std::vector<uint8_t> row(width * 3);
        for (uint32_t y = 0; y < height; ++y) {
          for (uint32_t x = 0; x < width; ++x) {
            uint32_t pixel; std::memcpy(&pixel, pixels + y * slot.layout.Footprint.RowPitch + x * 4, 4);
            for (unsigned c = 0; c < 3; ++c) row[x * 3 + c] = Channel(pixel, c * 10);
          }
          file.write(reinterpret_cast<const char*>(row.data()), row.size());
        }
        if (!file) throw std::runtime_error("candidate image write failed");
        ++images_;
      }
    }
    frames_ << slot.sequence << ',' << slot.microseconds << ',' << width << ',' << height << ','
            << magenta << ',' << maximum_row << ',' << wide_rows << ',' << image << '\n';
    if (wide_rows >= 5 || slot.sequence % 128 == 0) frames_.flush();
    if (!frames_) throw std::runtime_error("frame timeline write failed");
  }
  std::filesystem::path directory_;
  std::ofstream frames_;
  std::chrono::steady_clock::time_point started_ = std::chrono::steady_clock::now();
  std::array<Slot, 3> slots_;
  uint64_t requested_ = 0, processed_ = 0, wide_frames_ = 0, images_ = 0;
  bool failed_ = false;
};
}
