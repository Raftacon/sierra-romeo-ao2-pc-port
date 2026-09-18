#pragma once
#include <array>
#include <cstdint>
#include <rex/ppc/function.h>
namespace aot {
PPCFunc* KeyImportOverride(uint32_t guest_import);
int AcquireSessionNetworkKey(const uint8_t* id, const uint8_t* key);
void ReleaseSessionNetworkKey(const uint8_t* id);
bool FindNetworkKey(const uint8_t* id, std::array<uint8_t, 16>& output);
void ResetNetworkKeys();
}
