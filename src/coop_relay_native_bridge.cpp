#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include "coop_relay_native_bridge.h"
#include "coop_relay_session.h"
#include "coop_relay_certificate.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace aot {
namespace {
using Clock=std::chrono::steady_clock;
using State=CoopRelayBridgeState;
void Require(bool condition,const char* error){if(!condition)throw std::runtime_error(error);}
// Explicit local menu-test fixture only. Never used for a DNS name or remote IP,
// and never installed in the user's Windows certificate stores.
struct LocalTestTrust {
  HCERTCHAINENGINE engine=nullptr;
  ~LocalTestTrust(){if(engine)CertFreeCertificateChainEngine(engine);}
  void Load(const std::string& hostname){
    const auto* path=std::getenv("AOT_COOP_RELAY_TEST_CA");
    if(hostname!="127.0.0.1"||!path||!*path)return;
    std::ifstream file(std::filesystem::path(path),std::ios::binary|std::ios::ate);
    const auto size=file.tellg();Require(file&&size>0&&size<=65536,"Invalid local relay test certificate.");
    std::vector<uint8_t> bytes(size_t(size),0);file.seekg(0);file.read(reinterpret_cast<char*>(bytes.data()),bytes.size());
    Require(bool(file),"Cannot read local relay test certificate.");
    auto cert=CertCreateCertificateContext(X509_ASN_ENCODING,bytes.data(),DWORD(bytes.size()));
    auto store=CertOpenStore(CERT_STORE_PROV_MEMORY,0,0,0,nullptr);
    CERT_CHAIN_ENGINE_CONFIG config{};config.cbSize=sizeof(config);config.hExclusiveRoot=store;
    const bool ok=cert&&store&&CertAddCertificateContextToStore(store,cert,CERT_STORE_ADD_ALWAYS,nullptr)&&CertCreateCertificateChainEngine(&config,&engine);
    if(store)CertCloseStore(store,0);if(cert)CertFreeCertificateContext(cert);
    Require(ok,"Cannot configure local relay test trust.");
  }
};
void Configure(SOCKET socket){
  DWORD timeout=5000,enabled=1;
  Require(!setsockopt(socket,SOL_SOCKET,SO_SNDTIMEO,reinterpret_cast<char*>(&timeout),sizeof(timeout))&&
    !setsockopt(socket,SOL_SOCKET,SO_RCVTIMEO,reinterpret_cast<char*>(&timeout),sizeof(timeout))&&
    !setsockopt(socket,IPPROTO_TCP,TCP_NODELAY,reinterpret_cast<char*>(&enabled),sizeof(enabled)),"Cannot configure relay bridge socket.");
}
}
struct CoopRelayNativeBridge::Impl {
  mutable std::mutex mutex;
  CoopRelayBridgeStatus status;
  std::atomic<bool> cancelled=false,running=false;
  std::thread worker;
  std::vector<SOCKET> sockets;
  ~Impl(){Cancel();if(worker.joinable())worker.join();}
  void Cancel(){cancelled=true;std::lock_guard lock(mutex);for(auto socket:sockets)shutdown(socket,SD_BOTH);}
  void Check(){Require(!cancelled,"Relay connection cancelled.");}
  void StateTo(State state){std::lock_guard lock(mutex);status.state=state;}
  SOCKET Track(SOCKET socket){
    Require(socket!=INVALID_SOCKET,"Cannot open relay bridge socket.");
    std::lock_guard lock(mutex);sockets.push_back(socket);if(cancelled)shutdown(socket,SD_BOTH);return socket;
  }
  bool Ready(SOCKET socket,bool writing=false){
    Check();fd_set set,errors;FD_ZERO(&set);FD_ZERO(&errors);FD_SET(socket,&set);FD_SET(socket,&errors);timeval timeout{0,50000};
    const auto result=select(0,writing?nullptr:&set,writing?&set:nullptr,&errors,&timeout);
    Require(result!=SOCKET_ERROR&&!FD_ISSET(socket,&errors),"Relay bridge socket failed.");Check();return result>0;
  }
  SOCKET Connect(const std::wstring& hostname,const std::wstring& port){
    ADDRINFOEXW hints{};hints.ai_family=AF_INET;hints.ai_socktype=SOCK_STREAM;hints.ai_protocol=IPPROTO_TCP;
    PADDRINFOEXW addresses=nullptr;OVERLAPPED operation{};operation.hEvent=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    Require(operation.hEvent!=nullptr,"Cannot create relay resolver event.");
    HANDLE query=nullptr;timeval timeout{5,0};
    int result=GetAddrInfoExW(hostname.c_str(),port.c_str(),NS_DNS,nullptr,&hints,&addresses,&timeout,&operation,nullptr,&query);
    if(result==WSA_IO_PENDING){
      const auto deadline=Clock::now()+std::chrono::seconds(5);
      while(WaitForSingleObject(operation.hEvent,50)==WAIT_TIMEOUT){
        if(cancelled||Clock::now()>deadline){GetAddrInfoExCancel(&query);WaitForSingleObject(operation.hEvent,INFINITE);break;}
      }
      result=GetAddrInfoExOverlappedResult(&operation);
    }
    CloseHandle(operation.hEvent);
    std::unique_ptr<ADDRINFOEXW,decltype(&FreeAddrInfoExW)> owned(addresses,FreeAddrInfoExW);
    Check();Require(!result&&addresses,"Cannot resolve relay address.");
    const auto deadline=Clock::now()+std::chrono::seconds(5);
    for(auto* address=addresses;address;address=address->ai_next){
      auto socket=Track(::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP));u_long nonblocking=1;
      Require(!ioctlsocket(socket,FIONBIO,&nonblocking),"Cannot configure relay connect.");
      bool connected=!connect(socket,address->ai_addr,int(address->ai_addrlen));
      if(!connected&&WSAGetLastError()==WSAEWOULDBLOCK){
        while(Clock::now()<deadline){
          Check();fd_set writable,errors;FD_ZERO(&writable);FD_ZERO(&errors);FD_SET(socket,&writable);FD_SET(socket,&errors);timeval poll{0,50000};
          const int count=select(0,nullptr,&writable,&errors,&poll);Require(count!=SOCKET_ERROR,"Relay connect failed.");
          if(count){int error=0,length=sizeof(error);connected=!getsockopt(socket,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&length)&&!error;break;}
        }
      }
      if(connected){nonblocking=0;Require(!ioctlsocket(socket,FIONBIO,&nonblocking),"Cannot restore relay socket mode.");Configure(socket);return socket;}
      {std::lock_guard lock(mutex);closesocket(socket);sockets.erase(std::find(sockets.begin(),sockets.end(),socket));}
      if(Clock::now()>=deadline)break;
    }
    throw std::runtime_error("Cannot connect to relay endpoint.");
  }
  void Run(bool host,CoopRelayRoute route,uint16_t game_port,void* trust){
    WSADATA data{};const bool started=!WSAStartup(MAKEWORD(2,2),&data);
    try{
      Require(started,"Cannot initialize relay networking.");
      const auto split=route.endpoint.rfind(':');const auto name=route.endpoint.substr(6,split-6),port=route.endpoint.substr(split+1);
      LocalTestTrust local_trust;if(!trust){local_trust.Load(name);trust=local_trust.engine;}
      const auto relay_socket=Connect(std::wstring(name.begin(),name.end()),std::wstring(port.begin(),port.end()));
      CoopTlsStream stream;stream.Handshake(relay_socket,std::wstring(name.begin(),name.end()),trust);CoopRelaySession session(stream);
      SOCKET game=INVALID_SOCKET;
      if(host){
        route=session.RegisterHost(route.endpoint);
        {std::lock_guard lock(mutex);status.route=route;status.state=State::WaitingForPeer;}
        const auto deadline=Clock::now()+std::chrono::seconds(125);
        while(!session.HasBufferedInput()&&!Ready(relay_socket))Require(Clock::now()<deadline,"Relay pairing timed out.");
        Check();session.WaitPaired();game=Connect(L"127.0.0.1",std::to_wstring(game_port));
      }else{
        session.Join(route);
        auto listener=Track(::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP));sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        Require(!bind(listener,reinterpret_cast<sockaddr*>(&address),sizeof(address))&&!listen(listener,1),"Cannot open local relay listener.");
        int size=sizeof(address);Require(!getsockname(listener,reinterpret_cast<sockaddr*>(&address),&size),"Cannot read relay listener port.");
        {std::lock_guard lock(mutex);status.local_port=ntohs(address.sin_port);status.state=State::WaitingForGame;}
        const auto deadline=Clock::now()+std::chrono::seconds(15);
        while(!Ready(listener))Require(Clock::now()<deadline,"Local game did not connect to relay.");
        Check();game=Track(accept(listener,nullptr,nullptr));Configure(game);
      }
      StateTo(State::Connected);std::array<uint8_t,32768> buffer{};std::vector<uint8_t> incoming;
      while(!cancelled){
        fd_set readable;FD_ZERO(&readable);FD_SET(relay_socket,&readable);FD_SET(game,&readable);
        const bool buffered=session.HasBufferedInput();timeval poll{0,buffered?0:50000};
        Require(select(0,&readable,nullptr,nullptr,&poll)!=SOCKET_ERROR,"Relay forwarding failed.");Check();
        if(buffered||FD_ISSET(relay_socket,&readable)){
          if(!session.Read(incoming))break;
          size_t offset=0;while(offset<incoming.size()){Check();int sent=send(game,reinterpret_cast<char*>(incoming.data()+offset),int(incoming.size()-offset),0);Require(sent>0,"Local game receive failed.");offset+=sent;}
        }
        if(FD_ISSET(game,&readable)){const int count=recv(game,reinterpret_cast<char*>(buffer.data()),int(buffer.size()),0);Require(count>=0,"Local game send failed.");if(!count)break;session.Write({buffer.data(),size_t(count)});}
      }
      StateTo(State::Stopped);
    }catch(const std::exception& error){std::lock_guard lock(mutex);status.state=cancelled?State::Stopped:State::Failed;if(!cancelled)status.error=error.what();}
    {std::lock_guard lock(mutex);for(auto socket:sockets){shutdown(socket,SD_BOTH);closesocket(socket);}sockets.clear();}
    if(started)WSACleanup();running=false;
  }
  bool Start(bool host,CoopRelayRoute route,uint16_t port,void* trust){
    if(running||!route.Valid()||!route.endpoint.starts_with("tls://")||(host&&!port))return false;
    if(worker.joinable())worker.join();cancelled=false;running=true;
    {std::lock_guard lock(mutex);status={};status.state=State::Connecting;}
    try{worker=std::thread([this,host,route=std::move(route),port,trust]{Run(host,route,port,trust);});}
    catch(...){running=false;StateTo(State::Failed);throw;}
    return true;
  }
};
CoopRelayNativeBridge::CoopRelayNativeBridge():impl_(std::make_unique<Impl>()){}
CoopRelayNativeBridge::~CoopRelayNativeBridge()=default;
bool CoopRelayNativeBridge::Host(const std::string& endpoint,uint16_t port,void* trust){return impl_->Start(true,{endpoint,std::string(32,'0'),std::string(43,'a')},port,trust);}
bool CoopRelayNativeBridge::Peer(const CoopRelayRoute& route,void* trust){return impl_->Start(false,route,0,trust);}
void CoopRelayNativeBridge::Cancel(){impl_->Cancel();}
CoopRelayBridgeStatus CoopRelayNativeBridge::Snapshot()const{std::lock_guard lock(impl_->mutex);return impl_->status;}
}
