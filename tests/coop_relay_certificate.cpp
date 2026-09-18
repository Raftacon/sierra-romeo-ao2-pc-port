#include "src/coop_relay_certificate.h"
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>
#include <iostream>

int wmain(int argc,wchar_t** argv){
  if(argc!=2)return 2;
  std::ifstream input(std::filesystem::path(argv[1]),std::ios::binary);
  const std::vector<unsigned char> bytes{std::istreambuf_iterator<char>(input),{}};
  auto cert=CertCreateCertificateContext(X509_ASN_ENCODING,bytes.data(),DWORD(bytes.size()));
  if(!cert)return 3;
  auto store=CertOpenStore(CERT_STORE_PROV_MEMORY,0,0,0,nullptr);
  HCERTCHAINENGINE engine=nullptr;
  CERT_CHAIN_ENGINE_CONFIG config{};config.cbSize=sizeof(config);config.hExclusiveRoot=store;
  const bool setup=store&&CertAddCertificateContextToStore(store,cert,CERT_STORE_ADD_ALWAYS,nullptr)&&CertCreateCertificateChainEngine(&config,&engine);
  bool passed=false;
  if(setup){
    FILETIME now{};GetSystemTimeAsFileTime(&now);ULARGE_INTEGER later{};
    later.LowPart=now.dwLowDateTime;later.HighPart=now.dwHighDateTime;
    later.QuadPart+=3ULL*24*60*60*10000000;
    const FILETIME expired_at{later.LowPart,later.HighPart};
    passed=aot::VerifyCoopRelayCertificate(cert,L"127.0.0.1",engine)&&
      !aot::VerifyCoopRelayCertificate(cert,L"127.0.0.1",engine,&expired_at)&&
      !aot::VerifyCoopRelayCertificate(cert,L"localhost",engine)&&
      !aot::VerifyCoopRelayCertificate(cert,L"127.0.0.1")&&
      !aot::VerifyCoopRelayCertificate(nullptr,L"127.0.0.1",engine)&&
      !aot::VerifyCoopRelayCertificate(cert,std::wstring(L"127.0.0.1\0wrong",15),engine);
  }
  if(engine)CertFreeCertificateChainEngine(engine);
  if(store)CertCloseStore(store,0);
  CertFreeCertificateContext(cert);
  std::cout<<(passed?"Windows relay certificate policy passed\n":"Windows relay certificate policy failed\n");
  return passed?0:1;
}
