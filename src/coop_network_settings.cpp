#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include "coop_network_settings.h"
#include "coop_relay_route.h"
#include "vendor/nlohmann/json.hpp"
#include <cstdlib>
#include <fstream>
#include <mutex>

namespace aot {
namespace {
std::mutex settings_mutex;
CoopNetworkSettings saved;
std::filesystem::path settings_file;
bool DirectoryValid(std::string_view url){
  if(url.empty())return true;
  if(url.size()>320)return false;
  const bool secure=url.starts_with("https://");
  if(!secure&&!url.starts_with("http://"))return false;
  url.remove_prefix(secure?8:7);
  if(url.ends_with('/'))url.remove_suffix(1);
  const auto colon=url.find(':');const auto host=url.substr(0,colon);
  if(!secure&&host!="127.0.0.1"&&host!="localhost")return false;
  const std::string endpoint="tls://"+std::string(url)+(colon==url.npos?":443":"");
  return CoopRelayRoute{endpoint,std::string(32,'0'),std::string(43,'a')}.Valid();
}
}
std::string CoopNetworkSettings::Error()const{
  if(!DirectoryValid(directory))return "Directory needs an HTTPS address (local tests may use loopback HTTP).";
  if(!relay.empty()&&(relay.size()>320||!relay.starts_with("tls://")||
      !CoopRelayRoute{relay,std::string(32,'0'),std::string(43,'a')}.Valid()))
    return "Relay needs tls://hostname:port, or leave it blank for direct hosting.";
  return {};
}
bool LoadCoopNetworkSettings(const std::filesystem::path& user_data){
  std::lock_guard lock(settings_mutex);saved={};settings_file=user_data/"coop-network.json";
  std::error_code ec;if(!std::filesystem::exists(settings_file,ec))return !ec;
  try{
    std::ifstream file(settings_file,std::ios::binary|std::ios::ate);const auto size=file.tellg();
    if(!file||size<=0||size>4096)return false;
    std::string text(size_t(size),'\0');file.seekg(0);file.read(text.data(),text.size());if(!file)return false;
    const auto data=nlohmann::json::parse(text,[](int depth,nlohmann::json::parse_event_t,nlohmann::json&){if(depth>2)throw std::runtime_error("settings nesting");return true;});
    if(!data.is_object()||data.size()!=3||!data.at("version").is_number_integer()||data.at("version")!=1)return false;
    CoopNetworkSettings candidate{data.at("directory").get<std::string>(),data.at("relay").get<std::string>()};
    if(!candidate.Error().empty())return false;saved=std::move(candidate);return true;
  }catch(...){return false;}
}
CoopNetworkSettings SavedCoopNetworkSettings(){std::lock_guard lock(settings_mutex);return saved;}
CoopNetworkSettings EffectiveCoopNetworkSettings(){
  auto value=SavedCoopNetworkSettings();
  if(const auto* directory=std::getenv("AOT_COOP_DIRECTORY_URL"))value.directory=directory;
  if(const auto* relay=std::getenv("AOT_COOP_RELAY_ENDPOINT"))value.relay=relay;
  return value;
}
bool CoopNetworkOverridesActive(){return std::getenv("AOT_COOP_DIRECTORY_URL")||std::getenv("AOT_COOP_RELAY_ENDPOINT");}
bool SaveCoopNetworkSettings(const CoopNetworkSettings& value,std::string& error){
  error=value.Error();if(!error.empty())return false;
  std::lock_guard lock(settings_mutex);
  if(settings_file.empty()){error="Network preferences are not initialized.";return false;}
  std::error_code ec;std::filesystem::create_directories(settings_file.parent_path(),ec);
  if(ec){error="Cannot create the network preferences folder.";return false;}
  auto temporary=settings_file;temporary+=".tmp";
  const auto text=nlohmann::json{{"version",1},{"directory",value.directory},{"relay",value.relay}}.dump(2)+"\n";
  {std::ofstream file(temporary,std::ios::binary|std::ios::trunc);file<<text;file.flush();if(!file){error="Cannot write network preferences.";return false;}}
  if(!MoveFileExW(temporary.c_str(),settings_file.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)){
    error="Cannot replace network preferences.";std::filesystem::remove(temporary,ec);return false;
  }
  saved=value;return true;
}
}
