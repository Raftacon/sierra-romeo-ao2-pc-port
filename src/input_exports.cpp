// Keep guest XInput imports in the same module as the synchronized InputSystem.
// Its methods are nonvirtual: calls inside the SDK DLL bypass an EXE definition.
#include <rex/hook.h>
#include <rex/input/input_system.h>
#include <mutex>

namespace aot {
// Production uses the active runtime; the import contract test supplies its
// own input system without starting graphics, audio, or guest game threads.
rex::input::InputSystem* InputSystemForGuest();
}

namespace {
using namespace rex;
using namespace rex::input;
constexpr uint32_t kAnyUser = 1u << 30;
bool Unsupported(uint32_t flags) { return (flags & 0xFF) && !(flags & 1); }
uint32_t User(uint32_t user, uint32_t flags = 0) {
  return (user & 0xFF) == 0xFF || (flags & kAnyUser) ? 0 : user;
}
InputSystem* Input() {
  static std::once_flag reported;
  std::call_once(reported, [] { REXLOG_INFO("Guest XInput imports use the synchronized PC input system"); });
  return aot::InputSystemForGuest();
}
u32 Capabilities(u32 user, u32 flags, ppc_ptr_t<X_INPUT_CAPABILITIES> caps) {
  if (!caps) return X_ERROR_BAD_ARGUMENTS;
  if (Unsupported(flags)) return X_ERROR_DEVICE_NOT_CONNECTED;
  return Input()->GetCapabilities(User(user, flags), flags, caps);
}
u32 CapabilitiesEx(u32, u32 user, u32 flags, ppc_ptr_t<X_INPUT_CAPABILITIES> caps) {
  return Capabilities(user, flags, caps);
}
u32 State(u32 user, u32 flags, ppc_ptr_t<X_INPUT_STATE> state) {
  if (Unsupported(flags)) return X_ERROR_DEVICE_NOT_CONNECTED;
  return Input()->GetState(User(user, flags), state);
}
u32 Vibration(u32 user, u32, ppc_ptr_t<X_INPUT_VIBRATION> vibration) {
  if (!vibration) return X_ERROR_BAD_ARGUMENTS;
  return Input()->SetState(User(user), vibration);
}
u32 Keystroke(u32 user, u32 flags, ppc_ptr_t<X_INPUT_KEYSTROKE> key) {
  if (!key) return X_ERROR_BAD_ARGUMENTS;
  if (Unsupported(flags)) return X_ERROR_DEVICE_NOT_CONNECTED;
  return Input()->GetKeystroke(User(user, flags), flags, key);
}
u32 KeystrokeEx(mapped_u32 user, u32 flags, ppc_ptr_t<X_INPUT_KEYSTROKE> key) {
  if (!key || !user) return X_ERROR_BAD_ARGUMENTS;
  if (Unsupported(flags)) return X_ERROR_DEVICE_NOT_CONNECTED;
  auto result = Input()->GetKeystroke(User(*user, flags), flags, key);
  if (XSUCCEEDED(result)) *user = key->user_index;
  return result;
}
}

REX_HOOK(__imp__XamInputGetCapabilities, Capabilities)
REX_HOOK(__imp__XamInputGetCapabilitiesEx, CapabilitiesEx)
REX_HOOK(__imp__XamInputGetState, State)
REX_HOOK(__imp__XamInputSetState, Vibration)
REX_HOOK(__imp__XamInputGetKeystroke, Keystroke)
REX_HOOK(__imp__XamInputGetKeystrokeEx, KeystrokeEx)
