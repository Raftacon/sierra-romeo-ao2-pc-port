# Keyboard and mouse prompts

Unmodified source PNGs from [Kenney Input Prompts 1.5A](https://kenney.nl/assets/input-prompts),
licensed CC0. The original license is in `LICENSE.txt`.
Downloaded archive SHA-256:
`ac2fcf599080b0f3ba2d174c9474db6df1a0e96ff0662580e2da79a122ab78a1`.

`tools/prepare_keyboard_glyphs.py` encodes the source alpha masks as merged
rectangles in `src/keyboard_glyphs.generated.h`. The native game canvas draws
them in place of supported controller font characters. The prompt preparation
tools also combine these keys with the user's locally exported game font for
Exit, Skip and PC Display. Those composed game assets remain ignored.

The WASD symbol is composed during generation from `keyboard_w/a/s/d_outline`
source images, with W above A/S/D. Direction arrows, mouse movement and
middle-click remain distinct symbols. The original controller-font mapping
and native arrow-key tutorial validation are documented in
[`docs/keyboard-mouse.md`](../../docs/keyboard-mouse.md#direction-glyph-correction).
