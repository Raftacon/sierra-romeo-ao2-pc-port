#include "generated/default/army_of_two_init.h"
#include "src/network_campaign.h"
#include <rex/hook.h>
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <windows.h>

#define AOT_NETWORK_IMPORT(address, name) REX_EXTERN(__imp__##name);
#include "src/network_imports.inc"
#undef AOT_NETWORK_IMPORT

void Check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
REX_EXTERN(sub_823368A8);
REX_EXTERN(__imp__sub_823368A8);
REX_EXTERN(sub_829558D0);
REX_EXTERN(__imp__sub_829558D0);
REX_EXTERN(sub_82948C70);
REX_EXTERN(__imp__sub_82948C70);
REX_EXTERN(sub_8295B508);
REX_EXTERN(__imp__sub_8295B508);
PPCFunc* Original(const char* name) {
  auto module = GetModuleHandleA(AOT_RUNTIME_DLL_NAME);
  Check(module != nullptr, "Runtime not loaded");
  auto* result = reinterpret_cast<PPCFunc*>(GetProcAddress(module, name));
  Check(result != nullptr, "Missing original export");
  return result;
}

int TestKernelDatagrams(const std::filesystem::path& output);
int main(int argc, char** argv) {
  try {
    aot::InitializeCampaignNetwork(nullptr);
    Check(sub_823368A8 != __imp__sub_823368A8, "Campaign menu hook was not linked");
    Check(sub_829558D0 != __imp__sub_829558D0 && sub_82948C70 != __imp__sub_82948C70 &&
          sub_8295B508 != __imp__sub_8295B508, "Campaign backend hooks were not linked");
    Check(!aot::ConsumeCampaignSetupDiagnostic(), "UI fixture was queued without a menu selection");
    if (argc == 3 && std::string(argv[1]) == "--kernel") {
      _putenv_s("AOT_NETWORK_LOG", "");
      return TestKernelDatagrams(argv[2]);
    }
    const bool enabled = argc == 2;
    _putenv_s("AOT_NETWORK_LOG", enabled ? argv[1] : "");
    // Verify every generated guest import resolves to our forwarder and every
    // exact original is available in the pinned DLL. No game/GPU startup.
#define AOT_NETWORK_IMPORT(address, name) { \
    PPCFunc* mapped = nullptr; \
    for (auto* entry = PPCFuncMappings; entry->host; ++entry) \
      if (entry->guest == address) { mapped = entry->host; break; } \
    Check(mapped == &__imp__##name, "Guest network thunk bypasses forwarder"); \
    HMODULE owner = nullptr; \
    Check(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, \
        reinterpret_cast<LPCWSTR>(mapped), &owner) && owner == GetModuleHandleW(nullptr), "Forwarder is outside executable"); \
    Check(Original("__imp__" #name) != mapped, "Forwarder resolves recursively"); \
  }
#include "src/network_imports.inc"
#undef AOT_NETWORK_IMPORT
    // These SDK entries require only the supplied memory, not a kernel state.
    // Compare all guest memory/context bytes and host last-error state.
    const std::array pairs{
      std::pair{"__imp__NetDll_XNetGetTitleXnAddr", &__imp__NetDll_XNetGetTitleXnAddr},
      std::pair{"__imp__NetDll_XNetGetEthernetLinkStatus", &__imp__NetDll_XNetGetEthernetLinkStatus}};
    for (auto [name, hooked] : pairs) {
      std::array<uint8_t, 512> expected, actual;
      expected.fill(0xA5); actual = expected;
      PPCContext reference{}, observed{};
      reference.lr = observed.lr = 0x82ABCDEF;
      reference.r3.u32 = observed.r3.u32 = 64;
      reference.r4.u32 = observed.r4.u32 = 128;
      auto* original = Original(name);
      SetLastError(0x1234);
      original(reference, expected.data());
      const DWORD expected_error = GetLastError();
      SetLastError(0x1234);
      hooked(observed, actual.data());
      const DWORD actual_error = GetLastError();
      Check(expected == actual, "Trace changed guest memory");
      Check(std::memcmp(&reference, &observed, sizeof(reference)) == 0, "Trace changed PPC context");
      Check(expected_error == actual_error, "Trace changed host last error");
    }
    // Concurrent polling must respect the per-API cap without corrupting rows.
    std::vector<std::thread> workers;
    for (int i = 0; i < 8; ++i) workers.emplace_back([] {
      std::array<uint8_t, 64> memory{};
      for (int j = 0; j < 100; ++j) {
        PPCContext ctx{};
        __imp__NetDll_XNetGetEthernetLinkStatus(ctx, memory.data());
      }
    });
    for (auto& worker : workers) worker.join();
    if (enabled) {
      std::ifstream file(argv[1]);
      std::string line;
      Check(bool(std::getline(file, line)), "No trace header");
      unsigned rows = 0, polls = 0;
      while (std::getline(file, line)) {
        Check(std::count(line.begin(), line.end(), ',') == 12, "Interleaved/incomplete trace row");
        ++rows;
        if (line.starts_with("NetDll_XNetGetEthernetLinkStatus,")) ++polls;
      }
      Check(rows == 257 && polls == 256, "Per-import trace limit not enforced");
    }
    std::puts("Network forwarding: 58 imports, context/memory/error preservation and concurrent trace cap passed");
  } catch (const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); return 1; }
}
