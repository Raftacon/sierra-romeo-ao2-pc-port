#pragma once
#include <cstddef>
#include <cstdint>
#include <span>

namespace aot {
// Retail 829419F8/82947B30 input codec, inside packet type 0x55.
// Signed scalars have a sign-bit bias; multibyte scalars are big endian.
// Validate the complete packet before calling the original unchecked decoder.
inline bool ValidCoopInputPacket(std::span<const uint8_t> bytes, unsigned sender) {
  if (sender>1 || bytes.size()<5 || bytes.size()>0x4AF || bytes[0]!=0xD5) return false;
  const auto integer=[&](size_t at) {
    return (uint32_t(bytes[at])<<24 | uint32_t(bytes[at+1])<<16 |
            uint32_t(bytes[at+2])<<8 | bytes[at+3])^0x80000000u;
  };
  const auto frame=integer(1);
  if (frame==0xFFFFFFFEu) return bytes.size()==5;
  if (frame==0xFFFFFFFFu) {
    if (bytes.size()<10 || integer(5)>INT32_MAX) return false;
    const unsigned count=bytes[9]^0x80;
    if (count>2 || bytes.size()!=10+count) return false;
    // 8295F410 emits its local controller-ID list; 82961330 matches those
    // IDs against the receiving side's remote queue entries.
    for (unsigned i=0;i<count;++i) if ((bytes[10+i]^0x80)!=sender) return false;
    return true;
  }
  if (frame>INT32_MAX || bytes.size()<7) return false;
  const unsigned count=bytes[6]^0x80;
  if (!count || count>12) return false; // Original static array has 12 records.
  size_t offset=7;
  for (unsigned i=0;i<count;++i) {
    if (bytes.size()-offset<10) return false;
    const unsigned counts=bytes[offset+9]^0x80;
    const unsigned events=counts&15, axes=counts>>4;
    if (axes>8) return false; // Record +0xF8..+0x138 holds eight pairs.
    offset+=10;
    const size_t variable=3*events+2*axes;
    if (bytes.size()-offset<variable+6) return false;
    offset+=variable;
    if (((bytes[offset+1]^0x80)&15)!=sender) return false;
    const unsigned commands=bytes[offset+5]^0x80;
    if (commands>127) return false;
    offset+=6;
    if (bytes.size()-offset<3*commands) return false;
    offset+=3*commands;
  }
  return offset==bytes.size();
}
}
