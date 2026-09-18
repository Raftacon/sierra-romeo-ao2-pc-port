#pragma once
#include <cstdint>
#include <rex/ppc/function.h>
namespace aot {
PPCFunc* ReceiveImportOverride(uint32_t guest_import);
// Stop and drain host I/O before the runtime releases guest memory.
void InitializeNetworkReceive();
void ShutdownNetworkReceive();
}
