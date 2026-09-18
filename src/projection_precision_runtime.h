#pragma once
#include <cstdint>
#include <vector>
#include "projection_precision.h"
struct ID3D12Device;
namespace aot {
void InitializeProjectionPrecision(ID3D12Device* device);
bool ProjectionPrecisionEnabled();
bool WallProjectionPrecisionEnabled();
bool AutomaticProjectionPrecisionEnabled();
bool ProjectionSourceSnapshotsEnabled();
bool ProjectionFmaEnabled();
void TranslateProjectionPrecision(uint64_t guest, uint64_t modification,
    const ProjectionTranslationLayout& layout, std::vector<uint8_t>& binary);
}
