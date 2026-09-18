#pragma once
#include "coop_directory_client.h"

namespace aot {
struct CoopDirectoryStatus {
  bool searching=false, advertised=false;
  std::string search_error, host_error;
  std::vector<CoopDiscoveredRoom> rooms;
};
// One owned worker, no detached requests. Public methods never perform HTTP.
class CoopDirectoryWorker {
 public:
  explicit CoopDirectoryWorker(std::wstring endpoint);
  ~CoopDirectoryWorker();
  void Search();
  void SetEndpoint(std::wstring endpoint);
  void CancelSearch();
  void SetRoom(std::optional<CoopDiscoveredRoom>);
  CoopDirectoryStatus Snapshot() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
std::wstring CoopDirectoryEndpoint();
}
