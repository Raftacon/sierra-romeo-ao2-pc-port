#pragma once
#include <cstdint>
namespace aot {
uint64_t SnapshotGpuVblankWake();
bool TryGpuVblankWake(uint64_t observed,uint32_t milliseconds);
void NotifyGpuVblankWake();
}
