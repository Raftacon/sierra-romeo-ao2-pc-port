#pragma once
#include "coop_discovery.h"

namespace aot {
struct CoopDirectoryLease {std::string id, token;};
// Blocking, bounded HTTP operations: callers must use a worker, never UI/game ticks.
// HTTPS is required except for explicit loopback development endpoints.
class CoopDirectoryClient {
 public:
  explicit CoopDirectoryClient(std::wstring endpoint);
  bool Create(const CoopDiscoveredRoom&, CoopDirectoryLease&);
  bool Update(const CoopDiscoveredRoom&, const CoopDirectoryLease&, bool joinable);
  bool Remove(const CoopDirectoryLease&);
  bool Search(std::vector<CoopDiscoveredRoom>&);
  const std::string& Error() const {return error_;}
 private:
  std::wstring endpoint_;
  std::string error_;
  bool Request(const wchar_t* method,const std::string& path,const std::string& body,
               const std::string& token,std::string& response);
};
}
