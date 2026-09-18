#pragma once
#include <array>
#include <cstdint>
#include <utility>
#include <vector>
#include <rex/graphics/pipeline/shader/translator.h>
#include "projection_source.h"

namespace aot {
namespace projection_analysis {
using namespace rex::graphics;
struct Value {
  uint32_t identity = 0;
  int constant = -1;
  // For each matrix element: identity of the scalar multiplied by it.
  std::array<uint32_t,16> terms{};
};
class Analyzer final : public ShaderTranslator {
 public:
  explicit Analyzer(bool snapshots=false) : snapshots_(snapshots) {}
  ProjectionSource Run(Shader& shader) {
    if (shader.type()!=xenos::ShaderType::kVertex || shader.uses_register_dynamic_addressing()) return {};
    struct Translation final : Shader::Translation {
      explicit Translation(Shader& s) : Shader::Translation(s,0) {}
    } translation(shader);
    Clear();
    if (!TranslateAnalyzedShader(translation) || exports_!=1 || invalid_) return {};
    return result_;
  }
 private:
  std::array<std::array<Value,4>,64> registers_{};
  uint32_t next_=1, exports_=0;
  bool conditional_=false, invalid_=false;
  ProjectionSource result_;
  bool snapshots_=false, capture_reads_=false;
  uint32_t alu_=0;
  std::vector<std::pair<uint32_t,ProjectionCapture>> read_sources_;
  // Retain the provenance of unconditional literal fetch lanes even after
  // their register is reused. Opaque identities remain unique and versioned.
  std::vector<std::pair<uint32_t,uint32_t>> literal_sources_;
  Value Fresh() { Value v; v.identity=next_++; return v; }
  void Clear() { for (auto& r:registers_) for (auto& v:r) v=Fresh(); }
  // No arbitrary transfers: every accepted export must dominate termination.
  // Conditional exec blocks may calculate the source, but their data flow is
  // opaque at the merge. The matrix product/export must be unconditional.
  void ProcessLabel(uint32_t) override { invalid_=true; Clear(); }
  void ProcessLoopStartInstruction(const ParsedLoopStartInstruction&) override { invalid_=true; Clear(); }
  void ProcessLoopEndInstruction(const ParsedLoopEndInstruction&) override { invalid_=true; Clear(); }
  void ProcessCallInstruction(const ParsedCallInstruction&) override { invalid_=true; Clear(); }
  void ProcessReturnInstruction(const ParsedReturnInstruction&) override { invalid_=true; Clear(); }
  void ProcessJumpInstruction(const ParsedJumpInstruction&) override { invalid_=true; Clear(); }
  void ProcessExecInstructionBegin(const ParsedExecInstruction& i) override {
    conditional_=i.type!=ParsedExecInstruction::Type::kUnconditional;
    if (conditional_) Clear();
  }
  void ProcessExecInstructionEnd(const ParsedExecInstruction& i) override {
    if (conditional_) Clear();
    // Includes conditional early termination. A later textual export does not
    // establish that all invocations initialized the saved host position.
    if (i.is_end && exports_!=1) invalid_=true;
  }
  Value Read(const InstructionOperand& o,uint32_t lane) {
    if (!o.component_count || o.is_negated || o.is_absolute_value ||
        o.storage_addressing_mode!=InstructionStorageAddressingMode::kAbsolute) return {};
    const auto c=uint32_t(o.GetComponent(lane));
    if (c>=4) return {};
    if (o.storage_source==InstructionStorageSource::kRegister && o.storage_index<64) {
      const auto value=registers_[o.storage_index][c];
      if (snapshots_ && capture_reads_ && value.identity && !HasTerms(value) && value.constant<0) {
        bool found=false;
        for (const auto& source:read_sources_) if (source.first==value.identity) { found=true; break; }
        if (!found) read_sources_.push_back({value.identity,{alu_,o.storage_index,c}});
      }
      return value;
    }
    Value v;
    if (o.storage_source==InstructionStorageSource::kConstantFloat && o.storage_index<4)
      v.constant=int(o.storage_index*4+c);
    return v;
  }
  static bool HasTerms(const Value& v) {
    for (auto t:v.terms) if (t) return true;
    return false;
  }
  static Value Mul(Value a,Value b) {
    if (a.constant<0) std::swap(a,b);
    Value v;
    if (a.constant>=0 && b.identity && !HasTerms(b) && b.constant<0)
      v.terms[a.constant]=b.identity;
    return v;
  }
  static Value Add(const Value& a,const Value& b) {
    Value v;
    if (!HasTerms(a) || !HasTerms(b)) return v;
    for (unsigned k=0;k<16;++k) {
      if (a.terms[k] && b.terms[k]) return {};
      v.terms[k]=a.terms[k] ? a.terms[k] : b.terms[k];
    }
    return v;
  }
  void Store(const InstructionResult& dest,const std::array<Value,4>& values,bool uncertain) {
    const auto mask=dest.GetUsedWriteMask();
    if (!mask) return;
    if (dest.storage_target==InstructionStorageTarget::kPosition) {
      ++exports_;
      if (uncertain || dest.is_clamped || !dest.IsStandardSwizzle()) { invalid_=true; return; }
      std::array<uint32_t,4> ids{};
      for (unsigned lane=0;lane<4;++lane) {
        for (unsigned k=0;k<16;++k) {
          const auto t=values[lane].terms[k];
          if (k%4!=lane) { if (t) invalid_=true; continue; }
          if (!t || (lane && ids[k/4]!=t)) invalid_=true;
          if (!lane) ids[k/4]=t;
        }
      }
      if (invalid_) return;
      for (unsigned r=0;r<64;++r) {
        std::array<uint32_t,4> components{};
        unsigned used=0;
        for (unsigned row=0;row<4;++row) {
          bool found=false;
          for (unsigned c=0;c<4;++c) if (!(used&(1u<<c)) && registers_[r][c].identity==ids[row]) {
            components[row]=c; used|=1u<<c; found=true; break;
          }
          if (!found) break;
        }
        if (used==15) { result_={r,components}; return; }
      }
      // Prefer the existing live-register proof above, preserving its output
      // for previously recognized programs. Only recover lost literal lanes.
      for (unsigned r=0;r<64;++r) {
        std::array<uint32_t,4> components{};
        unsigned used=0, rows=0;
        for (unsigned row=0;row<4;++row) {
          bool found=false;
          for (const auto& [identity,literal]:literal_sources_) if (identity==ids[row]) {
            components[row]=literal; found=true; break;
          }
          if (!found) for (unsigned c=0;c<4;++c)
            if (!(used&(1u<<c)) && registers_[r][c].identity==ids[row]) {
              components[row]=c; used|=1u<<c; found=true; break;
            }
          if (!found) break;
          ++rows;
        }
        if (rows==4) { result_={r,components}; return; }
      }
      if (snapshots_) {
        ProjectionSource captured{0,{0,1,2,3},true};
        unsigned rows=0;
        for (unsigned row=0;row<4;++row) {
          bool found=false;
          for (const auto& [identity,source]:read_sources_) if (identity==ids[row]) {
            captured.captures[row]=source; found=true; break;
          }
          if (!found) break;
          ++rows;
        }
        if (rows==4) { result_=captured; return; }
      }
      invalid_=true;
      return;
    }
    if (dest.storage_target!=InstructionStorageTarget::kRegister) return;
    if (dest.storage_addressing_mode!=InstructionStorageAddressingMode::kAbsolute || dest.storage_index>=64) {
      Clear(); return;
    }
    for (unsigned c=0;c<4;++c) if (mask&(1u<<c)) {
      const auto component=uint32_t(dest.components[c]);
      Value v=(!uncertain && !dest.is_clamped && component<4) ? values[component] : Value{};
      // Unknown arithmetic is an opaque, versioned scalar. Known projection
      // expressions carry their terms; a later write cannot alias an old value.
      if (!v.identity) v.identity=next_++;
      registers_[dest.storage_index][c]=v;
    }
  }
  void ProcessVertexFetchInstruction(const ParsedVertexFetchInstruction& i) override {
    Store(i.result,{},true);
    const auto& d=i.result;
    if (conditional_ || i.is_predicated || d.is_clamped ||
        d.storage_target!=InstructionStorageTarget::kRegister ||
        d.storage_addressing_mode!=InstructionStorageAddressingMode::kAbsolute ||
        d.storage_index>=64) return;
    for (unsigned c=0;c<4;++c) if (d.GetUsedWriteMask()&(1u<<c)) {
      const auto literal=uint32_t(d.components[c]);
      if (literal==4 || literal==5)
        literal_sources_.push_back({registers_[d.storage_index][c].identity,literal});
    }
  }
  void ProcessTextureFetchInstruction(const ParsedTextureFetchInstruction& i) override { Store(i.result,{},true); }
  void ProcessAluInstruction(const ParsedAluInstruction& i,uint8_t) override {
    capture_reads_=!conditional_ && !i.is_predicated && !i.IsNop();
    const auto& vector_dest=i.vector_and_constant_result;
    const auto& scalar_dest=i.scalar_result;
    if (vector_dest.storage_target==scalar_dest.storage_target &&
        vector_dest.storage_index==scalar_dest.storage_index &&
        (vector_dest.GetUsedWriteMask() & scalar_dest.GetUsedWriteMask())) invalid_=true;
    std::array<Value,4> values{};
    for (unsigned c=0;c<4;++c) {
      const auto a=Read(i.vector_operands[0],c), b=Read(i.vector_operands[1],c);
      switch (i.vector_opcode) {
        case ucode::AluVectorOpcode::kMul: values[c]=Mul(a,b); break;
        case ucode::AluVectorOpcode::kMad: values[c]=Add(Mul(a,b),Read(i.vector_operands[2],c)); break;
        case ucode::AluVectorOpcode::kAdd: values[c]=Add(a,b); break;
        case ucode::AluVectorOpcode::kMax:
          if (i.vector_operands[0].GetIdenticalComponents(i.vector_operands[1])&(1u<<c)) values[c]=a;
          break;
        default: break;
      }
    }
    // Scalar and vector sources are read before either destination is written.
    // Scalar arithmetic is deliberately opaque to this proof.
    Store(i.scalar_result,{},true);
    Store(i.vector_and_constant_result,values,conditional_ || i.is_predicated);
    capture_reads_=false;
    ++alu_;
  }
};
}
inline ProjectionSource AnalyzeProjection(rex::graphics::Shader& shader,bool snapshots=false) {
  return projection_analysis::Analyzer(snapshots).Run(shader);
}
}
