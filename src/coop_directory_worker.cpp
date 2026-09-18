#include "coop_directory_worker.h"
#include "coop_network_settings.h"
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <thread>

namespace aot {
std::wstring CoopDirectoryEndpoint(){
  const std::string text=EffectiveCoopNetworkSettings().directory;
  if(text.size()>2048||text.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/.-_")!=text.npos)return {};
  return std::wstring(text.begin(),text.end());
}
struct CoopDirectoryWorker::Impl {
  mutable std::mutex mutex;
  std::condition_variable changed;
  std::wstring endpoint;
  std::wstring requested_endpoint;
  bool endpoint_dirty=false;
  bool stopping=false,search_pending=false,room_dirty=false;
  uint64_t search_generation=0,room_generation=0;
  std::optional<CoopDiscoveredRoom> room;
  CoopDirectoryStatus status;
  std::thread thread;
  explicit Impl(std::wstring value):endpoint(value),requested_endpoint(std::move(value)),thread([this]{Run();}){}
  ~Impl(){ {std::lock_guard lock(mutex);stopping=true;}changed.notify_one();thread.join();}
  void Run(){
    CoopDirectoryClient client(endpoint);CoopDirectoryLease lease;
    auto refresh=std::chrono::steady_clock::now();
    for(;;){
      std::unique_lock lock(mutex);
      changed.wait_until(lock,room?refresh:std::chrono::steady_clock::time_point::max(),[&]{return stopping||search_pending||room_dirty||endpoint_dirty;});
      if(stopping){lock.unlock();if(!lease.id.empty())client.Remove(lease);return;}
      if(endpoint_dirty){
        auto next=requested_endpoint;endpoint_dirty=false;lock.unlock();
        if(!lease.id.empty())client.Remove(lease);lease={};
        endpoint=std::move(next);client=CoopDirectoryClient(endpoint);
        lock.lock();room_dirty=true;refresh=std::chrono::steady_clock::now();continue;
      }
      if(search_pending){
        const auto generation=search_generation;search_pending=false;lock.unlock();
        std::vector<CoopDiscoveredRoom> rooms;const bool ok=!endpoint.empty()&&client.Search(rooms);
        const auto error=endpoint.empty()?"Public matchmaking service is not configured.":client.Error();
        lock.lock();if(generation==search_generation){status.searching=false;status.rooms=std::move(rooms);status.search_error=ok?"":error;}
        continue;
      }
      auto desired=room;const auto generation=room_generation;room_dirty=false;lock.unlock();
      bool ok=true;std::string operation_error;
      if(!desired){if(!lease.id.empty())ok=client.Remove(lease);lease={};}
      else if(endpoint.empty())ok=false;
      else if(lease.id.empty())ok=client.Create(*desired,lease);
      else {ok=client.Update(*desired,lease,true);if(!ok){operation_error=client.Error();client.Remove(lease);lease={};}}
      const auto error=endpoint.empty()?"Public matchmaking service is not configured.":operation_error.empty()?client.Error():operation_error;
      refresh=std::chrono::steady_clock::now()+std::chrono::seconds(ok?15:5);
      lock.lock();if(generation==room_generation){status.advertised=desired.has_value()&&ok;status.host_error=ok?"":error;}
    }
  }
};
CoopDirectoryWorker::CoopDirectoryWorker(std::wstring endpoint):impl_(std::make_unique<Impl>(std::move(endpoint))){}
CoopDirectoryWorker::~CoopDirectoryWorker()=default;
void CoopDirectoryWorker::SetEndpoint(std::wstring endpoint){
  auto& s=*impl_;{std::lock_guard lock(s.mutex);if(s.requested_endpoint==endpoint)return;
    s.requested_endpoint=std::move(endpoint);s.endpoint_dirty=true;++s.search_generation;++s.room_generation;
    s.status.rooms.clear();s.status.search_error.clear();s.status.host_error.clear();s.status.advertised=false;
    s.status.searching=s.search_pending;
  }s.changed.notify_one();
}
void CoopDirectoryWorker::Search(){auto& s=*impl_;{std::lock_guard lock(s.mutex);++s.search_generation;s.search_pending=true;s.status.searching=true;s.status.rooms.clear();s.status.search_error.clear();}s.changed.notify_one();}
void CoopDirectoryWorker::CancelSearch(){auto& s=*impl_;std::lock_guard lock(s.mutex);++s.search_generation;s.search_pending=false;s.status.searching=false;s.status.rooms.clear();s.status.search_error.clear();}
void CoopDirectoryWorker::SetRoom(std::optional<CoopDiscoveredRoom> room){auto& s=*impl_;{std::lock_guard lock(s.mutex);if(s.room==room)return;s.room=std::move(room);++s.room_generation;s.room_dirty=true;s.status.advertised=false;}s.changed.notify_one();}
CoopDirectoryStatus CoopDirectoryWorker::Snapshot()const{std::lock_guard lock(impl_->mutex);return impl_->status;}
}
