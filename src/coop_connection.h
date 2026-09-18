#pragma once
#include "coop_relay_route.h"
#include "coop_private_invitation.h"
#include <charconv>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace aot {
struct CoopConnection {
  bool join=false;
  std::string name="PC Player", address="127.0.0.1", port="37001", invite;
  std::optional<CoopRelayRoute> relay;
  bool PasteInvitation(std::string_view text) {
    if(auto online=CoopPrivateInvitation::Parse(text)) {
      relay=std::move(online->relay);invite=std::move(online->invite);return true;
    }
    if(text.size()!=32||text.find_first_not_of("0123456789abcdefABCDEF")!=text.npos)return false;
    invite=text;relay.reset();return true;
  }
  bool PasteAddress(std::string_view text) {
    if (text.size()>21) return false;
    auto candidate=*this;
    candidate.relay.reset();
    const auto colon=text.find(':');
    candidate.address=text.substr(0,colon);
    if (colon!=text.npos) candidate.port=text.substr(colon+1);
    candidate.join=true;candidate.name="PC Player";
    if (!candidate.Error(false).empty()) return false;
    address=std::move(candidate.address);port=std::move(candidate.port);relay.reset();return true;
  }
  uint16_t Port() const {
    unsigned value=0;
    const auto parsed=std::from_chars(port.data(),port.data()+port.size(),value);
    return parsed.ec==std::errc{} && parsed.ptr==port.data()+port.size() && value && value<=65535 ? uint16_t(value) : 0;
  }
  std::string Error(bool private_room) const {
    if (name.empty() || name.size()>32 || name.find_first_not_of(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _.-")!=name.npos)
      return "Enter a player name (1-32 letters, numbers or spaces).";
    // Relay joins use the bridge's assigned loopback port, not this draft's
    // direct-connect field. Hosting still needs its native listening port.
    if ((!join || !relay) && !Port()) return "Enter a port from 1 to 65535.";
    if (!join) return {};
    if (relay && (!relay->Valid() || !relay->endpoint.starts_with("tls://")))
      return "The selected online relay is invalid. Search again.";
    if (!relay) {
    auto rest=std::string_view(address);
    for (unsigned index=0;index<4;++index) {
      const auto dot=rest.find('.'); const auto part=rest.substr(0,dot);
      unsigned value=0; const auto parsed=std::from_chars(part.data(),part.data()+part.size(),value);
      if (part.empty() || part.size()>3 || (part.size()>1 && part.front()=='0') || parsed.ec!=std::errc{} ||
          parsed.ptr!=part.data()+part.size() || value>255 ||
          (!index && (!value || value>=224)) || (index==3 ? dot!=rest.npos : dot==rest.npos))
        return "Enter the host's IPv4 address, such as 192.168.1.20.";
      rest=dot==rest.npos ? std::string_view{} : rest.substr(dot+1);
    }
    }
    if (private_room && (invite.size()!=32 || invite.find_first_not_of("0123456789abcdefABCDEF")!=invite.npos))
      return "Enter the host's 32-character private invitation.";
    return {};
  }
};
struct CoopConnectionResult { bool cancelled=true; CoopConnection connection; };
}
