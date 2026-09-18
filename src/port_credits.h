#pragma once

namespace aot {
// Native rich-text styles and real newlines, appended before the first retail
// credits group. Keep the original game's credits and attribution intact.
inline constexpr char kPortCredits[] =
    "<Styles:AO2NoBGButton>SIERRA ROMEO<Styles:/>\n\n"
    "<Styles:OptionButtonStyle>Project Direction and Playtesting<Styles:/>\n"
    "<Styles:AO2White>Glenn Dodds (@Raftacon)<Styles:/>\n\n"
    "<Styles:AO2White>ReXGlue SDK and contributors<Styles:/>\n"
    "<Styles:AO2White>github.com/rexglue/rexglue-sdk<Styles:/>\n\n"
    "<Styles:AO2White>Xenia and contributors<Styles:/>\n"
    "<Styles:AO2White>github.com/xenia-project/xenia<Styles:/>\n\n"
    "<Styles:AO2White>AMD FidelityFX Super Resolution<Styles:/>\n"
    "<Styles:AO2White>github.com/GPUOpen-Effects/\nFidelityFX-FSR<Styles:/>\n\n"
    "<Styles:AO2White>Dear ImGui - Omar Cornut and contributors<Styles:/>\n"
    "<Styles:AO2White>github.com/ocornut/imgui<Styles:/>\n\n"
    "<Styles:AO2White>JSON for Modern C++ - Niels Lohmann and contributors<Styles:/>\n"
    "<Styles:AO2White>github.com/nlohmann/json<Styles:/>\n\n"
    "<Styles:AO2White>Kenney - Input Prompts<Styles:/>\n"
    "<Styles:AO2White>kenney.nl/assets/input-prompts<Styles:/>\n\n"
    "<Styles:AO2White>Sowa_95 - original 60 FPS patch research<Styles:/>\n"
    "<Styles:AO2White>github.com/xenia-canary/\ngame-patches<Styles:/>\n\n"
    "<Styles:AO2White>With thanks to the original Army of Two team and everyone credited below for bringing this game to life.<Styles:/>\n\n"
    "<Styles:AO2NoBGButton>ORIGINAL GAME<Styles:/>\n\n";
}  // namespace aot
