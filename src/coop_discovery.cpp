#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <bcrypt.h>
#include "coop_discovery.h"
#include <algorithm>
#include <array>
#include <cstring>

namespace aot {
namespace {
constexpr std::array<uint8_t,8> magic={'A','O','T','L','A','N',1,11};
bool Text(std::string_view s,size_t max) {
  return !s.empty() && s.size()<=max && std::all_of(s.begin(),s.end(),[](unsigned char c){return c>=32 && c<127;});
}
}
struct CoopDiscovery::Impl {
  SOCKET socket=INVALID_SOCKET;
  bool ready=false,host=false,searching=false;
  uint64_t deadline=0,next_send=0,last_reply=0;
  uint16_t discovery_port=37003;
  std::array<uint8_t,16> query{};
  CoopDiscoveredRoom advertised;
  CoopVisibility visibility=CoopVisibility::Private;
  std::vector<CoopDiscoveredRoom> rooms;
  std::string error;
  Impl() {WSADATA data{};ready=WSAStartup(MAKEWORD(2,2),&data)==0;}
  ~Impl(){Stop();if(ready) WSACleanup();}
  void Stop(){if(socket!=INVALID_SOCKET) closesocket(socket);socket=INVALID_SOCKET;host=searching=false;}
  bool Open(uint16_t port){
    Stop();rooms.clear();error.clear();
    if(!ready) {error="Network initialization failed.";return false;}
    socket=::socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
    BOOL yes=TRUE;u_long nonblock=1;
    sockaddr_in bind_address{};bind_address.sin_family=AF_INET;bind_address.sin_port=htons(port);
    if(socket==INVALID_SOCKET || setsockopt(socket,SOL_SOCKET,SO_EXCLUSIVEADDRUSE,reinterpret_cast<char*>(&yes),sizeof(yes)) ||
       setsockopt(socket,SOL_SOCKET,SO_BROADCAST,reinterpret_cast<char*>(&yes),sizeof(yes)) ||
       ioctlsocket(socket,FIONBIO,&nonblock) || bind(socket,reinterpret_cast<sockaddr*>(&bind_address),sizeof(bind_address))) {
      Stop();error="LAN discovery could not open its port. Direct joining is still available.";return false;
    }
    return true;
  }
};
CoopDiscovery::CoopDiscovery():impl_(std::make_unique<Impl>()){}
CoopDiscovery::~CoopDiscovery()=default;
void CoopDiscovery::Stop(){impl_->Stop();}
bool CoopDiscovery::Searching()const{return impl_->searching;}
const std::vector<CoopDiscoveredRoom>& CoopDiscovery::Rooms()const{return impl_->rooms;}
const std::string& CoopDiscovery::Error()const{return impl_->error;}
bool CoopDiscovery::Advertise(const CoopLobbySettings& settings,std::string name,uint16_t port,uint16_t discovery_port){
  auto& s=*impl_;
  if(!Text(name,32)||!Text(settings.map,192)||settings.difficulty>2||!port||!discovery_port) return false;
  if(!s.Open(discovery_port))return false;
  s.host=true;s.last_reply=0;s.advertised={"",std::move(name),CoopDiscoveryMap(settings.map),port,settings.difficulty,settings.visibility};return true;
}
bool CoopDiscovery::Search(CoopVisibility visibility,uint64_t now,uint16_t port){
  auto& s=*impl_;if(!port||!s.Open(0))return false;
  std::copy(magic.begin(),magic.end(),s.query.begin());
  if(BCryptGenRandom(nullptr,s.query.data()+8,8,BCRYPT_USE_SYSTEM_PREFERRED_RNG)!=0){s.Stop();s.error="LAN search could not initialize.";return false;}
  s.visibility=visibility;s.discovery_port=port;s.deadline=now+3000;s.next_send=now;s.searching=true;return true;
}
void CoopDiscovery::Poll(uint64_t now){
  auto& s=*impl_;if(s.socket==INVALID_SOCKET)return;
  if(s.searching && now>=s.deadline){s.Stop();return;}
  if(s.searching && now>=s.next_send){
    s.next_send=now+750;
    for(auto address:std::array<uint32_t,2>{INADDR_BROADCAST,INADDR_LOOPBACK}){
      sockaddr_in target{};target.sin_family=AF_INET;target.sin_port=htons(s.discovery_port);target.sin_addr.s_addr=htonl(address);
      sendto(s.socket,reinterpret_cast<const char*>(s.query.data()),int(s.query.size()),0,reinterpret_cast<sockaddr*>(&target),sizeof(target));
    }
  }
  for(unsigned i=0;i<32;++i){
    std::array<uint8_t,128> bytes{};sockaddr_in from{};int length=sizeof(from);
    const auto count=recvfrom(s.socket,reinterpret_cast<char*>(bytes.data()),int(bytes.size()),0,reinterpret_cast<sockaddr*>(&from),&length);
    if(count==SOCKET_ERROR){if(WSAGetLastError()==WSAEWOULDBLOCK)break;continue;}
    if(count<16||!std::equal(magic.begin(),magic.end(),bytes.begin()))continue;
    if(s.host){
      if(count!=16 || (s.last_reply && now-s.last_reply<20))continue;
      s.last_reply=now;
      const auto& r=s.advertised;
      bytes[16]=uint8_t(r.port>>8);bytes[17]=uint8_t(r.port);bytes[18]=uint8_t(r.visibility);bytes[19]=r.difficulty;
      bytes[20]=uint8_t(r.name.size());bytes[21]=uint8_t(r.map.size());
      std::copy(r.name.begin(),r.name.end(),bytes.begin()+22);std::copy(r.map.begin(),r.map.end(),bytes.begin()+22+r.name.size());
      sendto(s.socket,reinterpret_cast<char*>(bytes.data()),int(22+r.name.size()+r.map.size()),0,reinterpret_cast<sockaddr*>(&from),length);
    }else if(s.searching){
      if(count<22||!std::equal(s.query.begin(),s.query.end(),bytes.begin())||bytes[18]!=uint8_t(s.visibility)||bytes[19]>2||
         count!=22+bytes[20]+bytes[21]||!bytes[20]||bytes[20]>32||!bytes[21]||bytes[21]>64)continue;
      CoopDiscoveredRoom r;r.port=uint16_t(bytes[16]<<8|bytes[17]);r.visibility=s.visibility;r.difficulty=bytes[19];
      r.name.assign(reinterpret_cast<char*>(bytes.data()+22),bytes[20]);r.map.assign(reinterpret_cast<char*>(bytes.data()+22+bytes[20]),bytes[21]);
      char address[INET_ADDRSTRLEN]{};if(!inet_ntop(AF_INET,&from.sin_addr,address,sizeof(address))||!r.port||!Text(r.name,32)||!Text(r.map,64))continue;
      r.address=address;
      if(std::none_of(s.rooms.begin(),s.rooms.end(),[&](const auto& existing){return existing.address==r.address&&existing.port==r.port;})&&s.rooms.size()<32)s.rooms.push_back(std::move(r));
    }
  }
}
}
