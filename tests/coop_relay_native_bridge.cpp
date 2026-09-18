#include "src/coop_relay_certificate.h"
#include "src/coop_relay_native_bridge.h"
#include "src/coop_relay_host_retry.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <thread>
#include <vector>

int wmain(int argc,wchar_t** argv){
  if(argc!=5&&argc!=6)return 2;
  std::ifstream file(std::filesystem::path(argv[2]),std::ios::binary);const std::vector<unsigned char> der{std::istreambuf_iterator<char>(file),{}};
  auto cert=CertCreateCertificateContext(X509_ASN_ENCODING,der.data(),DWORD(der.size()));
  auto store=CertOpenStore(CERT_STORE_PROV_MEMORY,0,0,0,nullptr);HCERTCHAINENGINE engine=nullptr;
  CERT_CHAIN_ENGINE_CONFIG config{};config.cbSize=sizeof(config);config.hExclusiveRoot=store;
  const bool setup=cert&&store&&CertAddCertificateContextToStore(store,cert,CERT_STORE_ADD_ALWAYS,nullptr)&&CertCreateCertificateChainEngine(&config,&engine);
  bool passed=false;
  if(setup){
    aot::CoopRelayNativeBridge bridge;using State=aot::CoopRelayBridgeState;
    aot::CoopRelayHostRetry retry;
    if(retry.Poll(true,State::Failed,1000)||retry.Poll(true,State::Failed,5999)||!retry.Poll(true,State::Failed,6000)||retry.Poll(true,State::Failed,6001))return 5;
    if(retry.Poll(false,State::Failed,12000)||retry.Poll(true,State::Failed,12001)||retry.Poll(true,State::Connected,18000)||retry.Poll(true,State::Failed,18001))return 6;
    const auto endpoint="tls://127.0.0.1:"+std::to_string(std::stoi(argv[1]));
    const bool retry_peer=std::wstring(argv[3])==L"retry-peer";
    const bool host=std::wstring(argv[3])!=L"peer"&&!retry_peer,cancel=std::wstring(argv[3])==L"cancel",renew=std::wstring(argv[3])==L"renew";
    aot::CoopRelayRoute peer_route;
    bool retried=false;
    bool started=false;
    if(host)started=bridge.Host(endpoint,uint16_t(std::stoi(argv[4])),engine);
    else{
      if(argc!=6)return 2;
      const std::wstring room=argv[4],token=argv[5];peer_route={endpoint,{room.begin(),room.end()},{token.begin(),token.end()}};
      auto initial=peer_route;
      if(retry_peer)initial.join_token[0]=initial.join_token[0]=='a'?'b':'a';
      started=bridge.Peer(initial,engine);
    }
    retry.Reset();unsigned registrations=0;
    bool announced=false,cancelled=false;const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(20);
    while(started&&std::chrono::steady_clock::now()<deadline){
      auto status=bridge.Snapshot();
      if(retry_peer&&!retried&&status.state==State::Failed){
        if(status.local_port||status.error.find("This online game is no longer available.")!=0)break;
        // Failed is observable just before the worker releases its sockets.
        // Retry the same bridge once cleanup completes, without reconstructing it.
        retried=bridge.Peer(peer_route,engine);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        continue;
      }
      if(!announced&&status.state==(host?State::WaitingForPeer:State::WaitingForGame)){
        if(host)std::cout<<status.route.room<<' '<<status.route.join_token<<std::endl;
        else std::cout<<status.local_port<<std::endl;
        announced=true;
        ++registrations;
        if(renew&&registrations==2){passed=true;bridge.Cancel();break;}
      }
      if(cancel&&announced&&!cancelled){
        // Human pairing is idle, so it must not inherit the five-second TLS
        // record read timeout. No CoopLobby admission timer is involved here.
        std::this_thread::sleep_for(std::chrono::milliseconds(5500));
        if(bridge.Snapshot().state!=State::WaitingForPeer)break;
        const auto before=std::chrono::steady_clock::now();bridge.Cancel();
        if(std::chrono::steady_clock::now()-before>std::chrono::milliseconds(100))break;
        cancelled=true;
      }
      if(renew){
        const auto now=uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
        if(retry.Poll(true,status.state,now)&&bridge.Host(endpoint,uint16_t(std::stoi(argv[4])),engine))announced=false;
      }
      if(status.state==State::Failed&&!renew){std::cerr<<status.error<<std::endl;break;}
      if(status.state==State::Stopped){passed=announced&&(!cancel||cancelled)&&(!retry_peer||retried);break;}
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
  }
  if(engine)CertFreeCertificateChainEngine(engine);if(store)CertCloseStore(store,0);if(cert)CertFreeCertificateContext(cert);
  return passed?0:1;
}
