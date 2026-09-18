#include <winsock2.h>
#include <ws2tcpip.h>
#include "src/coop_tls_stream.h"
#include "src/coop_relay_certificate.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

int wmain(int argc,wchar_t** argv){
  if(argc!=5)return 2;
  WSADATA data{};if(WSAStartup(MAKEWORD(2,2),&data))return 3;
  const auto socket=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(uint16_t(std::stoi(argv[1])));
  if(connect(socket,reinterpret_cast<sockaddr*>(&address),sizeof(address))){closesocket(socket);WSACleanup();return 4;}
  std::ifstream file(std::filesystem::path(argv[2]),std::ios::binary);const std::vector<unsigned char> der{std::istreambuf_iterator<char>(file),{}};
  auto cert=CertCreateCertificateContext(X509_ASN_ENCODING,der.data(),DWORD(der.size()));
  auto store=CertOpenStore(CERT_STORE_PROV_MEMORY,0,0,0,nullptr);HCERTCHAINENGINE engine=nullptr;
  CERT_CHAIN_ENGINE_CONFIG config{};config.cbSize=sizeof(config);config.hExclusiveRoot=store;
  const bool setup=cert&&store&&CertAddCertificateContextToStore(store,cert,CERT_STORE_ADD_ALWAYS,nullptr)&&CertCreateCertificateChainEngine(&config,&engine);
  const bool trusted=std::wstring(argv[3])==L"trust",accept=trusted&&std::wstring(argv[4])==L"127.0.0.1";
  bool passed=false,handshake=false;
  try{
    if(!setup)throw std::runtime_error("test certificate setup");
    aot::CoopTlsStream stream;stream.Handshake(uintptr_t(socket),argv[4],trusted?engine:nullptr);handshake=true;
    if(!accept)throw std::runtime_error("accepted invalid certificate");
    std::vector<uint8_t> payload(131111);for(size_t i=0;i<payload.size();++i)payload[i]=uint8_t(i*31);
    stream.Write(payload);std::vector<uint8_t> received,part;
    while(stream.Read(part)){received.insert(received.end(),part.begin(),part.end());if(received.size()>payload.size())throw std::runtime_error("excess plaintext");}
    if(received!=payload)throw std::runtime_error("plaintext mismatch");passed=true;
  }catch(const std::exception& e){if(!accept&&!handshake)passed=true;else std::cerr<<e.what()<<'\n';}
  if(engine)CertFreeCertificateChainEngine(engine);if(store)CertCloseStore(store,0);if(cert)CertFreeCertificateContext(cert);
  closesocket(socket);WSACleanup();return passed?0:1;
}
