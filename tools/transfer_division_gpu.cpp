// Exhaustive hardware check of the bounded transfer-address arithmetic.
// This does not establish native shader integration, image parity or speed.
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

using Microsoft::WRL::ComPtr;
namespace {
void check(HRESULT hr, const char* operation) {
  if (FAILED(hr)) throw std::runtime_error(std::string(operation) + " HRESULT=" + std::to_string(uint32_t(hr)));
}
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void write(const std::filesystem::path& path, const void* data, size_t size) {
  std::ofstream file(path, std::ios::binary);
  file.write(static_cast<const char*>(data), size);
  require(bool(file), "Artifact write failed");
}
}

int main(int argc, char** argv) {
  try {
    require(argc == 3, "Usage: aot_transfer_division_gpu HLSL_FILE NEW_OUTPUT_DIRECTORY");
    const std::filesystem::path output(argv[2]);
    require(!std::filesystem::exists(output), "Output directory already exists");
    std::filesystem::create_directories(output);
    std::ifstream source_file(argv[1]);
    require(bool(source_file), "Cannot read candidate function");
    std::string source((std::istreambuf_iterator<char>(source_file)), std::istreambuf_iterator<char>());
    source += R"(
RWStructuredBuffer<uint4> results : register(u0);
[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID) {
  uint n = tid.x & 2047;
  uint d = (tid.x >> 11) + 1;
  if (d > 1023) return;
  uint2 candidate = AotDivideTile(n, d);
  results[tid.x] = uint4(candidate, n / d, n % d);
}
)";
    write(output / "test.hlsl", source.data(), source.size());
    ComPtr<ID3DBlob> code, error;
    HRESULT compiled = D3DCompile(source.data(), source.size(), argv[1], nullptr, nullptr,
        "main", "cs_5_0", D3DCOMPILE_OPTIMIZATION_LEVEL3 | D3DCOMPILE_IEEE_STRICTNESS, 0, &code, &error);
    if (error) write(output / "compiler.txt", error->GetBufferPointer(), error->GetBufferSize());
    check(compiled, "Compile exhaustive shader");
    write(output / "test.dxbc", code->GetBufferPointer(), code->GetBufferSize());
    ComPtr<ID3DBlob> assembly;
    check(D3DDisassemble(code->GetBufferPointer(), code->GetBufferSize(), 0, nullptr, &assembly), "Disassemble");
    write(output / "test.asm", assembly->GetBufferPointer(), assembly->GetBufferSize());
    ComPtr<IDXGIFactory6> factory;
    check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "Create factory");
    ComPtr<IDXGIAdapter1> adapter;
    check(factory->EnumAdapterByGpuPreference(0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
          IID_PPV_ARGS(&adapter)), "Select hardware adapter");
    DXGI_ADAPTER_DESC1 description{};
    check(adapter->GetDesc1(&description), "Adapter description");
    require(!(description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE), "Require hardware GPU");
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL requested = D3D_FEATURE_LEVEL_11_0, actual{};
    check(D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, &requested, 1,
          D3D11_SDK_VERSION, &device, &actual, &context), "Create D3D11 device");
    require(actual >= requested, "Insufficient feature level");
    ComPtr<ID3D11ComputeShader> shader;
    check(device->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &shader), "Create shader");
    constexpr uint32_t count = 2048 * 1023, bytes = count * 4 * sizeof(uint32_t);
    D3D11_BUFFER_DESC buffer_desc{};
    buffer_desc.ByteWidth = bytes;
    buffer_desc.Usage = D3D11_USAGE_DEFAULT;
    buffer_desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    buffer_desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    buffer_desc.StructureByteStride = 4 * sizeof(uint32_t);
    ComPtr<ID3D11Buffer> buffer, staging;
    check(device->CreateBuffer(&buffer_desc, nullptr, &buffer), "Create output buffer");
    D3D11_UNORDERED_ACCESS_VIEW_DESC uav_desc{};
    uav_desc.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    uav_desc.Buffer.NumElements = count;
    ComPtr<ID3D11UnorderedAccessView> uav;
    check(device->CreateUnorderedAccessView(buffer.Get(), &uav_desc, &uav), "Create output UAV");
    buffer_desc.Usage = D3D11_USAGE_STAGING;
    buffer_desc.BindFlags = 0;
    buffer_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    buffer_desc.MiscFlags = 0;
    check(device->CreateBuffer(&buffer_desc, nullptr, &staging), "Create readback");
    const UINT sentinel[4] = {0xdeadbeef, 0xdeadbeef, 0xdeadbeef, 0xdeadbeef};
    context->ClearUnorderedAccessViewUint(uav.Get(), sentinel);
    context->CSSetUnorderedAccessViews(0, 1, uav.GetAddressOf(), nullptr);
    context->CSSetShader(shader.Get(), nullptr, 0);
    context->Dispatch((count + 63) / 64, 1, 1);
    ID3D11UnorderedAccessView* null_uav = nullptr;
    context->CSSetUnorderedAccessViews(0, 1, &null_uav, nullptr);
    context->CopyResource(staging.Get(), buffer.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped), "Read GPU output");
    const auto* values = static_cast<const uint32_t*>(mapped.pData);
    uint64_t candidate_failures = 0, baseline_failures = 0;
    for (uint32_t i = 0; i < count; ++i) {
      const uint32_t n = i & 2047, d = (i >> 11) + 1;
      const uint32_t q = n / d, r = n % d;
      if (values[i*4] != q || values[i*4+1] != r) ++candidate_failures;
      if (values[i*4+2] != q || values[i*4+3] != r) ++baseline_failures;
    }
    write(output / "results.bin", values, bytes);
    context->Unmap(staging.Get(), 0);
    check(device->GetDeviceRemovedReason(), "Device status");
    std::ofstream report(output / "summary.json");
    report << "{\"complete\":true,\"pairs\":" << count
           << ",\"candidate_failures\":" << candidate_failures
           << ",\"baseline_failures\":" << baseline_failures
           << ",\"vendor_id\":" << description.VendorId << ",\"device_id\":" << description.DeviceId
           << ",\"limits\":\"D3D11 compute arithmetic on one hardware adapter; no native integration, pixel parity or timing claim\"}\n";
    require(bool(report), "Report write failed");
    require(!candidate_failures && !baseline_failures, "GPU arithmetic differs from CPU integer division");
    std::cout << count << " numerator/divisor pairs passed for candidate and baseline on hardware GPU.\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
