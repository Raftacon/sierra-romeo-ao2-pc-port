#pragma once
#include "coop_discovery.h"
#include <algorithm>

namespace aot {
// Empty checkpoint and difficulty -1 mean any. Filters never alter join data.
inline std::vector<CoopDiscoveredRoom> FilterCoopRooms(const std::vector<CoopDiscoveredRoom>& rooms,
    std::string_view checkpoint,int difficulty) {
  std::vector<CoopDiscoveredRoom> result;
  for(const auto& room:rooms)
    if((checkpoint.empty()||CoopDiscoveryMap(room.map)==checkpoint) &&
       (difficulty<0||room.difficulty==difficulty))result.push_back(room);
  return result;
}
inline std::vector<std::string> CoopCheckpointChoices(const std::vector<CoopDiscoveredRoom>& rooms){
  std::vector<std::string> result;
  for(const auto& room:rooms)result.push_back(CoopDiscoveryMap(room.map));
  std::sort(result.begin(),result.end());result.erase(std::unique(result.begin(),result.end()),result.end());
  result.insert(result.begin(),"");return result;
}
inline const char* CoopDifficultyLabel(int value){
  switch(value){case 0:return "Recruit";case 1:return "Contractor";case 2:return "Professional";default:return "Any difficulty";}
}
}
