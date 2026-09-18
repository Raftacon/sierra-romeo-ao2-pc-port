#pragma once
#include <filesystem>
#include <string>

namespace aot {
struct CoopNetworkSettings {
  std::string directory,relay;
  bool operator==(const CoopNetworkSettings&) const=default;
  std::string Error() const;
};
bool LoadCoopNetworkSettings(const std::filesystem::path& user_data);
CoopNetworkSettings SavedCoopNetworkSettings();
CoopNetworkSettings EffectiveCoopNetworkSettings();
bool CoopNetworkOverridesActive();
bool SaveCoopNetworkSettings(const CoopNetworkSettings&,std::string& error);
}
