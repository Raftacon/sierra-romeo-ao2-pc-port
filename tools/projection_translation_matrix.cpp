// Translate real cached guest programs through the pinned compiler, without
// launching the game or a GPU device. No retail bytes are checked into source.
#include "src/projection_precision_runtime.h"
#include "src/projection_analysis.h"
#include <bit>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <d3dcompiler.h>
#include <xxhash.h>
#include <rex/graphics/pipeline/shader/dxbc_translator.h>

namespace {
std::vector<uint8_t> original;
aot::ProjectionTranslationLayout captured_layout{};
unsigned patched_count=0;
bool automatic_mode=false;
bool snapshots_mode=false;
bool fused_mode=false;
bool expected_match=true;
struct ExecSurvey final : rex::graphics::ShaderTranslator {
  struct Block { rex::graphics::ParsedExecInstruction instruction; bool position=false; };
  std::vector<Block> blocks;
  void Run(rex::graphics::Shader& shader) {
    struct Translation final : rex::graphics::Shader::Translation {
      explicit Translation(rex::graphics::Shader& s) : rex::graphics::Shader::Translation(s,0) {}
    } translation(shader);
    if (!TranslateAnalyzedShader(translation)) throw std::runtime_error("Flow survey failed");
  }
  void ProcessExecInstructionBegin(const rex::graphics::ParsedExecInstruction& i) override {
    blocks.push_back({i});
  }
  void ProcessAluInstruction(const rex::graphics::ParsedAluInstruction& i,uint8_t) override {
    using rex::graphics::InstructionStorageTarget;
    for (const auto* d : {&i.scalar_result,&i.vector_and_constant_result})
      if (d->storage_target==InstructionStorageTarget::kPosition && d->GetUsedWriteMask())
        blocks.back().position=true;
  }
};
unsigned CheckLiteralSources(rex::graphics::Shader& shader,
    const aot::ProjectionSource& source,const ExecSurvey& survey,std::ostream& report) {
  using namespace rex::graphics;
  if (std::none_of(source.components.begin(),source.components.end(),[](auto c){return c>=4;})) return 0;
  unsigned checked=0;
  auto analyze=[&](const std::vector<uint32_t>& code) {
    Shader changed(xenos::ShaderType::kVertex,XXH3_64bits(code.data(),code.size()*4),
                   code.data(),code.size(),std::endian::native);
    rex::string::StringBuffer scratch;changed.AnalyzeUcode(scratch);
    return aot::AnalyzeProjection(changed);
  };
  for (const auto& block:survey.blocks) {
    uint32_t sequence=block.instruction.sequence;
    for (uint32_t offset=block.instruction.instruction_address;
         offset<block.instruction.instruction_address+block.instruction.instruction_count;
         ++offset,sequence>>=2) {
      const auto code=shader.ucode_data();
      if (!(sequence&1) || (code.at(offset*3)&31)!=uint32_t(ucode::FetchOpcode::kVertexFetch)) continue;
      for (unsigned lane=0;lane<4;++lane) {
        const auto word=offset*3+1,shift=lane*3;
        if (((code.at(word)>>shift)&7)!=5) continue;
        auto fetched=code;fetched[word]&=~(7u<<shift);
        if (analyze(fetched).world!=UINT32_MAX) continue; // unrelated literal
        for (unsigned variant=0;variant<5;++variant) {
          auto changed=code;
          if (variant==0) changed[word]&=~(7u<<shift); // fetched x, later overwritten
          if (variant==1) changed[word]=(changed[word]&~(7u<<shift))|(4u<<shift); // literal zero
          if (variant==2) changed[word]|=7u<<shift; // keep previous, unproven value
          if (variant==3) changed[word]|=1u<<31; // predicated fetch
          if (variant==4) changed[offset*3]|=1u<<18; // relative destination
          const auto proof=analyze(changed);
          const bool accepted=proof.world!=UINT32_MAX;
          report << std::hex << shader.ucode_data_hash() << std::dec << '\t'
                 << offset << '\t' << lane << '\t' << variant << '\t' << accepted << '\n';
          if (accepted!=(variant==1)) throw std::runtime_error("Literal-source mutation accepted an unproven source");
          if (accepted) {
            unsigned differences=0;
            for (unsigned row=0;row<4;++row) if (proof.components[row]!=source.components[row]) {
              if (proof.components[row]!=4 || source.components[row]!=5)
                throw std::runtime_error("Literal-zero proof changed unrelated source lanes");
              ++differences;
            }
            if (!differences || proof.world!=source.world)
              throw std::runtime_error("Literal-zero proof lost source correspondence");
          }
          ++checked;
        }
      }
    }
  }
  if (!checked) throw std::runtime_error("Literal proof lacks adversarial source coverage");
  return checked;
}
std::string Hex(uint64_t value) {
  std::ostringstream text;
  text << std::uppercase << std::hex << std::setw(16) << std::setfill('0') << value;
  return text.str();
}
void CheckDisassembly(const std::vector<uint8_t>& bytes) {
  ID3DBlob* blob=nullptr;
  const auto hr=D3DDisassemble(bytes.data(),bytes.size(),0,nullptr,&blob);
  if (FAILED(hr) || !blob) throw std::runtime_error("D3DDisassemble rejected translated binary");
  blob->Release();
}
uint32_t GlobalFlags(const std::vector<uint8_t>& bytes) {
  const auto word=[&](size_t offset) {
    if (offset>bytes.size() || bytes.size()-offset<4) throw std::runtime_error("Short DXBC read");
    uint32_t value;std::memcpy(&value,bytes.data()+offset,4);return value;
  };
  for (uint32_t i=0;i<word(28);++i) {
    const size_t part=word(32+4*i);
    if (word(part)!=0x58454853 && word(part)!=0x52444853) continue;
    const size_t end=part+8+word(part+4);
    uint32_t flags=0;unsigned found=0;
    for (size_t at=part+16;at<end;) {
      const uint32_t token=word(at),opcode=token&2047;
      const size_t length=opcode==53 ? word(at+4) : (token>>24)&127;
      if (!length || length>(end-at)/4) throw std::runtime_error("Invalid shader boundary");
      if (opcode==106) {flags=token;++found;}
      at+=length*4;
    }
    if (found!=1) throw std::runtime_error("Expected one global declaration");
    return flags;
  }
  throw std::runtime_error("Missing shader chunk");
}
void Save(const std::filesystem::path& path,const std::vector<uint8_t>& bytes) {
  std::ofstream out(path,std::ios::binary);
  out.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
  if (!out) throw std::runtime_error("Could not save shader");
}
}
namespace aot {
bool ProjectionPrecisionEnabled() { return true; }
bool AutomaticProjectionPrecisionEnabled() { return automatic_mode; }
bool ProjectionSourceSnapshotsEnabled() { return snapshots_mode; }
void TranslateProjectionPrecision(uint64_t guest,uint64_t,
    const ProjectionTranslationLayout& layout,std::vector<uint8_t>& binary) {
  original=binary;
  captured_layout=layout;
  captured_layout.fused=fused_mode;
  const auto result=PatchTranslatedProjection(guest,captured_layout,binary);
  if (!expected_match) {
    if (result!=ProjectionPatchResult::Unrelated || binary!=original)
      throw std::runtime_error("Unrecognized projection was modified");
    ++patched_count;
    return;
  }
  if (result!=ProjectionPatchResult::Patched)
    throw std::runtime_error("Projection patch failed for " + Hex(guest));
  ++patched_count;
}
}
int main(int argc,char** argv) {
  try {
    if (argc>3 && std::string(argv[argc-1])=="--fma") { fused_mode=true; --argc; }
    if (argc!=3 && argc!=4) throw std::runtime_error("Usage: aot_projection_matrix CACHE.xsh NEW_OUTPUT_DIRECTORY [--audit|--automatic|--flow-tests|--snapshots|--snapshot-flow-tests] [--fma]");
    const std::string mode=argc==4 ? argv[3] : "";
    snapshots_mode=mode=="--snapshots" || mode=="--snapshot-flow-tests";
    const bool flow_tests=mode=="--flow-tests" || mode=="--snapshot-flow-tests";
    const bool audit=flow_tests || (argc==4 && std::string(argv[3])=="--audit");
    automatic_mode=mode=="--automatic" || mode=="--snapshots";
    if (argc==4 && !audit && !automatic_mode) throw std::runtime_error("Unknown mode");
    const std::filesystem::path output(argv[2]);
    if (!std::filesystem::create_directory(output)) throw std::runtime_error("Require new output directory");
    std::ifstream input(argv[1],std::ios::binary);
    auto read=[&](auto* dest,size_t size) {
      if (!input.read(reinterpret_cast<char*>(dest),size)) throw std::runtime_error("Truncated shader cache");
    };
    uint32_t magic,version;read(&magic,4);read(&version,4);
    if (magic!=0x48534558 || std::byteswap(version)!=0x20201219) throw std::runtime_error("Unknown cache format");
    std::ofstream report(output/"translations.tsv");
    report << "guest\tmodification\tbindless\trov\tposition\tvector_result\tsystem_constants\tfloat_constants\tbefore_xxh3\tafter_xxh3\tguest_before_xxh3\tcaptured\n";
    std::ofstream capture_tests(output/"capture-tests.tsv");
    capture_tests << "guest\tmodification\tbindless\trov\tinvalid_layout\n";
    unsigned capture_cases=0, control_translations=0;
    std::set<uint64_t> found;
    std::ofstream coverage(output/"coverage.tsv");
    coverage << "guest\tworld\tcomponents\tinterpolators\tcaptured\tcaptures\n";
    std::ofstream flow(output/"flow-tests.tsv");
    flow << "guest\tcf_index\topcode\tposition_block\texpected_recognition\tactual_recognition\n";
    unsigned flow_cases=0, conditional_programs=0;
    unsigned literal_cases=0;
    std::ofstream literals(output/"literal-tests.tsv");
    literals << "guest\tinstruction\tlane\tvariant\taccepted\n";
    unsigned total_vertex=0, recognized=0;
    while (input.peek()!=std::char_traits<char>::eof()) {
      uint64_t guest;uint32_t type_count;
      read(&guest,8);read(&type_count,4);
      uint32_t count=type_count&0x7FFFFFFF;
      if (!count || count>0x100000) throw std::runtime_error("Unbounded guest shader");
      std::vector<uint32_t> words(count);read(words.data(),count*4);
      if (XXH3_64bits(words.data(),count*4)!=guest) throw std::runtime_error("Guest checksum mismatch");
      if (!(type_count>>31)) {
        using namespace rex::graphics;
        Shader candidate(xenos::ShaderType::kVertex,guest,words.data(),words.size(),std::endian::big);
        rex::string::StringBuffer scratch;candidate.AnalyzeUcode(scratch);
        const auto source=aot::AnalyzeProjection(candidate,snapshots_mode);
        ++total_vertex;
        if (source.world!=UINT32_MAX) ++recognized;
        coverage << Hex(guest) << '\t' << source.world << '\t';
        for (auto c:source.components) coverage << c;
        coverage << '\t' << candidate.writes_interpolators() << '\t' << source.captured << '\t';
        if (source.captured) for (const auto& c:source.captures)
          coverage << c.alu << ':' << c.reg << ':' << c.component << ',';
        coverage << '\n';
        if (flow_tests && source.world!=UINT32_MAX) {
          ExecSurvey survey;survey.Run(candidate);
          literal_cases+=CheckLiteralSources(candidate,source,survey,literals);
          const auto pos=std::find_if(survey.blocks.begin(),survey.blocks.end(),[](const auto& b){return b.position;});
          if (pos==survey.blocks.end()) throw std::runtime_error("Recognized shader lacks surveyed export");
          const auto export_cf=pos->instruction.dword_index;
          bool has_conditional=false;
          for (const auto& block:survey.blocks) {
            const auto cf=block.instruction.dword_index;
            has_conditional|=block.instruction.type!=ParsedExecInstruction::Type::kUnconditional;
            // Exercise boolean and predicate-dependent termination at every
            // exec block, and conditional nonterminal position exports.
            std::vector<uint32_t> opcodes={4,6,14};
            if (block.position) { opcodes.push_back(3);opcodes.push_back(5);opcodes.push_back(13); }
            for (auto opcode:opcodes) {
              auto altered=candidate.ucode_data();
              const uint32_t word=(cf/2)*3+((cf&1)?2:1), shift=(cf&1)?28:12;
              altered.at(word)=(altered.at(word)&~(15u<<shift))|(opcode<<shift);
              Shader changed(xenos::ShaderType::kVertex,XXH3_64bits(altered.data(),altered.size()*4),
                             altered.data(),altered.size(),std::endian::native);
              rex::string::StringBuffer changed_scratch;changed.AnalyzeUcode(changed_scratch);
              const auto proof=aot::AnalyzeProjection(changed,snapshots_mode);
              const bool expected=cf>export_cf, actual=proof.world!=UINT32_MAX;
              flow << Hex(guest) << '\t' << cf << '\t' << opcode << '\t' << block.position
                   << '\t' << expected << '\t' << actual << '\n';
              if (actual!=expected || (actual && proof!=source))
                throw std::runtime_error("Flow mutation violated projection dominance for "+Hex(guest));
              ++flow_cases;
            }
          }
          if (has_conditional) ++conditional_programs;
        }
      }
      if (audit) continue;
      if (automatic_mode && (type_count>>31)) continue;
      if (!automatic_mode && guest!=0x480333F4AFCF0E1E && guest!=0xD6E05D80EF7DEBF8 &&
          guest!=0xA7E5F6317B4316DB &&
          guest!=0xFB08EF4B31D5E686 && guest!=0x6AF9B63098372D65 &&
          guest!=0x813A25A25223C7F5 && guest!=0x5E20CD3F81F88801 &&
          guest!=0x91B258F7198B1CE4 && guest!=0xD66D9932DC2EC8D8 &&
          guest!=0x3306D6C23B238BE6 && guest!=0xAC2A17351535ED19 &&
          guest!=0x807B2A09A19C3B15 && guest!=0x494DCD69B7BA177C &&
          guest!=0x87C093F46637D13F && guest!=0xD66FF6280606248C && guest!=0xB22EF913802807F8 &&
          guest!=0x998F2B953D9B74FD && guest!=0xCB7E063397190431) continue;
      if (type_count>>31 || !found.insert(guest).second) throw std::runtime_error("Duplicate or non-vertex target");
      using namespace rex::graphics;
      Shader shader(xenos::ShaderType::kVertex,guest,words.data(),words.size(),std::endian::big);
      rex::string::StringBuffer scratch;shader.AnalyzeUcode(scratch);
      if (!automatic_mode && shader.uses_register_dynamic_addressing()) throw std::runtime_error("Unexpected indirect guest registers");
      const auto source_proof=aot::AnalyzeProjection(shader,snapshots_mode);
      expected_match=!automatic_mode || source_proof.world!=UINT32_MAX;
      std::cout << "Guest " << Hex(guest) << " special export mask "
                << shader.writes_point_size_edge_flag_kill_vertex() << '\n';
      const uint32_t all_mask=automatic_mode ? shader.writes_interpolators() : (guest==0x87C093F46637D13F || guest==0x998F2B953D9B74FD) ? 0x1FF : guest==0x3306D6C23B238BE6 ? 0x1F : guest==0xD66D9932DC2EC8D8 ? 0x7 : guest==0x91B258F7198B1CE4 ? 0xFF :
          (guest==0x480333F4AFCF0E1E || guest==0x6AF9B63098372D65 || guest==0xAC2A17351535ED19 || guest==0xD66FF6280606248C || guest==0xCB7E063397190431)
          ? 0x7F : (guest==0xFB08EF4B31D5E686 || guest==0x5E20CD3F81F88801 ||
                    guest==0x807B2A09A19C3B15 || guest==0x494DCD69B7BA177C || guest==0xB22EF913802807F8) ? 0x3F : 0;
      std::set<uint64_t> modifications;
      for (uint32_t mask=0;mask<=all_mask;++mask) modifications.insert(mask);
      for (uint32_t planes=0;planes<=6;++planes)
        for (uint32_t cull=0;cull<2;++cull)
          for (uint32_t kill=0;kill<2;++kill) {
            DxbcShaderTranslator::Modification mod(0);
            mod.vertex.interpolator_mask=all_mask;
            mod.vertex.user_clip_plane_count=planes;
            mod.vertex.user_clip_plane_cull=cull;
            mod.vertex.vertex_kill_and=kill;
            modifications.insert(mod.value);
          }
      for (bool bindless : {false,true}) for (bool rov : {false,true}) {
        DxbcShaderTranslator translator(rex::ui::GraphicsProvider::GpuVendorID::kNvidia,bindless,rov);
        for (uint64_t mod : modifications) {
          std::vector<uint8_t> guest_original;
          if (source_proof.captured) {
            // Use the same translator object with capture disabled, checking
            // per-translation reset as well as preserving a true guest control.
            snapshots_mode=false; expected_match=false;
            auto& control=*shader.GetOrCreateTranslation(mod);
            const unsigned before_control=patched_count;
            if (!translator.TranslateAnalyzedShader(control) || patched_count!=before_control+1)
              throw std::runtime_error("Guest control translation failed");
            guest_original=original;
            CheckDisassembly(guest_original);
            shader.DestroyTranslation(mod);
            snapshots_mode=true; expected_match=true; ++control_translations;
          }
          auto& translation=*shader.GetOrCreateTranslation(mod);
          const unsigned before=patched_count;
          if (!translator.TranslateAnalyzedShader(translation) || patched_count!=before+1)
            throw std::runtime_error("Translation failed or hook did not execute exactly once");
          const auto& bytes=translation.translated_binary();
          if (guest_original.empty()) guest_original=original;
          CheckDisassembly(original);CheckDisassembly(bytes);
          if (GlobalFlags(bytes)!=(GlobalFlags(original)|(expected_match ? (fused_mode ? 0x21000u : 0x1000u) : 0u)))
            throw std::runtime_error("Projection changed guest arithmetic policy or unrelated global flags");
          auto duplicate=bytes;
          if (aot::PatchTranslatedProjection(guest,captured_layout,duplicate)!=(expected_match ? aot::ProjectionPatchResult::Unsupported : aot::ProjectionPatchResult::Unrelated) || duplicate!=bytes)
            throw std::runtime_error("Already-patched rejection changed input");
          if (source_proof.captured) {
            if (!captured_layout.captured || captured_layout.captured_mask!=15)
              throw std::runtime_error("Translator did not emit all proven source captures");
            for (unsigned invalid=0;invalid<7;++invalid) {
              auto bad=captured_layout;
              if (invalid<4) bad.captured_mask&=~(1u<<invalid);
              if (invalid==4) bad.world=bad.position;
              if (invalid==5) bad.world=bad.vector_result;
              if (invalid==6) bad.components[3]=5;
              auto rejected=original;
              if (aot::PatchTranslatedProjection(guest,bad,rejected)!=aot::ProjectionPatchResult::Unsupported || rejected!=original)
                throw std::runtime_error("Invalid source-capture layout changed the shader");
              capture_tests << Hex(guest) << '\t' << Hex(mod) << '\t' << bindless << '\t' << rov << '\t' << invalid << '\n';
              ++capture_cases;
            }
          }
          report << Hex(guest) << '\t' << Hex(mod) << '\t' << bindless << '\t' << rov << '\t'
                 << captured_layout.position << '\t' << captured_layout.vector_result << '\t'
                 << captured_layout.system_constants << '\t' << captured_layout.float_constants << '\t'
                 << Hex(XXH3_64bits(original.data(),original.size())) << '\t'
                 << Hex(XXH3_64bits(bytes.data(),bytes.size())) << '\t'
                 << Hex(XXH3_64bits(guest_original.data(),guest_original.size())) << '\t' << captured_layout.captured << '\n';
          // Keep ordinary and maximal clip/cull representatives for GPU replay.
          DxbcShaderTranslator::Modification decoded(mod);
          if (bindless && !rov && (captured_layout.captured || mod==all_mask ||
              decoded.vertex.user_clip_plane_count==6)) {
            const auto name=Hex(guest)+"-"+Hex(mod);
            Save(output/(name+"-original.dxbc"),original);
            Save(output/(name+"-precise.dxbc"),bytes);
            if (source_proof.captured) Save(output/(name+"-guest-original.dxbc"),guest_original);
          }
          shader.DestroyTranslation(mod);
        }
      }
    }
    if ((!audit && !automatic_mode && found.size()!=18) || !report || !coverage) throw std::runtime_error("Missing target shader or report write failure");
    std::cout << "Projection arithmetic recognized in " << recognized << " / " << total_vertex << " vertex programs\n";
    if (flow_tests) {
      if (!flow_cases || !conditional_programs || !flow) throw std::runtime_error("Missing conditional-flow coverage");
      std::cout << "Validated " << flow_cases << " control-flow mutations; " << conditional_programs << " conditional source programs\n";
      if (!literal_cases || !literals) throw std::runtime_error("Missing literal-source mutation coverage or report write failure");
      std::cout << "Validated " << literal_cases << " literal-source mutations\n";
    }
    if (snapshots_mode && !audit && (!capture_cases || !capture_tests))
      throw std::runtime_error("Missing source-capture layout coverage");
    std::cout << "Translated, patched and disassembled " << patched_count-control_translations << " variants\n";
    if (control_translations) std::cout << "Validated " << control_translations << " guest controls and " << capture_cases << " invalid capture layouts\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n';return 1; }
}
