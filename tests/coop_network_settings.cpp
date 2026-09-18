#include "src/coop_network_settings.h"
#include <windows.h>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace aot;
void Check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
int main(){
  const auto folder=std::filesystem::temp_directory_path()/("aot-coop-settings-"+std::to_string(GetCurrentProcessId()));
  try{
    Check(!std::filesystem::exists(folder),"Require fresh test folder");
    Check(LoadCoopNetworkSettings(folder),"Missing settings should use defaults");
    Check(SavedCoopNetworkSettings()==CoopNetworkSettings{},"Default settings");
    CoopNetworkSettings settings{"https://directory.example:443/","tls://relay.example:37004"};std::string error;
    Check(SaveCoopNetworkSettings(settings,error),"Save valid endpoints");
    Check(LoadCoopNetworkSettings(folder)&&SavedCoopNetworkSettings()==settings,"Persistent endpoint round trip");
    for(auto bad:{"http://remote.example:9","https://user:secret@host:443","https://host/path","https://host:0","https://host?query","https://host/#fragment"}){
      auto candidate=settings;candidate.directory=bad;
      Check(!SaveCoopNetworkSettings(candidate,error)&&!error.empty()&&SavedCoopNetworkSettings()==settings,"Invalid directory changed preferences");
    }
    for(auto bad:{"tcp://127.0.0.1:37004","tls://host:0","tls://host:37004/path"}){
      auto candidate=settings;candidate.relay=bad;Check(!SaveCoopNetworkSettings(candidate,error),"Invalid relay saved");
    }
    Check(CoopNetworkSettings{"http://127.0.0.1:37002",""}.Error().empty(),"Loopback development service");
    SetEnvironmentVariableA("AOT_COOP_DIRECTORY_URL","http://127.0.0.1:39999");
    // getenv uses the C runtime's environment, so test the same API as the game.
    _putenv_s("AOT_COOP_DIRECTORY_URL","http://127.0.0.1:39999");
    Check(CoopNetworkOverridesActive()&&EffectiveCoopNetworkSettings().directory=="http://127.0.0.1:39999"&&SavedCoopNetworkSettings()==settings,"Test override persisted or ignored");
    _putenv_s("AOT_COOP_DIRECTORY_URL","");
    Check(EffectiveCoopNetworkSettings().directory==settings.directory,"Saved directory not restored");
    {std::ofstream file(folder/"coop-network.json");file<<"{bad json";}
    Check(!LoadCoopNetworkSettings(folder)&&SavedCoopNetworkSettings()==CoopNetworkSettings{},"Corrupt file should not preserve stale settings");
    Check(SaveCoopNetworkSettings({},error)&&LoadCoopNetworkSettings(folder),"Blank service configuration");
    std::filesystem::remove(folder/"coop-network.json");std::filesystem::remove(folder);
    std::cout<<"Network endpoint persistence, validation and test overrides passed\n";return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
