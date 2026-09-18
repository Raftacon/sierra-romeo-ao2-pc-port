#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef CERT_CHAIN_PARA_HAS_EXTRA_FIELDS
#define CERT_CHAIN_PARA_HAS_EXTRA_FIELDS
#endif
#include <windows.h>
#include <wincrypt.h>
#include <string>

namespace aot {
// Validate the certificate returned by Schannel before releasing application
// bytes. A null engine uses Windows trust; tests supply an isolated root engine.
// Does not install certificates or disable hostname/expiry checks.
bool VerifyCoopRelayCertificate(PCCERT_CONTEXT certificate,const std::wstring& hostname,
                               HCERTCHAINENGINE engine=nullptr,const FILETIME* verification_time=nullptr);
}
