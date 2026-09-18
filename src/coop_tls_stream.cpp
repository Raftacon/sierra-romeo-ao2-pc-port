#ifndef NOMINMAX
#define NOMINMAX
#endif
#define SECURITY_WIN32
#include <winsock2.h>
#include <security.h>
#include <schannel.h>
#include "coop_relay_certificate.h"
#include "coop_tls_stream.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <stdexcept>

namespace aot {
struct CoopTlsStream::Impl {
  SOCKET socket=INVALID_SOCKET;
  CredHandle credentials{};CtxtHandle context{};
  bool credential_valid=false,context_valid=false,verified=false,closed=false;
  SecPkgContext_StreamSizes sizes{};
  std::vector<uint8_t> encrypted;
  Impl(){SecInvalidateHandle(&credentials);SecInvalidateHandle(&context);}
  ~Impl(){
    if(context_valid&&verified){
      DWORD shutdown=SCHANNEL_SHUTDOWN;SecBuffer control{sizeof(shutdown),SECBUFFER_TOKEN,&shutdown};SecBufferDesc desc{SECBUFFER_VERSION,1,&control};
      if(ApplyControlToken(&context,&desc)==SEC_E_OK){
        SecBuffer token{0,SECBUFFER_TOKEN,nullptr};SecBufferDesc output{SECBUFFER_VERSION,1,&token};ULONG flags=0;TimeStamp expiry{};
        InitializeSecurityContextW(&credentials,&context,nullptr,ISC_REQ_STREAM|ISC_REQ_CONFIDENTIALITY|ISC_REQ_ALLOCATE_MEMORY,0,SECURITY_NATIVE_DREP,nullptr,0,&context,&output,&flags,&expiry);
        if(token.pvBuffer){try{Send({static_cast<uint8_t*>(token.pvBuffer),token.cbBuffer});}catch(...){}FreeContextBuffer(token.pvBuffer);}
      }
    }
    if(context_valid)DeleteSecurityContext(&context);if(credential_valid)FreeCredentialsHandle(&credentials);
  }
  [[noreturn]] void Fail(const char* message){verified=false;throw std::runtime_error(message);}
  void Send(std::span<const uint8_t> bytes){
    while(!bytes.empty()){
      const int sent=send(socket,reinterpret_cast<const char*>(bytes.data()),int(std::min<size_t>(bytes.size(),65536)),0);
      if(sent<=0)Fail("Relay TLS send failed.");bytes=bytes.subspan(size_t(sent));
    }
  }
  void Receive(){
    std::array<uint8_t,16384> buffer{};
    const auto count=recv(socket,reinterpret_cast<char*>(buffer.data()),int(buffer.size()),0);
    if(count<=0)Fail("Relay TLS stream ended without a close notification or timed out.");
    if(encrypted.size()+size_t(count)>131072)Fail("Relay TLS record exceeds the buffer limit.");
    encrypted.insert(encrypted.end(),buffer.begin(),buffer.begin()+count);
  }
  void KeepExtra(const SecBuffer* buffers,size_t count){
    size_t extra=0;
    for(size_t i=0;i<count;++i)if(buffers[i].BufferType==SECBUFFER_EXTRA)extra=buffers[i].cbBuffer;
    if(extra>encrypted.size())Fail("Invalid TLS extra-buffer size.");
    encrypted.erase(encrypted.begin(),encrypted.end()-extra);
  }
};
CoopTlsStream::CoopTlsStream():impl_(std::make_unique<Impl>()){}
CoopTlsStream::~CoopTlsStream()=default;
bool CoopTlsStream::HasBufferedInput() const{return !impl_->encrypted.empty();}
void CoopTlsStream::Handshake(uintptr_t socket,const std::wstring& hostname,void* trust_engine){
  auto& s=*impl_;if(s.credential_valid)throw std::runtime_error("TLS stream already initialized.");
  if(hostname.empty()||hostname.size()>253||hostname.find(L'\0')!=hostname.npos)s.Fail("Invalid relay hostname.");
  s.socket=SOCKET(socket);
  const DWORD timeout=5000;
  const DWORD no_delay=1;
  if(setsockopt(s.socket,IPPROTO_TCP,TCP_NODELAY,reinterpret_cast<const char*>(&no_delay),sizeof(no_delay)))s.Fail("Cannot configure TLS TCP socket.");
  if(setsockopt(s.socket,SOL_SOCKET,SO_RCVTIMEO,reinterpret_cast<const char*>(&timeout),sizeof(timeout))||
     setsockopt(s.socket,SOL_SOCKET,SO_SNDTIMEO,reinterpret_cast<const char*>(&timeout),sizeof(timeout)))s.Fail("Cannot set relay TLS socket timeouts.");
  SCHANNEL_CRED config{};config.dwVersion=SCHANNEL_CRED_VERSION;
  config.grbitEnabledProtocols=SP_PROT_TLS1_2_CLIENT;
  config.dwFlags=SCH_CRED_MANUAL_CRED_VALIDATION|SCH_CRED_NO_DEFAULT_CREDS;
  TimeStamp expiry{};
  if(AcquireCredentialsHandleW(nullptr,const_cast<wchar_t*>(UNISP_NAME_W),SECPKG_CRED_OUTBOUND,nullptr,&config,nullptr,nullptr,&s.credentials,&expiry)!=SEC_E_OK)s.Fail("Cannot acquire TLS credentials.");
  s.credential_valid=true;
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
  for(unsigned step=0;step<128;++step){
    if(std::chrono::steady_clock::now()>deadline)s.Fail("Relay TLS handshake timed out.");
    SecBuffer inputs[2]={{ULONG(s.encrypted.size()),SECBUFFER_TOKEN,s.encrypted.data()},{0,SECBUFFER_EMPTY,nullptr}};
    SecBufferDesc input{SECBUFFER_VERSION,2,inputs};
    SecBuffer token{0,SECBUFFER_TOKEN,nullptr};SecBufferDesc output{SECBUFFER_VERSION,1,&token};
    ULONG attributes=0;
    const auto status=InitializeSecurityContextW(&s.credentials,s.context_valid?&s.context:nullptr,const_cast<wchar_t*>(hostname.c_str()),
      ISC_REQ_STREAM|ISC_REQ_CONFIDENTIALITY|ISC_REQ_REPLAY_DETECT|ISC_REQ_SEQUENCE_DETECT|ISC_REQ_ALLOCATE_MEMORY,0,SECURITY_NATIVE_DREP,
      s.encrypted.empty()?nullptr:&input,0,&s.context,&output,&attributes,&expiry);
    s.context_valid=SecIsValidHandle(&s.context);
    if(token.pvBuffer){
      try{if(token.cbBuffer)s.Send({static_cast<uint8_t*>(token.pvBuffer),token.cbBuffer});}
      catch(...){FreeContextBuffer(token.pvBuffer);throw;}
      FreeContextBuffer(token.pvBuffer);
    }
    if(status==SEC_E_INCOMPLETE_MESSAGE){s.Receive();continue;}
    if(status!=SEC_E_OK&&status!=SEC_I_CONTINUE_NEEDED)s.Fail("Relay TLS handshake failed.");
    s.KeepExtra(inputs,2);
    if(status==SEC_E_OK){
      PCCERT_CONTEXT certificate=nullptr;
      if(QueryContextAttributesW(&s.context,SECPKG_ATTR_REMOTE_CERT_CONTEXT,&certificate)!=SEC_E_OK)s.Fail("Relay TLS certificate is missing.");
      const bool valid=VerifyCoopRelayCertificate(certificate,hostname,trust_engine);
      CertFreeCertificateContext(certificate);
      if(!valid)s.Fail("Relay TLS certificate or hostname verification failed.");
      if(QueryContextAttributesW(&s.context,SECPKG_ATTR_STREAM_SIZES,&s.sizes)!=SEC_E_OK||!s.sizes.cbMaximumMessage||s.sizes.cbMaximumMessage>65536)s.Fail("Invalid TLS stream sizes.");
      s.verified=true;return;
    }
    if(s.encrypted.empty())s.Receive();
  }
  s.Fail("Relay TLS handshake exceeded its step limit.");
}
void CoopTlsStream::Write(std::span<const uint8_t> bytes){
  auto& s=*impl_;if(!s.verified||s.closed)s.Fail("Relay TLS connection is not verified.");
  while(!bytes.empty()){
    const auto count=std::min<size_t>(bytes.size(),s.sizes.cbMaximumMessage);
    std::vector<uint8_t> packet(s.sizes.cbHeader+count+s.sizes.cbTrailer);
    std::copy_n(bytes.begin(),count,packet.begin()+s.sizes.cbHeader);
    SecBuffer buffers[4]={{s.sizes.cbHeader,SECBUFFER_STREAM_HEADER,packet.data()},
      {ULONG(count),SECBUFFER_DATA,packet.data()+s.sizes.cbHeader},
      {s.sizes.cbTrailer,SECBUFFER_STREAM_TRAILER,packet.data()+s.sizes.cbHeader+count},{0,SECBUFFER_EMPTY,nullptr}};
    SecBufferDesc desc{SECBUFFER_VERSION,4,buffers};
    if(EncryptMessage(&s.context,0,&desc,0)!=SEC_E_OK)s.Fail("Relay TLS encryption failed.");
    for(unsigned i=0;i<3;++i)s.Send({static_cast<uint8_t*>(buffers[i].pvBuffer),buffers[i].cbBuffer});
    bytes=bytes.subspan(count);
  }
}
bool CoopTlsStream::Read(std::vector<uint8_t>& bytes){
  auto& s=*impl_;bytes.clear();if(!s.verified)s.Fail("Relay TLS connection is not verified.");if(s.closed)return false;
  for(;;){
    if(s.encrypted.empty())s.Receive();
    SecBuffer buffers[4]={{ULONG(s.encrypted.size()),SECBUFFER_DATA,s.encrypted.data()},
      {0,SECBUFFER_EMPTY,nullptr},{0,SECBUFFER_EMPTY,nullptr},{0,SECBUFFER_EMPTY,nullptr}};
    SecBufferDesc desc{SECBUFFER_VERSION,4,buffers};
    const auto status=DecryptMessage(&s.context,&desc,0,nullptr);
    if(status==SEC_E_INCOMPLETE_MESSAGE){s.Receive();continue;}
    if(status==SEC_I_CONTEXT_EXPIRED){s.closed=true;return false;}
    if(status!=SEC_E_OK)s.Fail("Relay TLS decryption failed.");
    for(const auto& buffer:buffers)if(buffer.BufferType==SECBUFFER_DATA&&buffer.cbBuffer){
      const auto* data=static_cast<const uint8_t*>(buffer.pvBuffer);bytes.insert(bytes.end(),data,data+buffer.cbBuffer);
    }
    s.KeepExtra(buffers,4);if(!bytes.empty())return true;
  }
}
}
