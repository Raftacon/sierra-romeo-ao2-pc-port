// Offline diagnostic helper using the same checksum implementation as the GPU
// translator. Reads a complete DXBC container and prints its checksum words.
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <vector>
#include "DXBCChecksum.h"
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::ifstream stream(argv[1], std::ios::binary);
  std::vector<unsigned char> bytes{std::istreambuf_iterator<char>(stream), {}};
  if (bytes.size() < 32 || bytes.size() > 1024 * 1024 ||
      bytes[0] != 'D' || bytes[1] != 'X' || bytes[2] != 'B' || bytes[3] != 'C') return 1;
  unsigned int checksum[4];
  CalculateDXBCChecksum(bytes.data(), static_cast<unsigned int>(bytes.size()), checksum);
  for (unsigned int word : checksum)
    std::cout << std::hex << std::setfill('0') << std::setw(8) << word;
  std::cout << '\n';
}
