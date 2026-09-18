#pragma once
#include <charconv>
#include <string>
#include <string_view>

namespace aot {
struct CoopRelayRoute {
  std::string endpoint,room,join_token;
  bool operator==(const CoopRelayRoute&) const = default;
  bool Valid()const{
    if(room.size()!=32||room.find_first_not_of("0123456789abcdef")!=room.npos||join_token.size()!=43||
       join_token.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=join_token.npos)return false;
    auto value=std::string_view(endpoint);const bool tls=value.starts_with("tls://");
    if(!tls&&!value.starts_with("tcp://"))return false;
    value.remove_prefix(6);const auto colon=value.find(':');
    if(colon==value.npos)return false;
    const auto host=value.substr(0,colon),port=value.substr(colon+1);
    if(host.empty()||host.size()>253||host.front()=='.'||host.back()=='.'||host.find("..")!=host.npos||
       host.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")!=host.npos)return false;
    if(!tls&&host!="127.0.0.1"&&host!="localhost")return false;
    unsigned number=0;const auto result=std::from_chars(port.data(),port.data()+port.size(),number);
    return result.ec==std::errc{}&&result.ptr==port.data()+port.size()&&number>0&&number<=65535;
  }
};
}
