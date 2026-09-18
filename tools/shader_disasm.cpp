#include <bit>
#include <charconv>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <vector>
#include <xxhash.h>
#include <rex/cvar.h>
#include <rex/graphics/pipeline/shader/shader.h>

REXCVAR_DEFINE_STRING(dump_shaders, "", "GPU", "Unused by the offline disassembler");

int main(int argc, char** argv) {
  try {
    static_assert(std::endian::native == std::endian::little);
    if (argc != 3) throw std::runtime_error("Usage: aot_shader_disasm CACHE.xsh SHADER_HASH_HEX");
    const std::string_view hash_text(argv[2]);
    uint64_t wanted = 0;
    const auto parsed = std::from_chars(hash_text.data(), hash_text.data() + hash_text.size(), wanted, 16);
    if (parsed.ec != std::errc{} || parsed.ptr != hash_text.data() + hash_text.size())
      throw std::runtime_error("Shader hash must be hexadecimal without a prefix");
    std::ifstream input(argv[1], std::ios::binary);
    auto read = [&](auto* data, size_t size) {
      if (!input.read(reinterpret_cast<char*>(data), size))
        throw std::runtime_error("Truncated or unreadable shader cache");
    };
    uint32_t magic = 0, version = 0;
    read(&magic, 4); read(&version, 4);
    if (magic != 0x48534558 || std::byteswap(version) != 0x20201219)
      throw std::runtime_error("Unsupported XESH cache format");
    while (input.peek() != std::char_traits<char>::eof()) {
      uint64_t hash = 0;
      uint32_t count_and_type = 0;
      read(&hash, 8); read(&count_and_type, 4);
      const uint32_t count = count_and_type & 0x7FFFFFFF;
      if (!count || count > 0x100000) throw std::runtime_error("Invalid shader size");
      std::vector<uint32_t> words(count);
      read(words.data(), words.size() * sizeof(uint32_t));
      if (XXH3_64bits(words.data(), words.size() * sizeof(uint32_t)) != hash)
        throw std::runtime_error("Shader cache entry checksum mismatch");
      if (hash != wanted) continue;
      rex::graphics::Shader shader(static_cast<rex::graphics::xenos::ShaderType>(count_and_type >> 31),
          hash, words.data(), words.size(), std::endian::big);
      rex::string::StringBuffer scratch;
      shader.AnalyzeUcode(scratch);
      std::cout << shader.ucode_disassembly();
      return 0;
    }
    throw std::runtime_error("Shader hash not found in cache");
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
