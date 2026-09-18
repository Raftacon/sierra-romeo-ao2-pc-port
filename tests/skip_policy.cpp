#include "../src/skip_policy.h"
#include <iostream>
int main() {
  if (aot::CanStopMovieImmediately(0, 4, false) ||
      aot::CanStopMovieImmediately(3, 4, false)) return 7;
  if (!aot::CanStopMovieImmediately(-1, 0, false) ||
      !aot::CanStopMovieImmediately(4, 4, false) ||
      !aot::CanStopMovieImmediately(0, 4, true)) return 8;
  for (const char* movie : {"EALogo", "AGGRO", "Movies/LVL4_Full.bik", "game:\\Movies\\Intro.bik"})
    if (!aot::IsSkippableMovie(movie)) return 1;
  for (const char* movie : {"Ao2Logo2", "LoadingCoin", "LoadingCoinShell", "LoadingCoinWeapons", "AutoSaveIcon", "Coin", "NetConnect", "", "unknown"})
    if (aot::IsSkippableMovie(movie)) return 2;
  if (aot::SkipPromptAlpha(0, true, false, .016f) != 0) return 3;
  const auto fade_in = aot::SkipPromptAlpha(0, true, true, .1f);
  if (fade_in <= 0 || fade_in >= 1) return 4;
  const auto fade_out = aot::SkipPromptAlpha(1, true, false, .1f);
  if (fade_out <= 0 || fade_out >= 1) return 5;
  if (aot::SkipPromptAlpha(1, false, true, .016f) != 0) return 6;
  std::cout << "Skip loading exclusions and prompt fade policy passed\n";
}
