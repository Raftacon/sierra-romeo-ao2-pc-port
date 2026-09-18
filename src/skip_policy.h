#pragma once
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <string>
#include <string_view>

namespace aot {
inline bool CanStopMovieImmediately(int32_t playlist_index, int32_t playlist_count,
    bool playlist_stop_flag) {
  // The original stop routine defers to the startup/loading playlist while an
  // entry is active. A request there does not guarantee that the clip ends now.
  return playlist_stop_flag || playlist_index == -1 || playlist_index >= playlist_count;
}
inline bool IsSkippableMovie(std::string name) {
  std::transform(name.begin(), name.end(), name.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  const auto slash = name.find_last_of("/\\");
  if (slash != std::string::npos) name.erase(0, slash + 1);
  if (name.ends_with(".bik")) name.resize(name.size() - 4);
  // Ao2Logo2 is the static full-screen title/loading card. LoadingCoin*,
  // Coin, NetConnect and AutoSaveIcon are loading/status movies, not scenes.
  for (std::string_view allowed : {"ealogo", "esrblogo", "ao2logo", "intro", "aggro",
      "lvl0_temp_intro", "lvl4_full", "lvl5", "lvl6", "lvl7", "lvl8", "lvl9"})
    if (name == allowed) return true;
  return false;
}
inline float SkipPromptAlpha(float current, bool active, bool recent_input, float delta) {
  if (!active) return 0;
  const float target = recent_input ? 1.f : 0.f;
  const float step = std::clamp(delta, 0.f, .1f) / (recent_input ? .2f : .3f);
  return current < target ? std::min(target, current + step) : std::max(target, current - step);
}
}
