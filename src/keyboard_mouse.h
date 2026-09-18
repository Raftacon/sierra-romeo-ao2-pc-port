#pragma once
#include <rex/input/input_system.h>

namespace aot {
std::unique_ptr<rex::system::IInputSystem> CreatePcInput(bool tool_mode);
std::unique_ptr<rex::input::InputDriver> CreateKeyboardMouseDriver();
void UpdateMouseCapture(rex::ui::Window* window, bool interactive_overlay);
void SetKeyboardMenuContext(bool title, bool main, bool menu);
void ObserveMouseGameFrame();
void ObserveMouseGameplay();
void ObserveFullscreenMovie();
std::pair<float, float> MouseFrame(uint32_t frame);
bool KeyboardPrompts();
}
