#include "projection_precision.h"
#include "projection_snippet.h"
#include "projection_fma_snippet.h"
#include "thirdparty/dxbc/DXBCChecksum.h"
#include <algorithm>
#include <array>
#include <cstring>

namespace aot {
namespace {
using Words = std::vector<uint32_t>;
constexpr uint32_t FourCC(char a, char b, char c, char d) {
  return uint32_t(a) | uint32_t(b) << 8 | uint32_t(c) << 16 | uint32_t(d) << 24;
}
enum class WorldOrder { Xywz, Xyzw, Xwyz };
struct Layout {
  uint64_t guest, modification;
  std::array<uint32_t, 4> checksum;
  std::array<uint32_t, 4> native_checksum;
  uint32_t position, world, scratch, output, temps, words;
  uint32_t native_words;
  uint32_t system_constants = 0, float_constants = 2;
  WorldOrder world_order = WorldOrder::Xywz;
  bool inferred = false;
  std::array<uint32_t,4> components{};
  bool fused = false;
};
constexpr Layout layouts[] = {
  {0x480333F4AFCF0E1E, 0, {0x291a6398,0x5558f0dc,0x410edc16,0x7ce69087},
   {0x98867ca9,0xe8505ed0,0x892ac5af,0x29e505f9},14,5,16,0,23,7322,6257},
  {0x480333F4AFCF0E1E, 0x7F, {0xaade8145,0x90a1f3f4,0x8033efd9,0xaab718bc},
   {0xfee04a4d,0xf515363d,0x1d55543c,0xfaae4cc8},14,5,16,7,23,7472,6407},
  {0xD6E05D80EF7DEBF8, 0, {0xd634d5b2,0xc4b4896e,0x0e4352d5,0xe7dd5a5d},
   {0x2f5fa6ea,0x4a4fcfeb,0xb43d9858,0xee6207d3},10,1,12,0,19,3135,2621},
  // Non-aggro skinned depth pass: matches the equipment material's r5 xywz
  // world position, but runs separately when character transparency is active.
  {0xA7E5F6317B4316DB, 0, {}, {}, 14,5,16,0,23,0,0},
  // Decal projection is supported through translator metadata only. Its world
  // register uses ordinary xyzw, unlike the equipment programs' xywz ordering.
  {0xFB08EF4B31D5E686, 0, {}, {}, 12,1,14,6,21,0,0,0,2,WorldOrder::Xyzw},
  // Courtyard impact material: world components are x,w,y,z in guest r7.
  {0x6AF9B63098372D65, 0, {}, {}, 9,7,11,7,18,0,0,0,2,WorldOrder::Xwyz},
  // The courtyard wall's depth-writing color pass must agree with its prepass
  // and the impact projection. Correcting only the prepass is overwritten.
  {0x813A25A25223C7F5, 0, {}, {}, 2,1,4,0,11,0,0,0,2,WorldOrder::Xywz},
  {0x5E20CD3F81F88801, 0, {}, {}, 9,7,11,6,18,0,0,0,2,WorldOrder::Xwyz},
  // Static vertex-color material used by the alley palm; shares the prepass.
  {0x91B258F7198B1CE4, 0, {}, {}, 11,9,13,8,20,0,0,0,2,WorldOrder::Xwyz},
  // Unlit, destination-modulating static material; shares the static prepass.
  {0xD66D9932DC2EC8D8, 0, {}, {}, 5,4,7,3,14,0,0,0,2,WorldOrder::Xwyz},
  // Additional static materials sharing the wall prepass. Runtime support
  // remains gated with the complete static group, never enabled independently.
  {0x3306D6C23B238BE6, 0, {}, {}, 8,6,10,5,17,0,0,0,2,WorldOrder::Xwyz},
  {0xAC2A17351535ED19, 0, {}, {}, 11,5,13,7,20,0,0,0,2,WorldOrder::Xyzw},
  // Later static lighting passes, using an ordinary xyzw world position.
  {0x807B2A09A19C3B15, 0, {}, {}, 10,4,12,6,19,0,0,0,2,WorldOrder::Xyzw},
  {0x494DCD69B7BA177C, 0, {}, {}, 11,4,13,6,20,0,0,0,2,WorldOrder::Xyzw},
  // Vertex-lit static material, also visible in the partner camera. Guest r10
  // is reused after oPos, so retain the world projection at that export.
  {0x87C093F46637D13F, 0, {}, {}, 12,10,14,9,21,0,0,0,2,WorldOrder::Xwyz},
  {0xD66FF6280606248C, 0, {}, {}, 9,7,11,7,18,0,0,0,2,WorldOrder::Xwyz},
  {0xB22EF913802807F8, 0, {}, {}, 9,7,11,6,18,0,0,0,2,WorldOrder::Xwyz},
  // Courtyard wreck material, distinct from the other alley car's program.
  {0x998F2B953D9B74FD, 0, {}, {}, 12,10,14,9,21,0,0,0,2,WorldOrder::Xwyz},
  // Remaining courtyard prepass user, with world components in xywz order.
  {0xCB7E063397190431, 0, {}, {}, 10,8,12,7,19,0,0},
};
bool Boundaries(const Words& words, std::vector<size_t>& offsets) {
  if (words.size() < 2 || words[1] != words.size()) return false;
  size_t at = 2;
  while (at < words.size()) {
    const uint32_t opcode = words[at] & 2047;
    if (opcode == 53 && at + 1 == words.size()) return false;
    const size_t count = opcode == 53 ? words[at+1] : (words[at] >> 24) & 127;
    if (!count || count > words.size() - at) return false;
    offsets.push_back(at);
    at += count;
  }
  return at == words.size();
}
void Append(Words& dst, std::initializer_list<uint32_t> src) {
  dst.insert(dst.end(), src.begin(), src.end());
}
void Guard(Words& dst, uint32_t temporary, bool equal, uint32_t system_constants=0) {
  // Only the captured, verified clip-space mode is corrected. Use a new temp
  // so even unsupported modes retain every original temporary value.
  Append(dst, {0x09000001,0x00100012,temporary,0x0030800A,system_constants,0,0,0x00004001,14});
  Append(dst, {equal ? 0x07000020u : 0x07000027u,
               0x00100012,temporary,0x0010000A,temporary,0x00004001,8});
  Append(dst, {0x0304001F,0x0010000A,temporary}); // if_nz
}
bool Body(const Layout& layout, Words& body) {
  static_assert(projection_extra_temps == projection_fma_extra_temps);
  Words snippet = layout.fused ?
      Words(std::begin(projection_fma_snippet), std::end(projection_fma_snippet)) :
      Words(std::begin(projection_snippet), std::end(projection_snippet));
  std::vector<size_t> offsets;
  if (!Boundaries(snippet, offsets)) return false;
  for (size_t at : offsets) {
    const uint32_t opcode = snippet[at] & 2047;
    if ((opcode >= 88 && opcode <= 106) || opcode == 62) continue;
    if (opcode == 53) return false;
    Words op(snippet.begin()+at, snippet.begin()+at+((snippet[at]>>24)&127));
    size_t cursor = 1;
    while (cursor < op.size()) {
      const size_t token_at = cursor++;
      const uint32_t token = op[token_at], type = (token >> 12) & 255;
      if (token >> 31) {
        bool extended;
        do {
          if (cursor >= op.size()) return false;
          extended = (op[cursor++] >> 31) != 0;
        } while (extended);
      }
      const size_t dimensions = (token >> 20) & 3;
      if (dimensions > op.size()-cursor) return false;
      for (size_t i=0; i<dimensions; ++i)
        if ((token >> (22+3*i)) & 7) return false;
      if (type == 0) {
        if (dimensions != 1 || op[cursor] >= projection_extra_temps) return false;
        op[cursor] += layout.temps;
      } else if (type == 1 || type == 2) {
        if (dimensions != 1 || op[cursor] != 0) return false;
        op[token_at] = token & ~(255u << 12);
        if (type == 1 && (layout.inferred || layout.world_order != WorldOrder::Xywz)) {
          // The shared snippet expects xywz input. Remap only its input reads;
          // do not modify the guest register or material interpolators.
          if ((token & 3) != 2 || ((token >> 2) & 3) != 1) return false;
          uint32_t swizzle=0;
          for (unsigned lane=0; lane<4; ++lane) {
            const unsigned component=(token>>(4+2*lane))&3;
            const unsigned mapped = layout.inferred ? layout.components[component>=2 ? component^1 : component] : layout.world_order == WorldOrder::Xyzw
                ? (component>=2 ? component^1 : component)
                : (component==1 ? 2 : component==2 ? 1 : component);
            swizzle |= mapped << (4+2*lane);
          }
          op[token_at]=(op[token_at]&~0xFF0u)|swizzle;
        }
        op[cursor] = type == 1 ? layout.world : layout.position;
      } else if (type == 8) {
        if (dimensions != 3 || op[cursor+1] > 1) return false;
        op[cursor] = op[cursor+1] == 0 ? layout.system_constants : layout.float_constants;
      } else return false;
      cursor += dimensions;
    }
    body.insert(body.end(), op.begin(), op.end());
  }
  return !body.empty();
}
}

static ProjectionPatchResult PatchProjectionImpl(uint64_t guest, uint64_t modification,
    std::vector<uint8_t>& binary, const ProjectionTranslationLayout* translated) {
  const auto first=std::find_if(std::begin(layouts),std::end(layouts),
      [guest](const Layout& item) { return item.guest==guest; });
  const bool inferred=translated && translated->automatic;
  if (inferred ? translated->world==UINT32_MAX : first==std::end(layouts)) return ProjectionPatchResult::Unrelated;
  const auto unsupported = ProjectionPatchResult::Unsupported;
  if (!inferred && first->world_order != WorldOrder::Xywz && !translated) return unsupported;
  const Layout* layout = nullptr;
  for (const auto& candidate : layouts)
    if (candidate.guest == guest && candidate.modification == modification) layout = &candidate;
  Layout generated = inferred ? Layout{} : *first;
  if (inferred) {
    if (translated->world>=(translated->captured ? 4096u : 64u)) return unsupported;
    if (translated->captured && (translated->captured_mask!=15 ||
        translated->world==translated->position || translated->world==translated->vector_result ||
        translated->components!=std::array<uint32_t,4>{0,1,2,3})) return unsupported;
    unsigned mask=0;
    for (auto c:translated->components) {
      if (c>5 || (c<4 && (mask&(1u<<c)))) return unsupported;
      if (c<4) mask|=1u<<c;
    }
    generated.world=translated->world;
    generated.components=translated->components;
    generated.inferred=true;
  }
  if (translated) {
    if (translated->position >= 4096 || translated->vector_result >= 4096 ||
        translated->system_constants >= 32 || translated->float_constants >= 32)
      return unsupported;
    generated.position = translated->position;
    generated.scratch = translated->vector_result;
    generated.system_constants = translated->system_constants;
    generated.float_constants = translated->float_constants;
    generated.fused = translated->fused;
    layout = &generated;
  }
  if (!layout || binary.size() < 32 || binary.size() > 1024*1024 || binary.size()%4) return unsupported;
  Words container(binary.size()/4);
  std::memcpy(container.data(), binary.data(), binary.size());
  if (container[0] != FourCC('D','X','B','C') || container[6] != binary.size() ||
      container[7] > 32 || 8+container[7] > container.size()) return unsupported;
  // Full generated-container identity, not just an instruction-pattern guess.
  std::array<uint32_t,4> checksum;
  CalculateDXBCChecksum(binary.data(), static_cast<unsigned>(binary.size()), checksum.data());
  // RenderDoc enables embedded guest-instruction comments. Native versions
  // have identical executable tokens, with those comment records omitted.
  if ((!translated && checksum != layout->checksum && checksum != layout->native_checksum) ||
      !std::equal(checksum.begin(), checksum.end(), container.begin()+1)) return unsupported;
  std::vector<Words> parts;
  size_t shader_part = SIZE_MAX, feature_part = SIZE_MAX;
  for (size_t i=0; i<container[7]; ++i) {
    const size_t offset = container[8+i]/4;
    if (container[8+i]%4 || offset > container.size()-2 || container[offset+1]%4) return unsupported;
    const size_t size = container[offset+1]/4+2;
    if (size > container.size()-offset) return unsupported;
    parts.emplace_back(container.begin()+offset, container.begin()+offset+size);
    if (container[offset] == FourCC('S','H','E','X')) shader_part = i;
    if (container[offset] == FourCC('S','F','I','0')) feature_part = i;
  }
  if (shader_part == SIZE_MAX || feature_part == SIZE_MAX || parts[feature_part].size()!=4) return unsupported;
  if (translated && ((parts[feature_part][2]&projection_feature_flags[0]) ||
                     (parts[feature_part][3]&projection_feature_flags[1]))) return unsupported;
  Words words(parts[shader_part].begin()+2, parts[shader_part].end());
  std::vector<size_t> offsets;
  const auto expected_words=checksum==layout->native_checksum ? layout->native_words : layout->words;
  if ((!translated && words.size()!=expected_words) || words.size()<86 || !Boundaries(words, offsets)) return unsupported;
  const Words anchor{0x05000036,0x001000F2,layout->position,0x00100E46,layout->scratch};
  size_t anchor_at=SIZE_MAX, temps_at=SIZE_MAX, globals_at=SIZE_MAX;
  for (size_t at : offsets) {
    if (words[at] == 0x02000068) temps_at=at;
    if ((words[at]&2047) == 106) globals_at=at;
    if (words.size()-at >= anchor.size() && std::equal(anchor.begin(),anchor.end(),words.begin()+at)) {
      if (anchor_at != SIZE_MAX) return unsupported;
      anchor_at=at;
    }
  }
  if (translated && temps_at!=SIZE_MAX) generated.temps = words[temps_at+1];
  if (layout->temps > 4088) return unsupported;
  if (translated) {
    // Leave guest clip position and the entire host epilogue intact. In
    // particular, user clip distances must consume guest-space coordinates.
    // Replace only the final viewport-adjusted position, before vertex-kill
    // handling, using a separate value saved at the original position export.
    const Words viewport_offset{0x0B000032,0x00100072,layout->position,
      0x00308246,layout->system_constants,0,9,0x00100FF6,layout->position,
      0x00100246,layout->position};
    size_t viewport_at=SIZE_MAX;
    for (size_t at : offsets) {
      if (words.size()-at >= viewport_offset.size() &&
          std::equal(viewport_offset.begin(),viewport_offset.end(),words.begin()+at)) {
        if (viewport_at!=SIZE_MAX) return unsupported;
        viewport_at=at;
      }
    }
    if (anchor_at==SIZE_MAX || temps_at==SIZE_MAX || globals_at==SIZE_MAX ||
        viewport_at==SIZE_MAX || anchor_at+5>viewport_at ||
        layout->position>=layout->temps || layout->scratch>=layout->temps ||
        layout->world>=layout->temps) return unsupported;
    const uint32_t saved_position=layout->temps+projection_extra_temps;
    const uint32_t guard_temp=saved_position+1;
    Layout body_layout=*layout;
    body_layout.position=saved_position;
    Words body;
    Guard(body,guard_temp,true,layout->system_constants);
    const bool literal_world=layout->inferred && std::any_of(
        layout->components.begin(),layout->components.end(),[](auto c){return c>=4;});
    if (literal_world) {
      // Assemble only the injected calculation's input. The guest registers,
      // their original arithmetic and material interpolators stay intact.
      body_layout.world=guard_temp+1;
      body_layout.components={0,1,2,3};
      for (unsigned row=0;row<4;++row) {
        const uint32_t c=layout->components[row];
        const uint32_t destination=0x00100002u | (1u<<(4+row));
        if (c>=4) Append(body,{0x05000036,destination,body_layout.world,
                              0x00004001,c==5 ? 0x3f800000u : 0u});
        else Append(body,{0x05000036,destination,body_layout.world,
                          0x0010000Au|(c<<4),layout->world});
      }
    }
    if (!Body(body_layout,body)) return unsupported;
    body.push_back(0x01000015);
    Words replacement;
    Guard(replacement,guard_temp,true,layout->system_constants);
    Append(replacement,{0x05000036,0x001000F2,layout->position,0x00100E46,saved_position,0x01000015});
    words.insert(words.begin()+viewport_at+viewport_offset.size(),replacement.begin(),replacement.end());
    words.insert(words.begin()+anchor_at+5,body.begin(),body.end());
    words[temps_at+1]=guard_temp+1+uint32_t(literal_world);
  } else {
  const size_t epilogue = words.size()-86;
  const Words prefix{0x09000001,0x00100012,layout->scratch,0x0030800A,0,0,0,0x00004001,8};
  const Words tail{0x05000036,0x001020F2,layout->output,0x00100E46,layout->position,0x0100003E};
  if (anchor_at==SIZE_MAX || temps_at==SIZE_MAX || globals_at==SIZE_MAX ||
      words[temps_at+1]!=layout->temps || anchor_at+5>epilogue ||
      !std::equal(prefix.begin(),prefix.end(),words.begin()+epilogue) ||
      !std::equal(tail.begin(),tail.end(),words.end()-6)) return unsupported;
  Words body;
  const uint32_t guard_temp=layout->temps+projection_extra_temps;
  Guard(body, guard_temp, true);
  if (!Body(*layout, body)) return unsupported;
  body.push_back(0x01000015); // endif
  Words guarded_epilogue;
  Guard(guarded_epilogue, guard_temp, false);
  guarded_epilogue.insert(guarded_epilogue.end(), words.begin()+epilogue,words.end()-6);
  guarded_epilogue.push_back(0x01000015);
  words.erase(words.begin()+epilogue, words.end()-6);
  words.insert(words.begin()+epilogue, guarded_epilogue.begin(), guarded_epilogue.end());
  words.insert(words.begin()+anchor_at+5, body.begin(), body.end());
  words[temps_at+1]=guard_temp+1;
  }
  // Import the capability required by the inserted FP64 instructions only.
  // The standalone snippet also permits arithmetic refactoring (bit 11).
  // Enabling that for the guest program can change unrelated FP32 outputs.
  // D3D11_SB_GLOBAL_FLAG_ENABLE_DOUBLE_PRECISION_FLOAT_OPS is bit 12.
  constexpr uint32_t double_precision_flag = 1u << 12;
  static_assert((projection_global_flags & double_precision_flag) != 0);
  words[globals_at] |= double_precision_flag;
  constexpr uint32_t double_extensions_flag = 1u << 17;
  static_assert((projection_fma_global_flags & double_extensions_flag) != 0);
  if (layout->fused) words[globals_at] |= double_extensions_flag;
  words[1]=static_cast<uint32_t>(words.size());
  parts[shader_part]={FourCC('S','H','E','X'),static_cast<uint32_t>(words.size()*4)};
  parts[shader_part].insert(parts[shader_part].end(), words.begin(),words.end());
  const auto* features=layout->fused ? projection_fma_feature_flags : projection_feature_flags;
  parts[feature_part][2]|=features[0];
  parts[feature_part][3]|=features[1];
  Words result(container.begin(),container.begin()+8+container[7]);
  for (size_t i=0; i<parts.size(); ++i) {
    result[8+i]=static_cast<uint32_t>(result.size()*4);
    result.insert(result.end(),parts[i].begin(),parts[i].end());
  }
  result[6]=static_cast<uint32_t>(result.size()*4);
  std::vector<uint8_t> output(result.size()*4);
  std::memcpy(output.data(),result.data(),output.size());
  CalculateDXBCChecksum(output.data(),static_cast<unsigned>(output.size()),checksum.data());
  std::memcpy(output.data()+4,checksum.data(),sizeof(checksum));
  binary.swap(output);
  return ProjectionPatchResult::Patched;
}
ProjectionPatchResult PatchProjection(uint64_t guest, uint64_t modification,
                                      std::vector<uint8_t>& binary) {
  return PatchProjectionImpl(guest,modification,binary,nullptr);
}
ProjectionPatchResult PatchTranslatedProjection(uint64_t guest,
    const ProjectionTranslationLayout& layout, std::vector<uint8_t>& binary) {
  return PatchProjectionImpl(guest,0,binary,&layout);
}
}
