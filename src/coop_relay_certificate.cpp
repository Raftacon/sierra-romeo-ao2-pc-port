#include "coop_relay_certificate.h"

namespace aot {
bool VerifyCoopRelayCertificate(PCCERT_CONTEXT certificate,const std::wstring& hostname,HCERTCHAINENGINE engine,const FILETIME* verification_time){
  if(!certificate||hostname.empty()||hostname.size()>253||hostname.find(L'\0')!=hostname.npos)return false;
  CERT_CHAIN_PARA parameters{};parameters.cbSize=sizeof(parameters);
  LPSTR usage=const_cast<LPSTR>(szOID_PKIX_KP_SERVER_AUTH);
  parameters.RequestedUsage.dwType=USAGE_MATCH_TYPE_AND;
  parameters.RequestedUsage.Usage.cUsageIdentifier=1;
  parameters.RequestedUsage.Usage.rgpszUsageIdentifier=&usage;
  parameters.dwUrlRetrievalTimeout=2000;
  PCCERT_CHAIN_CONTEXT chain=nullptr;
  if(!CertGetCertificateChain(engine,certificate,const_cast<FILETIME*>(verification_time),certificate->hCertStore,&parameters,
      CERT_CHAIN_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT|CERT_CHAIN_REVOCATION_ACCUMULATIVE_TIMEOUT,nullptr,&chain))return false;
  SSL_EXTRA_CERT_CHAIN_POLICY_PARA ssl{};ssl.cbSize=sizeof(ssl);ssl.dwAuthType=AUTHTYPE_SERVER;
  ssl.pwszServerName=const_cast<wchar_t*>(hostname.c_str());
  CERT_CHAIN_POLICY_PARA policy{};policy.cbSize=sizeof(policy);policy.pvExtraPolicyPara=&ssl;
  CERT_CHAIN_POLICY_STATUS status{};status.cbSize=sizeof(status);
  const bool valid=CertVerifyCertificateChainPolicy(CERT_CHAIN_POLICY_SSL,chain,&policy,&status)&&status.dwError==0;
  CertFreeCertificateChain(chain);return valid;
}
}
