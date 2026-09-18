// Controlled GPU execution of locally translated terrain/instance programs.
// This checks shader output preservation, not native scene or raster fidelity.
#include <windows.h>
#include <d3d12.h>
#include <d3dcompiler.h>
#include <d3d12shader.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;
namespace {
constexpr unsigned vertices = 128, lanes = 24, stride = lanes * sizeof(float);
using F4 = std::array<float, 4>;
void require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}
void check(HRESULT hr, const char* message) {
  require(SUCCEEDED(hr), std::string(message) + " HRESULT=" + std::to_string(uint32_t(hr)));
}
std::vector<uint8_t> read(const std::filesystem::path& path) {
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  require(bool(file), "Cannot read " + path.string());
  const auto size = file.tellg();
  require(size > 0 && size < 1024 * 1024, "Unexpected shader size");
  std::vector<uint8_t> bytes(static_cast<size_t>(size));
  file.seekg(0); file.read(reinterpret_cast<char*>(bytes.data()), size);
  require(bool(file), "Incomplete shader read");
  return bytes;
}
void write(const std::filesystem::path& path, const void* bytes, size_t size) {
  std::ofstream file(path, std::ios::binary);
  file.write(static_cast<const char*>(bytes), size);
  require(bool(file), "Cannot write " + path.string());
}
struct Program {
  std::vector<uint8_t> bytes;
  ComPtr<ID3D12PipelineState> pipeline;
  bool instanced = false;
  explicit Program(const std::filesystem::path& path) : bytes(read(path)) {
    ComPtr<ID3D12ShaderReflection> reflection;
    check(D3DReflect(bytes.data(), bytes.size(), IID_PPV_ARGS(&reflection)), "Reflect shader");
    D3D12_SHADER_DESC desc{}; check(reflection->GetDesc(&desc), "Shader description");
    require(D3D12_SHVER_GET_TYPE(desc.Version) == D3D12_SHVER_VERTEX_SHADER &&
            desc.InputParameters == 1 && desc.OutputParameters == 6 &&
            desc.BoundResources == 5, "Unexpected terrain shader interface");
    D3D12_SIGNATURE_PARAMETER_DESC input{};
    check(reflection->GetInputParameterDesc(0, &input), "Input signature");
    require(input.SystemValueType == D3D_NAME_VERTEX_ID && input.Mask == 1 &&
            input.ComponentType == D3D_REGISTER_COMPONENT_UINT32, "Expected vertex ID input");
    for (unsigned i = 0; i < 6; ++i) {
      D3D12_SIGNATURE_PARAMETER_DESC output{};
      check(reflection->GetOutputParameterDesc(i, &output), "Output signature");
      require(output.Register == i && output.Mask == 15 && output.Stream == 0 &&
              output.ComponentType == D3D_REGISTER_COMPONENT_FLOAT32 &&
              std::string(output.SemanticName) == (i == 5 ? "SV_Position" : "TEXCOORD") &&
              output.SemanticIndex == (i == 5 ? 0 : i), "Unexpected output layout");
    }
    struct Binding { const char* name; D3D_SHADER_INPUT_TYPE type; unsigned slot, size; };
    const Binding bindings[] = {
      {"xe_shared_memory_srv", D3D_SIT_BYTEADDRESS, 0, 0},
      {"xe_shared_memory_uav", D3D_SIT_UAV_RWBYTEADDRESS, 0, 0},
      {"xe_system_cbuffer", D3D_SIT_CBUFFER, 0, 464},
      {"xe_fetch_cbuffer", D3D_SIT_CBUFFER, 3, 768},
      {"xe_float_cbuffer", D3D_SIT_CBUFFER, 1, 464}};
    for (const auto& expected : bindings) {
      D3D12_SHADER_INPUT_BIND_DESC binding{};
      check(reflection->GetResourceBindingDescByName(expected.name, &binding), "Resource binding");
      require(binding.Type == expected.type && binding.BindPoint == expected.slot &&
              binding.BindCount == 1 && binding.Space == 0, "Unexpected resource binding");
      if (expected.size) {
        D3D12_SHADER_BUFFER_DESC cb{};
        check(reflection->GetConstantBufferByName(expected.name)->GetDesc(&cb), "Constant buffer");
        if (std::string(expected.name) == "xe_float_cbuffer") {
          require(cb.Size == 144 || cb.Size == expected.size, "Unexpected float constant buffer size");
          instanced = cb.Size == 144;
        } else require(cb.Size == expected.size, "Unexpected constant buffer size");
      }
    }
  }
};
struct Fixture {
  std::array<uint32_t, 128> system{};
  std::array<uint32_t, 192> fetch{};
  std::array<F4, 32> constants{};
  std::array<uint32_t, 4096> data{};
  std::array<F4, vertices> expected{};
  Fixture(unsigned scenario, unsigned flags, bool instanced) {
    system[0] = flags; system[3] = UINT32_MAX; system[7] = vertices - 1;
    const F4 scale{0.91f, -1.03f, 0.999f, 0}, offset{0.00031f, -0.00027f, 0.001f, 0};
    std::memcpy(system.data() + 32, scale.data(), 12);
    std::memcpy(system.data() + 36, offset.data(), 12);
    // Fetch 95, base 0, no endian conversion; packed 12-byte terrain vertices.
    fetch[190] = 3; fetch[191] = uint32_t(data.size()) << 2;
    for (auto& c : constants) c = {0.125f, 0.25f, 0.5f, 1.0f};
    // Keep fog exponent inputs modest and finite, with both saturated regions.
    constants[5] = {-0.0001f, -0.0002f, -0.0003f, -0.0004f};
    constants[14] = {1,0,0,0}; constants[15] = {0,1,0,0}; constants[16] = {0,0,1,0};
    if (scenario & 2) {
      constants[14] = {0, -2, 0, 0}; constants[15] = {0.5f, 0, 0, 0};
      constants[16] = {0, 0, 0.5f, 0};
    }
    const float sign = scenario & 1 ? -1.0f : 1.0f;
    constants[17] = {sign * (30000.125f + scenario * 217.25f),
                     -sign * (41000.375f + scenario * 131.5f), 1200.625f + scenario * 31.25f, 1};
    constants[24] = {128, 1.0f / 32767.0f, 0, 0};
    constants[27] = {256, -32768, 0, 0}; // packed guest c254
    constants[28] = {0, 1, 0, 1};        // packed guest c255
    constants[0] = {0.001037f, 0.000217f, 0.000019f, 0.0000031f};
    constants[1] = {-0.000413f, 0.001143f, -0.000027f, 0.0000017f};
    constants[2] = {0.000213f, -0.000391f, 0.000981f, 0.0000073f};
    if (instanced) {
      constants[5][0] = 1.0f / 16; constants[6][0] = 16;
      constants[8] = {2.0f / 255, 0, -1, 1}; // packed guest c255
      fetch[188] = 4096 | 3; fetch[189] = (8 * 14) << 2;
      fetch[191] = (16 * 12) << 2;
      for (unsigned v = 0; v < 16; ++v) {
        const F4 position{float(v % 4) * 0.75f, float(v / 4) * 1.25f, float(int(v % 7) - 3) * 0.375f, 1};
        for (unsigned k = 0; k < 3; ++k) data[v*12+k] = std::bit_cast<uint32_t>(position[k]);
        data[v*12+4] = 0x008080ff; data[v*12+5] = 0x0080ff80; data[v*12+6] = 0x00ff8080;
        data[v*12+8] = std::bit_cast<uint32_t>(float(v % 4) / 4);
        data[v*12+9] = std::bit_cast<uint32_t>(float(v / 4) / 4);
      }
      for (unsigned instance = 0; instance < 8; ++instance) {
        F4 translation = constants[17];
        translation[0] += float(instance) * 31.25f; translation[1] -= float(instance) * 17.5f;
        const F4 rows[]{translation, constants[14], constants[15], constants[16]};
        for (unsigned row = 0; row < 4; ++row) for (unsigned k = 0; k < 3; ++k)
          data[1024 + instance*14 + row*3+k] = std::bit_cast<uint32_t>(rows[row][k]);
      }
    }
    for (unsigned k = 0; k < 4; ++k) {
      const double center = double(constants[0][k]) * constants[17][0] +
                            double(constants[1][k]) * constants[17][1] +
                            double(constants[2][k]) * constants[17][2];
      constants[3][k] = float(-center + (k == 3 ? 1.0 : 0.0));
    }
    for (unsigned v = 0; v < vertices; ++v) {
      unsigned x = v % 16, y = v / 16;
      const float height = float(int(v % 11) - 5) * 0.375f;
      const unsigned packed_z = scenario & 4 ? (v * 17) % 256 : 0;
      if (!instanced) {
        data[v*3] = x | y << 8 | packed_z << 16 | 128u << 24;
        data[v*3+1] = std::bit_cast<uint32_t>(height);
        data[v*3+2] = 0; // zero packed normal XY; reconstructed normal Z is 1
      }
      const float z = float(float(float(packed_z) * constants[24][1]) + height);
      F4 world{};
      for (unsigned k = 0; k < 4; ++k) {
        float local = float(float(y * 128) * constants[15][k]);
        local = float(local + float(float(x * 128) * constants[14][k]));
        local = float(local + float(z * constants[16][k]));
        world[k] = float(local + constants[17][k]);
      }
      if (instanced) {
        const unsigned object_vertex = v % 16, instance = v / 16;
        for (unsigned k = 0; k < 3; ++k) {
          float local = std::bit_cast<float>(data[1024 + instance*14+k]);
          for (int axis = 2; axis >= 0; --axis) {
            const float coordinate = std::bit_cast<float>(data[object_vertex*12+axis]);
            const float basis = std::bit_cast<float>(data[1024 + instance*14+(axis+1)*3+k]);
            local = float(local + float(coordinate * basis));
          }
          world[k] = local;
        }
        world[3] = 1;
      }
      std::array<double, 4> p{};
      for (unsigned k = 0; k < 4; ++k) {
        p[k] = double(constants[3][k]) * world[3];
        p[k] = p[k] + double(constants[2][k]) * world[2];
        p[k] = p[k] + double(constants[1][k]) * world[1];
        p[k] = p[k] + double(constants[0][k]) * world[0];
      }
      for (unsigned k = 0; k < 3; ++k) p[k] = p[k] * double(scale[k]) + p[3] * double(offset[k]);
      for (unsigned k = 0; k < 4; ++k) expected[v][k] = float(p[k]);
    }
  }
};
struct GPU {
  ComPtr<ID3D12Device> device;
  ComPtr<ID3D12CommandQueue> queue;
  ComPtr<ID3D12CommandAllocator> allocator;
  ComPtr<ID3D12GraphicsCommandList> list;
  ComPtr<ID3D12Fence> fence;
  ComPtr<ID3D12RootSignature> root;
  ComPtr<ID3D12InfoQueue> info;
  HANDLE event = nullptr;
  UINT64 serial = 0;
  DXGI_ADAPTER_DESC1 adapter_desc{};
  ~GPU() { if (event) CloseHandle(event); }
  GPU() {
    ComPtr<ID3D12Debug> debug;
    if (SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&debug)))) debug->EnableDebugLayer();
    ComPtr<IDXGIFactory6> factory;
    check(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)), "DXGI factory");
    ComPtr<IDXGIAdapter1> adapter;
    for (unsigned index = 0;; ++index) {
      const auto hr = factory->EnumAdapterByGpuPreference(index, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                                                        IID_PPV_ARGS(&adapter));
      require(hr != DXGI_ERROR_NOT_FOUND, "No hardware D3D12 adapter");
      check(hr, "Enumerate adapter");
      check(adapter->GetDesc1(&adapter_desc), "Adapter description");
      if (!(adapter_desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) &&
          SUCCEEDED(D3D12CreateDevice(adapter.Get(), D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device)))) break;
      adapter.Reset();
    }
    D3D12_FEATURE_DATA_D3D12_OPTIONS options{};
    check(device->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS, &options, sizeof(options)), "GPU features");
    require(options.DoublePrecisionFloatShaderOps, "GPU does not support shader double precision");
    device.As(&info);
    D3D12_COMMAND_QUEUE_DESC q{};
    check(device->CreateCommandQueue(&q, IID_PPV_ARGS(&queue)), "Create queue");
    check(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)), "Create allocator");
    check(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator.Get(), nullptr,
                                  IID_PPV_ARGS(&list)), "Create command list");
    check(list->Close(), "Close initial list");
    check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)), "Create fence");
    event = CreateEventW(nullptr, FALSE, FALSE, nullptr); require(event, "Create event");
    std::array<D3D12_ROOT_PARAMETER, 5> params{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_SRV;
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_UAV;
    for (unsigned i = 2; i < 5; ++i) params[i].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    params[2].Descriptor.ShaderRegister = 0; params[3].Descriptor.ShaderRegister = 3;
    params[4].Descriptor.ShaderRegister = 1;
    for (auto& p : params) p.ShaderVisibility = D3D12_SHADER_VISIBILITY_VERTEX;
    D3D12_ROOT_SIGNATURE_DESC desc{};
    desc.NumParameters = unsigned(params.size()); desc.pParameters = params.data();
    desc.Flags = D3D12_ROOT_SIGNATURE_FLAG_ALLOW_STREAM_OUTPUT;
    ComPtr<ID3DBlob> blob, errors;
    check(D3D12SerializeRootSignature(&desc, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &errors), "Serialize root signature");
    check(device->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(),
                                     IID_PPV_ARGS(&root)), "Create root signature");
  }
  void validate() {
    check(device->GetDeviceRemovedReason(), "Device removed");
    if (!info) return;
    for (UINT64 i = 0; i < info->GetNumStoredMessages(); ++i) {
      SIZE_T size = 0; check(info->GetMessage(i, nullptr, &size), "Debug message size");
      std::vector<uint8_t> bytes(size);
      auto* msg = reinterpret_cast<D3D12_MESSAGE*>(bytes.data());
      check(info->GetMessage(i, msg, &size), "Debug message");
      require(msg->Severity > D3D12_MESSAGE_SEVERITY_WARNING, msg->pDescription);
    }
    info->ClearStoredMessages();
  }
  ComPtr<ID3D12Resource> buffer(UINT64 size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES state,
                              D3D12_RESOURCE_FLAGS flags = D3D12_RESOURCE_FLAG_NONE) {
    D3D12_HEAP_PROPERTIES props{}; props.Type = heap;
    D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width = size; desc.Height = 1; desc.DepthOrArraySize = 1; desc.MipLevels = 1;
    desc.SampleDesc.Count = 1; desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR; desc.Flags = flags;
    ComPtr<ID3D12Resource> result;
    check(device->CreateCommittedResource(&props, D3D12_HEAP_FLAG_NONE, &desc, state, nullptr,
                                         IID_PPV_ARGS(&result)), "Create buffer");
    return result;
  }
  void upload(ID3D12Resource* resource, const void* bytes, size_t size, size_t offset = 0) {
    void* mapped = nullptr; D3D12_RANGE read_range{0,0};
    check(resource->Map(0, &read_range, &mapped), "Map upload");
    std::memcpy(static_cast<uint8_t*>(mapped) + offset, bytes, size);
    const D3D12_RANGE written{offset, offset + size}; resource->Unmap(0, &written);
  }
  void transition(ID3D12Resource* resource, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
    D3D12_RESOURCE_BARRIER barrier{}; barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
    list->ResourceBarrier(1, &barrier);
  }
  void pipeline(Program& p) {
    std::array<D3D12_SO_DECLARATION_ENTRY, 6> entries{};
    for (unsigned i = 0; i < 6; ++i) entries[i] = {0, i == 5 ? "SV_Position" : "TEXCOORD", i == 5 ? 0 : i, 0, 4, 0};
    const UINT stream_stride = stride;
    D3D12_GRAPHICS_PIPELINE_STATE_DESC desc{};
    desc.pRootSignature = root.Get(); desc.VS = {p.bytes.data(), p.bytes.size()};
    desc.StreamOutput = {entries.data(), unsigned(entries.size()), &stream_stride, 1, D3D12_SO_NO_RASTERIZED_STREAM};
    desc.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    desc.SampleMask = UINT_MAX; desc.SampleDesc.Count = 1;
    desc.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID; desc.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    desc.RasterizerState.DepthClipEnable = TRUE;
    desc.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    desc.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_POINT;
    check(device->CreateGraphicsPipelineState(&desc, IID_PPV_ARGS(&p.pipeline)), "Create stream-output pipeline");
    validate();
  }
  std::vector<float> run(const Program& p, const Fixture& fixture) {
    constexpr UINT64 output_size = vertices * stride, counter_offset = output_size;
    auto inputs = buffer(sizeof(fixture.data), D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
    upload(inputs.Get(), fixture.data.data(), sizeof(fixture.data));
    auto cb = buffer(2048, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
    upload(cb.Get(), fixture.system.data(), sizeof(fixture.system));
    upload(cb.Get(), fixture.fetch.data(), sizeof(fixture.fetch), 512);
    upload(cb.Get(), fixture.constants.data(), sizeof(fixture.constants), 1280);
    auto srv = buffer(sizeof(fixture.data), D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COPY_DEST);
    auto uav = buffer(sizeof(fixture.data), D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS);
    auto output = buffer(output_size + 8, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COPY_DEST);
    auto zero = buffer(256, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
    const UINT64 zero_count = 0; upload(zero.Get(), &zero_count, sizeof(zero_count));
    auto result = buffer(output_size + 8, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    check(allocator->Reset(), "Reset allocator"); check(list->Reset(allocator.Get(), p.pipeline.Get()), "Reset list");
    list->CopyBufferRegion(srv.Get(), 0, inputs.Get(), 0, sizeof(fixture.data));
    list->CopyBufferRegion(uav.Get(), 0, inputs.Get(), 0, sizeof(fixture.data));
    list->CopyBufferRegion(output.Get(), counter_offset, zero.Get(), 0, sizeof(zero_count));
    transition(srv.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    transition(uav.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    transition(output.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_STREAM_OUT);
    list->SetGraphicsRootSignature(root.Get());
    list->SetGraphicsRootShaderResourceView(0, srv->GetGPUVirtualAddress());
    list->SetGraphicsRootUnorderedAccessView(1, uav->GetGPUVirtualAddress());
    list->SetGraphicsRootConstantBufferView(2, cb->GetGPUVirtualAddress());
    list->SetGraphicsRootConstantBufferView(3, cb->GetGPUVirtualAddress() + 512);
    list->SetGraphicsRootConstantBufferView(4, cb->GetGPUVirtualAddress() + 1280);
    D3D12_STREAM_OUTPUT_BUFFER_VIEW view{output->GetGPUVirtualAddress(), output_size,
                                       output->GetGPUVirtualAddress() + counter_offset};
    list->SOSetTargets(0, 1, &view); list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_POINTLIST);
    list->DrawInstanced(vertices, 1, 0, 0);
    transition(output.Get(), D3D12_RESOURCE_STATE_STREAM_OUT, D3D12_RESOURCE_STATE_COPY_SOURCE);
    list->CopyBufferRegion(result.Get(), 0, output.Get(), 0, output_size + 8);
    check(list->Close(), "Close draw list");
    ID3D12CommandList* lists[]{list.Get()}; queue->ExecuteCommandLists(1, lists);
    check(queue->Signal(fence.Get(), ++serial), "Signal draw fence");
    check(fence->SetEventOnCompletion(serial, event), "Register draw fence");
    require(WaitForSingleObject(event, 5000) == WAIT_OBJECT_0, "GPU draw timeout");
    validate();
    void* mapped = nullptr; const D3D12_RANGE range{0, output_size + 8};
    check(result->Map(0, &range, &mapped), "Read stream output");
    UINT64 filled = 0; std::memcpy(&filled, static_cast<uint8_t*>(mapped) + counter_offset, 8);
    std::vector<float> values(vertices * lanes); std::memcpy(values.data(), mapped, output_size);
    const D3D12_RANGE written{0,0}; result->Unmap(0, &written);
    require(filled == output_size, "Incomplete or overflowing stream output");
    return values;
  }
};
}
int main(int argc, char** argv) {
  try {
    require(argc == 5, "Usage: aot_projection_gpu guest-original.dxbc snapshot-only.dxbc precise.dxbc NEW_OUTPUT_DIR");
    const std::filesystem::path output(argv[4]);
    require(!std::filesystem::exists(output), "Output directory must be new");
    std::filesystem::create_directories(output);
    Program original(argv[1]), snapshot(argv[2]), precise(argv[3]);
    require(original.instanced == snapshot.instanced && original.instanced == precise.instanced,
            "Mixed shader input layouts");
    GPU gpu; for (auto* p : {&original, &snapshot, &precise}) gpu.pipeline(*p);
    std::ofstream report(output / "results.csv");
    report << "scenario,flags,vertices,material_lanes_checked,changed_position_lanes,cpu_position_lanes_checked\n";
    unsigned total_changed = 0;
    for (unsigned scenario = 0; scenario < 8; ++scenario) for (unsigned flags : {8u,9u,0u,2u,4u,6u,10u,12u,14u}) {
      Fixture fixture(scenario, flags, original.instanced);
      const auto tag = std::to_string(scenario) + "-" + std::to_string(flags);
      write(output / (tag + "-system.bin"), fixture.system.data(), sizeof(fixture.system));
      write(output / (tag + "-fetch.bin"), fixture.fetch.data(), sizeof(fixture.fetch));
      write(output / (tag + "-constants.bin"), fixture.constants.data(), sizeof(fixture.constants));
      write(output / (tag + "-vertices.bin"), fixture.data.data(), sizeof(fixture.data));
      write(output / (tag + "-cpu-position.bin"), fixture.expected.data(), sizeof(fixture.expected));
      const auto a = gpu.run(original, fixture), b = gpu.run(snapshot, fixture), c = gpu.run(precise, fixture);
      write(output / (tag + "-guest-output.bin"), a.data(), a.size() * sizeof(float));
      write(output / (tag + "-snapshot-output.bin"), b.data(), b.size() * sizeof(float));
      write(output / (tag + "-precise-output.bin"), c.data(), c.size() * sizeof(float));
      require(a == b && !std::memcmp(a.data(), b.data(), a.size()*sizeof(float)), "Snapshot-only changed output in " + tag);
      unsigned changed = 0, cpu_checked = 0;
      for (unsigned v = 0; v < vertices; ++v) for (unsigned k = 0; k < lanes; ++k) {
        const unsigned at = v * lanes + k;
        require(std::isfinite(a[at]) && std::isfinite(c[at]), "Nonfinite output in " + tag + " lane " + std::to_string(k));
        const auto before = std::bit_cast<uint32_t>(a[at]), after = std::bit_cast<uint32_t>(c[at]);
        if (k < 20 || (flags & 14) != 8) require(before == after, "Preserved output changed in " + tag);
        else {
          changed += before != after; ++cpu_checked;
          require(after == std::bit_cast<uint32_t>(fixture.expected[v][k-20]),
                  "CPU position mismatch in " + tag + " vertex " + std::to_string(v) + " lane " + std::to_string(k-20) +
                  " actual=" + std::to_string(c[at]) + " expected=" + std::to_string(fixture.expected[v][k-20]));
        }
      }
      if ((flags & 14) == 8) require(changed > 0, "Fixture did not exercise precision differences");
      total_changed += changed;
      report << scenario << ',' << flags << ',' << vertices << ',' << vertices*20 << ',' << changed << ',' << cpu_checked << '\n';
      report.flush();
    }
    require(bool(report), "Failed writing report");
    std::ofstream summary(output / "summary.json");
    summary << "{\"complete\":true,\"scope\":\"synthetic finite inputs; no rasterization or native scene\","
            << "\"fixture\":\"" << (original.instanced ? "instanced" : "terrain") << "\","
            << "\"vendor_id\":" << gpu.adapter_desc.VendorId << ",\"device_id\":" << gpu.adapter_desc.DeviceId
            << ",\"debug_layer\":" << (gpu.info ? "true" : "false")
            << ",\"scenarios\":72,\"draws\":216,\"vertices_per_draw\":128,\"changed_position_lanes\":" << total_changed << "}\n";
    require(bool(summary), "Failed writing summary");
    std::cout << "72 synthetic cases passed on hardware GPU; " << total_changed << " position lanes corrected.\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
