#pragma once
#ifdef _WIN32
#include <windows.h>
#include <cstdint>
namespace aot {
inline bool NetworkMemorySpan(uint8_t* base, uint32_t address, uint64_t length, bool write) {
  if (!length) return true;
  if (!address || uint64_t(address) + length > 0x100000000ull) return false;
  uintptr_t current = reinterpret_cast<uintptr_t>(base + address);
  const uintptr_t end = current + size_t(length);
  while (current < end) {
    MEMORY_BASIC_INFORMATION info{};
    if (!VirtualQuery(reinterpret_cast<void*>(current), &info, sizeof(info)) || info.State != MEM_COMMIT ||
        (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    const DWORD protection = info.Protect & 0xFF;
    if (write && protection != PAGE_READWRITE && protection != PAGE_WRITECOPY &&
        protection != PAGE_EXECUTE_READWRITE && protection != PAGE_EXECUTE_WRITECOPY) return false;
    const uintptr_t next = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
    if (next <= current) return false;
    current = next;
  }
  return true;
}
}
#endif
