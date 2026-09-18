#include "src/tutorial_text.h"
#include <cstdlib>
#include <fstream>
#include <iostream>

using Bytes = std::vector<uint8_t>;
void word(Bytes& data, uint32_t value) {
  for (int shift : {24, 16, 8, 0}) data.push_back(uint8_t(value >> shift));
}
Bytes package(const std::vector<std::string>& strings) {
  Bytes data;
  word(data, uint32_t(strings.size()));
  for (const auto& text : strings) {
    word(data, uint32_t(text.size() + 1));
    data.insert(data.end(), text.begin(), text.end());
    data.push_back(0);
  }
  return data;
}
void require(bool ok, const char* message) {
  if (!ok) { std::cerr << message << '\n'; std::exit(1); }
}
int main(int argc, char** argv) {
  const std::string file = "..\\AO2Game\\Localization\\INT\\AO2Game.int";
  const std::string glyph = "<Fonts:00_fonts.Xbox360_18pt>j<Fonts:/>";
  const std::string line = "DuckDown_Buttons=Click left stick button " + glyph + " to duck down.\r\n";
  const std::string neutral = "DuckDown_Buttons=Press " + glyph + " to duck down.\r\n";
  const std::string tail = "DuckDown_Buttons_PS3=unmodified\r\n;comment\r\nName=caf\xe9\r\n";
  auto original = package({"unrelated.int", line, file, "[InGameMessages]\r\n" + line + tail});
  const auto before = original;
  auto result = aot::PrepareTutorialText(original);
  require(result && result->changed_lines == 1, "Expected one scoped text edit");
  require(result->bytes == package({"unrelated.int", line, file, "[InGameMessages]\r\n" + neutral + tail}),
          "Rebuilt lengths or unrelated bytes differ from independent expected package");
  require(original == before, "Source package was modified");
  auto second = aot::PrepareTutorialText(result->bytes);
  require(second && !second->changed_lines && second->bytes == result->bytes, "Rewrite is not idempotent");
  auto other = package({file, "[Unrelated]\n" + line});
  auto unchanged = aot::PrepareTutorialText(other);
  require(unchanged && unchanged->bytes == other, "Changed an unrelated section");
  require(aot::NeutralTutorialLine("PullUp_Buttons=Pull Up your partner " + glyph) ==
          "PullUp_Buttons=Pull Up your partner " + glyph, "Changed unrelated action prose");
  for (size_t size = 0; size < original.size(); ++size)
    require(!aot::PrepareTutorialText(std::span(original).first(size)), "Accepted truncated package");
  auto malformed = original; malformed.push_back(0);
  require(!aot::PrepareTutorialText(malformed), "Accepted trailing bytes");
  malformed = original; malformed[3] = 3;
  require(!aot::PrepareTutorialText(malformed), "Accepted odd string count");
  malformed = original; malformed[4] = 0xff;
  require(!aot::PrepareTutorialText(malformed), "Accepted unsupported wide/oversized string");
  malformed = original; malformed[8] = 0;
  require(!aot::PrepareTutorialText(malformed), "Accepted embedded NUL");
  malformed = original; malformed.back() = 1;
  require(!aot::PrepareTutorialText(malformed), "Accepted unterminated string");
  if (argc == 3) {
    std::ifstream input(argv[1], std::ios::binary);
    Bytes data{std::istreambuf_iterator<char>(input), {}};
    auto patched = aot::PrepareTutorialText(data);
    require(patched && patched->changed_lines, "No retail text edits");
    std::ofstream output(argv[2], std::ios::binary);
    output.write(reinterpret_cast<const char*>(patched->bytes.data()), patched->bytes.size());
    require(bool(output), "Could not write diagnostic package");
    std::cout << "Retail changed lines: " << patched->changed_lines << '\n';
  }
  std::cout << "Localization bounds, byte preservation, scope and idempotence passed\n";
}
