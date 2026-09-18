#include "src/coop_discovery.h"
#include "src/coop_browser_filter.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>
using namespace aot;
void Check(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
int main(){try{
  const std::vector<CoopDiscoveredRoom> sample={
    {"127.0.0.1","First","01_100",37001,0,CoopVisibility::Public},
    {"127.0.0.2","Second","05_03",37002,1,CoopVisibility::Public},
    {"127.0.0.3","Third","05_03",37003,2,CoopVisibility::Public}};
  Check(FilterCoopRooms(sample,"",-1)==sample,"any filters preserve join fields");
  Check(FilterCoopRooms(sample,"05_03",-1).size()==2,"checkpoint filter");
  Check(FilterCoopRooms(sample,"05_03",1)==std::vector<CoopDiscoveredRoom>{sample[1]},"combined filter preserves endpoint");
  Check(FilterCoopRooms(sample,"01_100",2).empty(),"empty filtered results");
  Check(CoopCheckpointChoices(sample)==std::vector<std::string>{"","01_100","05_03"},"unique checkpoint choices");
  CoopDiscovery host,private_client,public_client;
  constexpr uint16_t discovery_port=37963;
  Check(CoopDiscoveryMap("Checkpoint?LoadSaveGame?Difficulty=0?UseGlobalCheckpoint")=="Continue","saved checkpoint label");
  Check(CoopDiscoveryMap("Checkpoint?LoadSaveGame?CheckpointToLoad=01_100?Difficulty=0?")=="01_100","public filter checkpoint label");
  Check(host.Advertise({CoopVisibility::Private,"Checkpoint?LoadSaveGame?FromCheckpoint=05_03?Difficulty=1",1},"LAN Host",37964,discovery_port),"advertise");
  Check(private_client.Search(CoopVisibility::Private,1000,discovery_port),"private search");
  Check(public_client.Search(CoopVisibility::Public,1000,discovery_port),"public search");
  for(uint64_t now=1000;now<=4100;now+=25){
    private_client.Poll(now);public_client.Poll(now);host.Poll(now);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  Check(!private_client.Searching(),"bounded search");
  Check(!private_client.Rooms().empty(),"actual UDP response");
  Check(public_client.Rooms().empty(),"private rooms excluded from public search");
  for(const auto& room:private_client.Rooms()){
    Check(room.name=="LAN Host"&&room.port==37964&&room.map=="05_03"&&room.difficulty==1,"endpoint and settings");
    Check(!room.address.empty(),"source-derived IP");
  }
  host.Stop();
  Check(private_client.Search(CoopVisibility::Private,5000,discovery_port),"refresh");
  for(uint64_t now=5000;now<=8100;now+=25){private_client.Poll(now);std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  Check(private_client.Rooms().empty(),"withdrawn room disappears after refresh");
  Check(host.Advertise({CoopVisibility::Public,"01_100",0},"Public Host",37965,discovery_port),"restart public");
  Check(public_client.Search(CoopVisibility::Public,9000,discovery_port),"public refresh");
  for(uint64_t now=9000;now<=12100;now+=25){public_client.Poll(now);host.Poll(now);std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  Check(!public_client.Rooms().empty()&&public_client.Rooms()[0].name=="Public Host","public discovery");
  std::cout<<"LAN discovery: UDP responses, visibility, bounded search, refresh, withdrawal and restart passed\n";
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
