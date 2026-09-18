#pragma once
#include "coop_relay_route.h"
#include <array>
#include <optional>

namespace aot {
struct CoopPrivateInvitation {
  CoopRelayRoute relay;
  std::string invite;
  bool Valid() const {
    return relay.Valid()&&relay.endpoint.starts_with("tls://")&&invite.size()==32&&
      invite.find_first_not_of("0123456789abcdefABCDEF")==invite.npos;
  }
  std::string Encode() const {
    if(!Valid())return {};
    return "aot1|"+relay.endpoint+"|"+relay.room+"|"+relay.join_token+"|"+invite;
  }
  static std::optional<CoopPrivateInvitation> Parse(std::string_view text) {
    if(text.size()>512)return std::nullopt;
    std::array<std::string_view,5> fields;
    for(size_t i=0;i<fields.size();++i){
      const auto delimiter=text.find('|');
      if((i==fields.size()-1)!=(delimiter==text.npos))return std::nullopt;
      fields[i]=text.substr(0,delimiter);
      if(delimiter!=text.npos)text.remove_prefix(delimiter+1);
    }
    if(fields[0]!="aot1")return std::nullopt;
    CoopPrivateInvitation result{{std::string(fields[1]),std::string(fields[2]),std::string(fields[3])},std::string(fields[4])};
    return result.Valid()?std::optional(std::move(result)):std::nullopt;
  }
};
}
