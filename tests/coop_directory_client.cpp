#include "src/coop_directory_client.h"
#include "src/coop_directory_worker.h"
#include <chrono>
#include <thread>
#include <iostream>
#include <stdexcept>
using namespace aot;
void Check(bool value,const char* label){if(!value)throw std::runtime_error(label);}
int main(int argc,char** argv){try{
  Check(argc==2||argc==3,"local test endpoint required");
  const std::string endpoint=argv[1];CoopDirectoryClient client(std::wstring(endpoint.begin(),endpoint.end()));
  if(argc==3 && std::string(argv[2])=="worker"){
    const auto wait=[](auto predicate){const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(8);while(!predicate()){Check(std::chrono::steady_clock::now()<deadline,"worker deadline");std::this_thread::sleep_for(std::chrono::milliseconds(10));}};
    CoopDirectoryWorker host(std::wstring(endpoint.begin(),endpoint.end())),browser(std::wstring(endpoint.begin(),endpoint.end()));
    host.SetRoom(CoopDiscoveredRoom{"","Worker Host","05_03",37001,0,CoopVisibility::Public});
    wait([&]{return host.Snapshot().advertised;});
    browser.Search();wait([&]{return !browser.Snapshot().searching;});Check(browser.Snapshot().rooms.size()==1,"worker search results");
    browser.Search();browser.CancelSearch();std::this_thread::sleep_for(std::chrono::milliseconds(200));Check(browser.Snapshot().rooms.empty()&&!browser.Snapshot().searching,"cancel ignores late results");
    host.SetRoom(std::nullopt);
    std::vector<CoopDiscoveredRoom> rooms;wait([&]{return client.Search(rooms)&&rooms.empty();});
    CoopDirectoryWorker missing(L"");missing.Search();wait([&]{return !missing.Snapshot().searching;});Check(!missing.Snapshot().search_error.empty(),"unconfigured service feedback");
    missing.SetEndpoint(std::wstring(endpoint.begin(),endpoint.end()));missing.Search();wait([&]{return !missing.Snapshot().searching;});
    Check(missing.Snapshot().search_error.empty(),"Worker did not accept newly configured directory");
    host.SetRoom(CoopDiscoveredRoom{"","Worker Host","05_03",37001,0,CoopVisibility::Public});wait([&]{return host.Snapshot().advertised;});
    host.SetEndpoint(L"");wait([&]{return client.Search(rooms)&&rooms.empty();});Check(!host.Snapshot().advertised,"Old directory retained advertisement after endpoint switch");
    host.SetRoom(std::nullopt);
    std::cout<<"Directory worker registration, search, cancellation and withdrawal passed\n";return 0;
  }
  if(argc==3){std::vector<CoopDiscoveredRoom> rooms;Check(!client.Search(rooms)&&rooms.empty(),"invalid response must fail without rooms");return 0;}
  CoopDiscoveredRoom room{"","Public Host","Checkpoint?LoadSaveGame?FromCheckpoint=05_03?Difficulty=1",37001,1,CoopVisibility::Public};
  CoopDirectoryLease lease;std::vector<CoopDiscoveredRoom> rooms;
  Check(client.Search(rooms)&&rooms.empty(),"initial empty directory");
  Check(client.Create(room,lease),"register room");
  Check(client.Search(rooms)&&rooms.size()==1,"search registered room");
  Check(rooms[0].address=="127.0.0.1"&&rooms[0].map=="05_03"&&rooms[0].port==37001&&rooms[0].difficulty==1,"endpoint and checkpoint");
  auto invalid=lease;invalid.token=std::string(43,'a');Check(!client.Remove(invalid),"wrong owner rejected");
  Check(client.Update(room,lease,false),"mark unavailable");
  Check(client.Search(rooms)&&rooms.empty(),"unavailable excluded");
  Check(client.Update(room,lease,true),"reopen");
  Check(client.Search(rooms)&&rooms.size()==1,"reopened visible");
  Check(client.Remove(lease),"remove room");Check(client.Search(rooms)&&rooms.empty(),"removed excluded");
  room.relay=CoopRelayRoute{"tls://relay.example.test:37004",std::string(32,'a'),std::string(43,'b')};
  Check(client.Create(room,lease),"register relay metadata");
  Check(client.Search(rooms)&&rooms.size()==1&&rooms[0].relay==room.relay,"relay descriptor roundtrip");
  Check(client.Remove(lease),"remove relay room");
  room.relay->endpoint="tcp://remote.test:37004";Check(!client.Create(room,lease),"remote plaintext relay refused");room.relay.reset();
  room.visibility=CoopVisibility::Private;Check(!client.Create(room,lease),"private room never published");
  CoopDirectoryClient unsafe(L"http://192.0.2.1:37002");Check(!unsafe.Search(rooms),"remote plaintext refused");
  std::cout<<"Native directory client: register, search, refresh, owner checks, removal and endpoint policy passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
