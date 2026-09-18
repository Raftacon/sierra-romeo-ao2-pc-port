#pragma once
#include <cstdint>

namespace aot {
// Implemented beside the spatial draw trace. Called before packet predication;
// observes only, and has no register or guest-memory write access.
void ObserveGpuCopyPacket(uint32_t packet, uint64_t bin_select,
                          uint64_t bin_mask, uint32_t mode,
                          uint32_t destination, uint32_t viz_query);
}
