#include <rex/input/input_system.h>
#include <rex/hook.h>
#include <array>
#include <atomic>
#include <iostream>
#include <thread>

using namespace rex;
using namespace rex::input;
REX_EXTERN(__imp__XamInputGetState);
REX_EXTERN(__imp__XamInputGetCapabilities);
REX_EXTERN(__imp__XamInputSetState);
REX_EXTERN(__imp__XamInputGetKeystroke);
REX_EXTERN(__imp__XamInputGetCapabilitiesEx);
REX_EXTERN(__imp__XamInputGetKeystrokeEx);
namespace { InputSystem* guest_input = nullptr; }
namespace aot { InputSystem* InputSystemForGuest() { return guest_input; } }

class Pad final : public InputDriver {
 public:
  Pad(uint64_t id, uint16_t buttons) : InputDriver(nullptr, 0), id_(static_cast<DeviceId>(id)), buttons_(buttons) {}
  X_STATUS Setup() override { return X_STATUS_SUCCESS; }
  void EnumerateDevices(std::vector<DeviceInfo>& out) override { out.push_back({id_, 0, "Concurrency test pad", "", true}); }
  X_RESULT GetDeviceState(DeviceId id, X_INPUT_STATE* state) override {
    if (id != id_) return X_ERROR_DEVICE_NOT_CONNECTED;
    *state = {}; state->gamepad.buttons = buttons_; return X_ERROR_SUCCESS;
  }
  X_RESULT GetDeviceCapabilities(DeviceId id, uint32_t, X_INPUT_CAPABILITIES* caps) override {
    if (id != id_) return X_ERROR_DEVICE_NOT_CONNECTED;
    *caps = {}; caps->type = 1; return X_ERROR_SUCCESS;
  }
  X_RESULT SetDeviceVibration(DeviceId id, X_INPUT_VIBRATION*) override { return id == id_ ? X_ERROR_SUCCESS : X_ERROR_DEVICE_NOT_CONNECTED; }
  X_RESULT GetDeviceKeystroke(DeviceId id, uint32_t, X_INPUT_KEYSTROKE*) override { return id == id_ ? X_ERROR_EMPTY : X_ERROR_DEVICE_NOT_CONNECTED; }
 private:
  DeviceId id_; uint16_t buttons_;
};
int main() {
  InputSystem input(nullptr);
  input.AddDriver(std::make_unique<Pad>(1, 0x1000));
  input.AddDriver(std::make_unique<Pad>(2, 0x2000));
  input.SetDeviceAssignment(std::make_unique<SlotAssignment>());
  guest_input = &input;
  // Check the original import ABI, null-state queries, flag filtering and
  // any-user normalization before racing the actual import functions.
  alignas(16) std::array<uint8_t, 256> memory{};
  auto call = [&](PPCFunc* fn, uint32_t a, uint32_t b, uint32_t d, uint32_t e = 0) {
    PPCContext ctx{}; ctx.r3.u64 = a; ctx.r4.u64 = b; ctx.r5.u64 = d; ctx.r6.u64 = e;
    fn(ctx, memory.data()); return ctx.r3.u32;
  };
  if (call(__imp__XamInputGetState, 0xFF, 0, 0) != X_ERROR_SUCCESS ||
      call(__imp__XamInputGetState, 0, 2, 64) != X_ERROR_DEVICE_NOT_CONNECTED ||
      call(__imp__XamInputGetCapabilities, 0, 0, 0) != X_ERROR_BAD_ARGUMENTS ||
      call(__imp__XamInputGetCapabilitiesEx, 0, 0xFF, 1, 64) != X_ERROR_SUCCESS ||
      call(__imp__XamInputSetState, 0, 0, 0) != X_ERROR_BAD_ARGUMENTS ||
      call(__imp__XamInputGetKeystrokeEx, 32, 0, 0) != X_ERROR_BAD_ARGUMENTS) {
    std::cerr << "Guest input import contract failed\n"; return 1;
  }
  std::atomic<bool> start{false}, failed{false};
  auto sample = [&](bool guest) {
    alignas(16) std::array<uint8_t, 256> storage{};
    auto* output = reinterpret_cast<X_INPUT_STATE*>(storage.data() + 64);
    while (!start) std::this_thread::yield();
    for (int i = 0; i < 20000 && !failed; ++i) {
      X_RESULT result;
      if (guest) {
        PPCContext ctx{}; ctx.r3.u64 = 0xFF; ctx.r5.u64 = 64;
        __imp__XamInputGetState(ctx, storage.data()); result = ctx.r3.u32;
      } else result = input.GetState(0, output);
      if (result != X_ERROR_SUCCESS || output->gamepad.buttons != 0x3000) failed = true;
    }
  };
  std::thread movie(sample, true), game(sample, false), capabilities([&] {
    while (!start) std::this_thread::yield();
    for (int i = 0; i < 20000 && !failed; ++i) {
      if (call(__imp__XamInputGetCapabilities, 7, 1u << 30, 64) != X_ERROR_SUCCESS ||
          reinterpret_cast<X_INPUT_CAPABILITIES*>(memory.data() + 64)->type != 1 ||
          call(__imp__XamInputSetState, 0xFF, 0, 128) != X_ERROR_SUCCESS ||
          call(__imp__XamInputGetKeystroke, 0, 0, 160) != X_ERROR_EMPTY) failed = true;
    }
  });
  start = true; movie.join(); game.join(); capabilities.join();
  if (failed) { std::cerr << "Concurrent input polling lost or corrupted a device state\n"; return 1; }
  std::cout << "100000 concurrent host/guest-import input operations retained both devices\n";
}
