// Compare actual projection arithmetic on the hardware adapter, not WARP.
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

using Microsoft::WRL::ComPtr;
namespace {
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
void check(HRESULT hr, const char* message) {
  if (FAILED(hr)) throw std::runtime_error(std::string(message) + " HRESULT=" + std::to_string(uint32_t(hr)));
}
void write(const std::filesystem::path& path, const void* data, size_t size) {
  std::ofstream stream(path, std::ios::binary);
  stream.write(static_cast<const char*>(data), size);
  require(bool(stream), "Artifact write failed");
}
}
int main(int argc, char** argv) {
  try {
    require(argc == 3, "Usage: aot_projection_fma_gpu TEST_HLSL NEW_OUTPUT_DIRECTORY");
    const std::filesystem::path output(argv[2]);
    require(!std::filesystem::exists(output), "Output directory exists");
    std::filesystem::create_directories(output);
    std::ifstream input(argv[1]);
    require(bool(input), "Cannot read test shader");
    const std::string source((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    write(output / "test.hlsl", source.data(), source.size());
    ComPtr<ID3DBlob> code, error, assembly;
    HRESULT compiled = D3DCompile(source.data(), source.size(), argv[1], nullptr, nullptr,
        "main", "cs_5_0", D3DCOMPILE_OPTIMIZATION_LEVEL3 | D3DCOMPILE_IEEE_STRICTNESS, 0, &code, &error);
    if (error) write(output / "compiler.txt", error->GetBufferPointer(), error->GetBufferSize());
    check(compiled, "Compile comparison shader");
    write(output / "test.dxbc", code->GetBufferPointer(), code->GetBufferSize());
    check(D3DDisassemble(code->GetBufferPointer(), code->GetBufferSize(), 0, nullptr, &assembly), "Disassemble");
    write(output / "test.asm", assembly->GetBufferPointer(), assembly->GetBufferSize());
    const std::string text(static_cast<const char*>(assembly->GetBufferPointer()), assembly->GetBufferSize());
    require(text.find("dfma") != std::string::npos && text.find("dmul") != std::string::npos &&
            text.find("dadd") != std::string::npos, "Missing comparison arithmetic in compiled shader");
    ComPtr<IDXGIFactory6> factory;
    check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "Create factory");
    ComPtr<IDXGIAdapter1> adapter;
    check(factory->EnumAdapterByGpuPreference(0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
          IID_PPV_ARGS(&adapter)), "Select adapter");
    DXGI_ADAPTER_DESC1 description{};
    check(adapter->GetDesc1(&description), "Adapter description");
    require(!(description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE), "Require hardware GPU");
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    const D3D_FEATURE_LEVEL requested = D3D_FEATURE_LEVEL_11_0;
    D3D_FEATURE_LEVEL actual{};
    check(D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, &requested, 1,
          D3D11_SDK_VERSION, &device, &actual, &context), "Create D3D11 device");
    D3D11_FEATURE_DATA_D3D11_OPTIONS options{};
    check(device->CheckFeatureSupport(D3D11_FEATURE_D3D11_OPTIONS, &options, sizeof(options)), "Query extended doubles");
    require(options.ExtendedDoublesShaderInstructions, "Adapter lacks extended double operations");
    ComPtr<ID3D11ComputeShader> shader;
    check(device->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &shader), "Create shader");
    constexpr uint32_t count = 1u << 20, bytes = count * 4 * sizeof(uint32_t);
    D3D11_BUFFER_DESC desc{};
    desc.ByteWidth = bytes; desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED; desc.StructureByteStride = 16;
    ComPtr<ID3D11Buffer> buffer, staging;
    check(device->CreateBuffer(&desc, nullptr, &buffer), "Create result buffer");
    D3D11_UNORDERED_ACCESS_VIEW_DESC view{};
    view.ViewDimension = D3D11_UAV_DIMENSION_BUFFER; view.Buffer.NumElements = count;
    ComPtr<ID3D11UnorderedAccessView> uav;
    check(device->CreateUnorderedAccessView(buffer.Get(), &view, &uav), "Create result view");
    desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ; desc.MiscFlags = 0;
    check(device->CreateBuffer(&desc, nullptr, &staging), "Create readback");
    const UINT sentinel[4] = {0xdeadbeef, 0xdeadbeef, 0xdeadbeef, 0xdeadbeef};
    context->ClearUnorderedAccessViewUint(uav.Get(), sentinel);
    context->CSSetUnorderedAccessViews(0, 1, uav.GetAddressOf(), nullptr);
    context->CSSetShader(shader.Get(), nullptr, 0);
    context->Dispatch(count / 64, 1, 1);
    ID3D11UnorderedAccessView* empty = nullptr;
    context->CSSetUnorderedAccessViews(0, 1, &empty, nullptr);
    context->CopyResource(staging.Get(), buffer.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped), "Read results");
    const auto* values = static_cast<const uint32_t*>(mapped.pData);
    std::array<uint32_t, 8> raw_world{}, raw_output{}, semantic{}, missing{};
    uint64_t failures = 0;
    for (uint32_t i = 0; i < count; ++i) {
      const auto group = i >> 17;
      missing[group] += values[i*4] != i;
      raw_world[group] += values[i*4+1] != 0;
      raw_output[group] += values[i*4+2] != 0;
      semantic[group] += values[i*4+3] != 0;
      failures += values[i*4] != i || (group == 7 ? values[i*4+3] != 0 :
                  (values[i*4+1] != 0 || values[i*4+2] != 0 || values[i*4+3] != 0));
    }
    write(output / "results.bin", values, bytes);
    context->Unmap(staging.Get(), 0);
    check(device->GetDeviceRemovedReason(), "Device status");
    std::ofstream report(output / "summary.json");
    report << "{\"complete\":true,\"cases\":" << count << ",\"failures\":" << failures
           << ",\"vendor_id\":" << description.VendorId << ",\"device_id\":" << description.DeviceId
           << ",\"extended_doubles\":true,\"scenarios\":[";
    for (unsigned i = 0; i < 8; ++i) {
      if (i) report << ',';
      report << "{\"scenario\":" << i << ",\"cases\":131072,\"missing\":" << missing[i]
             << ",\"raw_world_differences\":" << raw_world[i] << ",\"raw_output_differences\":" << raw_output[i]
             << ",\"semantic_differences\":" << semantic[i] << '}';
    }
    report << "],\"limits\":\"D3D11 compute comparison on one adapter. Nonfinite scenario accepts different NaN payloads only. No full shader, raster parity or speed claim.\"}\n";
    report.close(); require(bool(report), "Report write failed");
    std::cout << count << " cases; failures=" << failures << '\n';
    require(failures == 0, "Projection arithmetic mismatch");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
