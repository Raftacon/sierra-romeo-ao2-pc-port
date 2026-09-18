#pragma once

#include <cstdint>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <filesystem>
#include <chrono>

namespace aot {
struct PadState {
  uint16_t buttons = 0;
  uint8_t lt = 0, rt = 0;
  int16_t lx = 0, ly = 0, rx = 0, ry = 0;
};

inline PadState ReadPad(std::istream& row) {
  unsigned buttons, lt, rt;
  int lx, ly, rx, ry;
  std::string extra;
  if (!(row >> std::hex >> buttons >> std::dec >> lt >> rt >> lx >> ly >> rx >> ry) ||
      row >> extra || buttons > 65535 || lt > 255 || rt > 255 ||
      lx < -32768 || lx > 32767 || ly < -32768 || ly > 32767 ||
      rx < -32768 || rx > 32767 || ry < -32768 || ry > 32767) {
    throw std::runtime_error("Invalid controller state");
  }
  return {static_cast<uint16_t>(buttons), static_cast<uint8_t>(lt), static_cast<uint8_t>(rt),
          static_cast<int16_t>(lx), static_cast<int16_t>(ly), static_cast<int16_t>(rx), static_cast<int16_t>(ry)};
}

class InputScript {
 public:
  explicit InputScript(const std::string& path) {
    if (path.empty()) return;
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Input script could not be opened");
    std::string line;
    uint64_t previous_end = 0;
    while (std::getline(input, line)) {
      if (line.find_first_not_of(" \t\r") == std::string::npos || line[0] == '#') continue;
      std::istringstream row(line);
      Event e{};
      if (!(row >> e.start >> e.duration) || !e.duration ||
          e.start < previous_end || e.start > 86400000 || e.duration > 86400000) {
        throw std::runtime_error("Invalid or overlapping input script timing");
      }
      e.pad = ReadPad(row);
      previous_end = e.start + e.duration;
      events_.push_back(e);
    }
  }
  PadState Sample(uint64_t elapsed) const {
    for (const auto& e : events_)
      if (elapsed >= e.start && elapsed - e.start < e.duration) return e.pad;
    return {};
  }
 private:
  struct Event { uint64_t start, duration; PadState pad; };
  std::vector<Event> events_;
};

// Live states expire after 1.5 seconds; a test controller must refresh held input.
inline PadState ReadLivePad(const std::string& path) {
  std::error_code error;
  auto written = std::filesystem::last_write_time(path, error);
  if (error || std::filesystem::file_time_type::clock::now() - written > std::chrono::milliseconds(1500)) return {};
  std::ifstream input(path);
  if (!input) return {};
  try { return ReadPad(input); } catch (const std::runtime_error&) { return {}; }
}
}  // namespace aot
