// Exercise the actual recompiled retail GetData reader, including its pending
// markers, device completion fence and begin/end sample subtraction.
#include "generated/default/army_of_two_init.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#ifdef _WIN32
#include <windows.h>
#endif

bool TestRetailOcclusionReader() {
#ifdef _WIN32
  auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, size_t{0x100000000ull},
                                                 MEM_RESERVE, PAGE_NOACCESS));
  if (!base) return false;
  struct Release {
    uint8_t* base;
    ~Release() { VirtualFree(base, 0, MEM_RELEASE); }
  } release{base};
  constexpr uint32_t query = 0x1000, device = 0x4000, output = 0x7000;
  constexpr uint32_t fence = 0x7800, snapshot = 0xC0010000;
  if (!VirtualAlloc(base, 0x10000, MEM_COMMIT, PAGE_READWRITE) ||
      !VirtualAlloc(base + snapshot, 0x1000, MEM_COMMIT, PAGE_READWRITE)) return false;
  auto be = [base](uint32_t address, uint32_t value) { REX_STORE_U32(address, value); };
  auto le = [base](uint32_t address, uint32_t value) {
    std::memcpy(base + address, &value, sizeof(value));
  };
  be(query, device);
  be(query + 4, 9);  // Retail occlusion query type.
  be(query + 28, 0x10000);  // Physical pointer translated by the original reader.
  be(query + 148, 1);
  auto read = [&](uint32_t status, uint32_t samples, bool consumed) {
    base[query + 20] = 0;
    be(output, 0xDEADBEEF);
    PPCContext ctx{};
    ctx.r1.u32 = 0xF000;
    ctx.r3.u32 = query;
    ctx.r4.u32 = output;
    ctx.r5.u32 = 4;
    sub_82A433C0(ctx, base);
    return ctx.r3.u32 == status && REX_LOAD_U32(output) == samples &&
           bool(base[query + 20] & 0x80) == consumed && ctx.r1.u32 == 0xF000;
  };
  be(snapshot + 16, 0xFFFFFEED);
  be(snapshot + 20, 0xFFFFFEED);
  if (!read(1, 1, false)) return false;
  le(snapshot + 16, 0); le(snapshot + 20, 0);
  if (!read(0, 0, true)) return false;
  le(snapshot + 16, 77); le(snapshot + 20, 5);
  le(snapshot + 48, 17); le(snapshot + 52, 3);
  if (!read(0, 62, true)) return false;
  // Even populated snapshots remain pending until the associated device fence.
  be(query + 24, 100);
  be(device + 10896, fence);
  be(device + 10908, 101);
  be(fence, 99);
  if (!read(1, 1, false)) return false;
  be(fence, 100);
  if (!read(0, 62, true)) return false;
  std::puts("Retail occlusion reader: pending, hidden, visible and fence cases passed");
#endif
  return true;
}
