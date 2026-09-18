// Offline entry point for testing exactly the patcher used by the native GPU.
#include "src/projection_precision.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>
int main(int argc, char** argv) {
  if (argc!=5 && argc!=9 && argc!=14) return 2;
  try {
    std::ifstream source(argv[3],std::ios::binary);
    if (!source) return 3;
    std::vector<uint8_t> binary{std::istreambuf_iterator<char>(source),{}};
    aot::ProjectionTranslationLayout layout{};
    if (argc>=9) layout={uint32_t(std::stoul(argv[5])),uint32_t(std::stoul(argv[6])),
                        uint32_t(std::stoul(argv[7])),uint32_t(std::stoul(argv[8]))};
    if (argc==14) {
      layout.automatic=true;layout.world=uint32_t(std::stoul(argv[9]));
      for (unsigned i=0;i<4;++i) layout.components[i]=uint32_t(std::stoul(argv[10+i]));
    }
    auto status=argc==5 ? aot::PatchProjection(std::stoull(argv[1],nullptr,16),std::stoull(argv[2],nullptr,16),binary) :
      aot::PatchTranslatedProjection(std::stoull(argv[1],nullptr,16),layout,binary);
    if (status!=aot::ProjectionPatchResult::Patched) {
      std::cerr << "Unchanged patch result " << static_cast<int>(status) << '\n';
      return status==aot::ProjectionPatchResult::Unrelated ? 4 : 5;
    }
    std::ofstream output(argv[4],std::ios::binary);
    output.write(reinterpret_cast<const char*>(binary.data()),binary.size());
    return output ? 0 : 6;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 7; }
}
