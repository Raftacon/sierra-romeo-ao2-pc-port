#pragma once
#include <cstdint>
namespace aot {
void InitializeWaitDiagnostics();
void FinishWaitInterval();
void StartWaitInterval(uint64_t frame);
}
