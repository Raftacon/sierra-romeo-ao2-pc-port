#pragma once
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace aot {
struct TutorialTextResult {
  std::vector<uint8_t> bytes;
  size_t changed_lines = 0;
};

inline std::string NeutralTutorialLine(std::string_view line) {
  constexpr std::string_view tag = "<Fonts:00_fonts.Xbox360_18pt>";
  const auto equals = line.find('=');
  if (equals == line.npos || line.find(tag, equals) == line.npos) return std::string(line);
  const auto key = line.substr(0, equals);
  if (!(key.ends_with("_Buttons") || key.ends_with("_Tutorial") ||
        key.ends_with("_ButtonInfo") || key.ends_with("_Description"))) return std::string(line);
  std::string value(line.substr(equals + 1));
  auto replace = [&](std::string_view from, std::string_view to) {
    size_t pos = 0;
    while ((pos = value.find(from, pos)) != value.npos) {
      value.replace(pos, from.size(), to);
      pos += to.size();
    }
  };
  // Only replace control wording directly adjoining an existing glyph. Keep
  // actions, ordering, glyph tags, line breaks and unrelated prose intact.
  for (const auto& [from, to] : {
      std::pair{"Click left stick button ", "Press "},
      {"Pull and hold left trigger ", "Hold "},
      {"Pull the Left Trigger ", "Hold "},
      {"Pull the left trigger ", "Press "},
      {"Pull the right trigger ", "Press "},
      {"pull the right trigger ", "press "},
      {"Use right trigger ", "Press "},
      {"Use left trigger ", "Hold "},
      {"Use left stick ", "Use "},
      {"Use right stick ", "Use "},
      {"Right stick ", ""},
      {"left bumper ", ""}, {"right bumper ", ""},
      {"D-pad up ", ""}, {"D-pad down ", ""},
      {"D-pad left ", ""}, {"D-pad right ", ""}, {"D-pad ", ""},
      {"the BACK button ", ""}, {"Pull ", "Press "}}) {
    replace(std::string(from) + std::string(tag), std::string(to) + std::string(tag));
  }
  replace("Use the D-pad to control", "Use the directional controls to control");
  return std::string(line.substr(0, equals + 1)) + value;
}

inline std::optional<TutorialTextResult> PrepareTutorialText(std::span<const uint8_t> source) {
  if (source.size() < 4 || source.size() > 4 * 1024 * 1024) return std::nullopt;
  auto word = [&](size_t offset) {
    return uint32_t(source[offset]) << 24 | uint32_t(source[offset + 1]) << 16 |
           uint32_t(source[offset + 2]) << 8 | source[offset + 3];
  };
  // This coalesced format stores a count of strings: alternating path/content,
  // not a count of path/content pairs. Only the observed ANSI form is supported.
  const auto count = word(0);
  if (count > 1024 || count % 2) return std::nullopt;
  TutorialTextResult result{{source.begin(), source.begin() + 4}, 0};
  size_t cursor = 4;
  std::string path;
  for (uint32_t i = 0; i < count; ++i) {
    if (source.size() - cursor < 4) return std::nullopt;
    const auto begin = cursor;
    const auto length = word(cursor);
    cursor += 4;
    if (length > source.size() - cursor || (length && source[cursor + length - 1])) return std::nullopt;
    std::string text(reinterpret_cast<const char*>(source.data() + cursor), length ? length - 1 : 0);
    if (text.find('\0') != text.npos) return std::nullopt;
    cursor += length;
    std::string updated = text;
    if (!(i % 2)) path = text;
    else if (path == "..\\AO2Game\\Localization\\INT\\AO2Game.int") {
      updated.clear();
      std::string_view section;
      size_t offset = 0;
      while (offset < text.size()) {
        const auto newline = text.find('\n', offset);
        const auto end = newline == text.npos ? text.size() : newline;
        const auto line = std::string_view(text).substr(offset, end - offset);
        if (line.starts_with('[')) {
          section = line;
          if (section.ends_with('\r')) section.remove_suffix(1);
        }
        const auto replacement = section == "[AO2Character]" || section == "[AO2Partner]" || section == "[InGameMessages]"
            ? NeutralTutorialLine(line) : std::string(line);
        result.changed_lines += replacement != line;
        updated += replacement;
        if (newline != text.npos) updated += '\n';
        offset = end + 1;
      }
    }
    if (updated == text) result.bytes.insert(result.bytes.end(), source.begin() + begin, source.begin() + cursor);
    else {
      const auto size = uint32_t(updated.size() + 1);
      for (int shift : {24, 16, 8, 0}) result.bytes.push_back(uint8_t(size >> shift));
      result.bytes.insert(result.bytes.end(), updated.begin(), updated.end());
      result.bytes.push_back(0);
    }
  }
  if (cursor != source.size()) return std::nullopt;
  return result;
}
}  // namespace aot
