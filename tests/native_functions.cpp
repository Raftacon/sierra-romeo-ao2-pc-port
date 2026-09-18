// Regression test for a real game function recovered from an indirect call.
// Oracle: PPC64 integer add semantics and big-endian guest storage; exercises
// overflow, byte order, and unrelated-memory preservation in the compiled code.
#include "generated/default/army_of_two_init.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <algorithm>
#include <vector>
#include <bit>
#include <rex/cvar.h>
#include "src/coop_input_packet.h"
#ifdef _WIN32
#include <windows.h>
#endif

extern void AotPresentInterval(PPCRegister& r10);
extern bool TestRetailOcclusionReader();
static void RetailIntervalFixed(PPCContext& ctx, uint8_t* base) {
#include "present_interval_fixed.inc"
}
static void RetailIntervalPrevious(PPCContext& ctx, uint8_t* base) {
#include "present_interval_previous.inc"
}

#ifdef _WIN32
static bool TestRetailCoopInputDecoder() {
  auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,size_t(1)<<32,MEM_RESERVE,PAGE_NOACCESS));
  if (!base) return false;
  struct Release { uint8_t* base; ~Release() { VirtualFree(base,0,MEM_RELEASE); } } release{base};
  if (!VirtualAlloc(base+0x10000,0x10000,MEM_COMMIT,PAGE_READWRITE) ||
      !VirtualAlloc(base+0x821A1000,4096,MEM_COMMIT,PAGE_READWRITE)) return false;
  // Original byte-reader switch table at 821A1580, from the loaded image.
  constexpr std::array<uint8_t,8> reader_table{0x3D,0x34,0x2B,0x22,0x19,0x10,0x07,0x00};
  std::copy(reader_table.begin(),reader_table.end(),base+0x821A1580);
  auto word=[&](uint32_t address)->rex::be<uint32_t>& { return *reinterpret_cast<rex::be<uint32_t>*>(base+address); };
  constexpr uint32_t record=0x11000, stream=0x12000, bytes=0x13000, stack=0x18000;
  for (unsigned sender=0;sender<2;++sender) for (unsigned maximum=0;maximum<2;++maximum) {
    const unsigned events=maximum ? 15 : 0, axes=maximum ? 8 : 0;
    std::vector<uint8_t> packet{0xD5,0x80,0,0,17,0x80,0x81,
        0x82,0x31,0x80,0x19,0x80,0x12,0x34,0x56,0x78,uint8_t(((axes<<4)|events)^0x80)};
    for (unsigned i=0;i<events;++i) { packet.push_back(0x81); packet.push_back(0x82); packet.push_back(3); }
    for (unsigned i=0;i<axes;++i) { packet.push_back(0x83); packet.push_back(0x84); }
    for (auto value:{0x80,0x80+int(sender),0x80,0x80,0x80,0x80}) packet.push_back(uint8_t(value));
    if (!aot::ValidCoopInputPacket(packet,sender)) return false;
    std::fill_n(base+record,0x254,uint8_t(0));
    std::copy(packet.begin()+7,packet.end(),base+bytes);
    PPCContext ctx{}; ctx.r1.u32=stack; ctx.r3.u32=stream; ctx.r4.u32=bytes; ctx.r5.u32=uint32_t(packet.size()-7);
    sub_822365E8(ctx,base);
    ctx.r3.u32=record; ctx.r4.u32=stream; ctx.r5.u32=17; sub_82947B30(ctx,base);
    if (word(record+0x2C)!=15 || word(record+0x30)!=0x31 || word(record+0x34)!=25 ||
        word(record)!=0x12345678 || word(record+0xF4)!=events || word(record+0x138)!=axes ||
        word(record+0x250)!=sender || word(stream+8+0x410)!=packet.size()-7 || word(stream+8+0x414)) return false;
    if (maximum && (word(record+0x40)!=1 || word(record+0xEC)!=2 || word(record+0xF8)!=3 || word(record+0x134)!=4)) return false;
  }
  return true;
}
static bool TestRetailCoopViewportDescriptor() {
  // Execute the retail receiver, not a copy of the adapter's field writes.
  // Reserve guest address space but commit only the globals and test arena.
  auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,size_t(1)<<32,MEM_RESERVE,PAGE_NOACCESS));
  if (!base) return false;
  struct Release { uint8_t* base; ~Release() { VirtualFree(base,0,MEM_RELEASE); } } release{base};
  if (!VirtualAlloc(base+0x83122000,4096,MEM_COMMIT,PAGE_READWRITE) ||
      !VirtualAlloc(base+0x83114000,4096,MEM_COMMIT,PAGE_READWRITE) ||
      !VirtualAlloc(base+0x10000,0x10000,MEM_COMMIT,PAGE_READWRITE)) return false;
  auto word=[&](uint32_t address)->rex::be<uint32_t>& {
    return *reinterpret_cast<rex::be<uint32_t>*>(base+address);
  };
  constexpr uint32_t backend=0x11000, descriptor=0x10000, stack=0x18000;
  word(0x831228E4)=backend;
  const std::array<std::array<uint32_t,3>,3> cases{{{0,1280,720},{1,1920,1080},{1,16384,1}}};
  for (const auto& data:cases) {
    std::fill_n(base+backend,0xD58,uint8_t(0xCD));
    word(descriptor)=0x820E7DC0; word(descriptor+4)=data[0]; word(descriptor+8)=UINT32_MAX;
    word(descriptor+12)=data[1]; word(descriptor+16)=data[2];
    PPCContext ctx{}; ctx.r1.u32=stack; ctx.r3.u32=descriptor; ctx.lr=0x12345678;
    sub_829590A8(ctx,base);
    const auto target=backend+0xBB8+data[0]*16;
    if (word(target)!=std::bit_cast<uint32_t>(float(data[1])) ||
        word(target+4)!=std::bit_cast<uint32_t>(float(data[2])) ||
        word(target+8)!=data[0] || word(target+12)!=UINT32_MAX ||
        ctx.r1.u32!=stack || ctx.lr!=0x12345678) return false;
    for (uint32_t offset=0;offset<0xD58;++offset)
      if ((backend+offset<target || backend+offset>=target+16) && base[backend+offset]!=0xCD) return false;
  }
  // The real match reset must discard independently advanced shell frames
  // and restore the initial RNG seed before network frame zero is produced.
  constexpr uint32_t simulation=0x14000, tls_slot=0x16000, tls=0x17000;
  word(tls_slot)=tls; word(tls+4)=0xAABBCCDD;
  word(simulation+0x5C)=5332; word(simulation+0x60)=3222; word(simulation+0x64)=3219;
  PPCContext reset{}; reset.r1.u32=stack; reset.r13.u32=tls_slot; reset.r3.u32=simulation; reset.lr=0x12345678;
  sub_829554D8(reset,base);
  if (word(simulation+0x5C)!=UINT32_MAX || word(simulation+0x60)!=UINT32_MAX ||
      word(simulation+0x64)!=UINT32_MAX || word(tls+4)!=1 ||
      reset.r1.u32!=stack || reset.lr!=0x12345678) return false;
  return true;
}
#endif

int main() {
#ifdef _WIN32
  if (!TestRetailCoopInputDecoder()) {
    std::fputs("Retail co-op input decoder regression\n",stderr); return 1;
  }
  if (!TestRetailCoopViewportDescriptor()) {
    std::fputs("Retail co-op viewport descriptor regression\n",stderr); return 1;
  }
#endif
  if (!TestRetailOcclusionReader()) {
    std::fputs("Retail occlusion reader regression\n", stderr);
    return 1;
  }
#ifdef _WIN32
  // The original concurrency test exercised only direct host calls. Verify
  // that the actual generated guest thunks resolve inside this executable,
  // rather than the runtime DLL's separate, unsynchronized implementation.
  // Wait diagnostics likewise need the generated calls to reach their local
  // forwarders; the forwarders then call the original exports in the DLL.
  for (const size_t address : {0x8308AB34u, 0x8308AB44u, 0x8308AB54u,
       0x8308ADB4u, 0x8308ADF4u, 0x8308B054u, 0x8308B2B4u, 0x8308B7F4u}) {
    PPCFunc* target = nullptr;
    for (auto* mapping = PPCFuncMappings; mapping->host; ++mapping)
      if (mapping->guest == address) { target = mapping->host; break; }
    HMODULE module = nullptr;
    if (!target || !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
          GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
          reinterpret_cast<LPCWSTR>(target), &module) || module != GetModuleHandleW(nullptr)) {
      std::fprintf(stderr, "Guest thunk %08zx bypasses its local hook\n", address);
      return 1;
    }
  }
  std::puts("Guest input and wait thunks: all eight resolve inside the executable");
#endif
  constexpr std::array<uint32_t, 7> values{
      0, 1, 255, 0x12345678, 0x7fffffff, 0xfffffffe, 0xffffffff};
  for (const uint32_t value : values) {
    alignas(32) std::array<uint8_t, 512> memory;
    memory.fill(0xa5);
    for (int i = 0; i < 4; ++i)
      memory[260 + i] = static_cast<uint8_t>(value >> (24 - i * 8));
    auto expected = memory;
    const uint64_t result = uint64_t(value) + 1;
    for (int i = 0; i < 4; ++i)
      expected[260 + i] = static_cast<uint8_t>(result >> (24 - i * 8));
    PPCContext ctx{};
    ctx.r3.u64 = 256;
    ctx.r1.u64 = 480;
    ctx.r31.u64 = 0x1122334455667788;
    ctx.lr = 0x81234560;
    sub_82B50E98(ctx, memory.data());
    if (ctx.r3.u64 != result || memory != expected || ctx.r1.u64 != 480 ||
        ctx.r31.u64 != 0x1122334455667788 || ctx.lr != 0x81234560) {
      std::fprintf(stderr, "Recovered function regression for input %08x\n", value);
      return 1;
    }
  }
  std::puts("Recovered guest function: 7 arithmetic/endian/memory cases passed");

  // The computed callback at 82DD6D18 orders objects by the largest 16-bit
  // field in each object's linked list, descending. Its unequal branch enters
  // 82DD6D8C with live volatile registers, exercising cross-function state.
  const std::vector<std::pair<std::vector<uint16_t>, std::vector<uint16_t>>> pairs{
      {{}, {}}, {{3}, {5}}, {{5}, {3}}, {{5}, {5}},
      {{3, 10, 5}, {7, 10}}, {{65535, 1}, {32768}}, {{}, {1}}};
  for (const auto& [left, right] : pairs) {
    alignas(32) std::array<uint8_t, 1024> memory{};
    auto store = [&](uint32_t address, uint32_t value) {
      for (int i = 0; i < 4; ++i)
        memory[address + i] = static_cast<uint8_t>(value >> (24 - i * 8));
    };
    auto make_list = [&](uint32_t argument, uint32_t object, uint32_t nodes,
                         const std::vector<uint16_t>& values) {
      store(argument, object);
      store(object + 32, values.empty() ? 0 : nodes);
      for (size_t i = 0; i < values.size(); ++i) {
        const uint32_t node = nodes + uint32_t(i) * 32;
        store(node, node + 16);
        store(node + 4, i + 1 == values.size() ? 0 : node + 32);
        store(node + 16, (uint32_t(values[i]) << 13) | 0x1fff);
      }
    };
    make_list(32, 64, 512, left);
    make_list(36, 128, 768, right);
    const auto before = memory;
    const auto a = left.empty() ? 0 : *std::max_element(left.begin(), left.end());
    const auto b = right.empty() ? 0 : *std::max_element(right.begin(), right.end());
    const int expected = a == b ? 0 : (a > b ? -1 : 1);
    PPCContext ctx{};
    ctx.r3.u64 = 32;
    ctx.r4.u64 = 36;
    sub_82DD6D18(ctx, memory.data());
    if (ctx.r3.s32 != expected || memory != before) {
      std::fprintf(stderr, "Computed callback mismatch: max %u vs %u\n", a, b);
      return 1;
    }
  }
  std::puts("Computed guest callback: 7 list/order/shared-register cases passed");
  // Execute the actual generated retail branch graph and production hook.
  // The former placement is a negative control: mode 4 must retain three
  // refreshes there, demonstrating why testing only the hook body misses it.
  alignas(32) std::array<uint8_t, 16384> interval_memory{};
  const auto store_interval = [&](uint32_t address, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
      interval_memory[address + i] = uint8_t(value >> (24 - 8 * i));
  };
  unsigned checked = 0, previous_misses = 0;
  for (bool immediate : {false, true}) {
    if (!rex::cvar::SetFlagByName("aot_immediate_guest_present", immediate ? "true" : "false")) return 1;
    for (int fps : {30, 60}) {
      if (!rex::cvar::SetFlagByName("aot_fps", std::to_string(fps))) return 1;
      for (uint32_t mode : {0u, 1u, 2u, 4u, 0x80000000u, 8u}) {
        for (uint32_t fallback : {0u, 1u, 2u, 3u, 4u, 64u}) {
          interval_memory.fill(0xa5);
          store_interval(13596, mode);
          store_interval(80, fallback);
          store_interval(11852, 0x12345678);
          const auto before = interval_memory;
          const uint32_t retail = mode == 0x80000000u ? 0 : mode == 4 ? 3 :
              mode == 2 ? 2 : mode <= 1 ? 1 : fallback;
          const uint32_t expected = fps != 60 ? retail : immediate ? 0 : retail > 1 ? 1 : retail;
          PPCContext fixed{}, previous{};
          RetailIntervalFixed(fixed, interval_memory.data());
          RetailIntervalPrevious(previous, interval_memory.data());
          if (fixed.r8.u32 != expected * 256 || interval_memory != before ||
              (fps == 30 && previous.r8.u32 != fixed.r8.u32)) {
            std::fprintf(stderr, "Retail interval mismatch: fps=%d mode=%08x fallback=%u\n", fps, mode, fallback);
            return 1;
          }
          if (!immediate && fps == 60 && mode == 4 && previous.r8.u32 == 3 * 256) ++previous_misses;
          ++checked;
        }
      }
    }
  }
  if (!rex::cvar::SetFlagByName("aot_immediate_guest_present", "false")) return 1;
  if (previous_misses != 6) return 1;
  std::printf("Retail presentation: %u branch/packing cases pass; former hook misses all %u three-refresh controls\n",
              checked, previous_misses);
  return 0;
}
