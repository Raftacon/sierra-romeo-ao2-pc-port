#pragma once
#include <string_view>

namespace aot {
// Retail mid-mission UI supplies both checkpoint coordinates. Its Shell URL
// must also identify the menu phase so the original loader selects local UI
// simulation instead of comparing independent shop cameras as gameplay.
inline bool CoopNeedsMidmissionOption(std::string_view url) {
  const auto separator=url.find('?');
  auto map=url.substr(0,separator);
  const auto equal=[](std::string_view a,std::string_view b) {
    if (a.size()!=b.size()) return false;
    for (size_t i=0;i<a.size();++i) {
      const auto c=a[i]>='A' && a[i]<='Z' ? char(a[i]+('a'-'A')) : a[i];
      if (c!=b[i]) return false;
    }
    return true;
  };
  if ((!equal(map,"shell") && !equal(map,"shell.ao2")) || separator==url.npos) return false;
  bool level=false, sublevel=false;
  url.remove_prefix(separator+1);
  while (!url.empty()) {
    const auto end=url.find('?');
    const auto field=url.substr(0,end);
    url=end==url.npos ? std::string_view{} : url.substr(end+1);
    if (field=="midmission" || field.starts_with("midmission=")) return false;
    bool* seen=nullptr; std::string_view value;
    if (field.starts_with("MMSCP_LVL=")) { seen=&level; value=field.substr(10); }
    else if (field.starts_with("MMSCP_SUBLVL=")) { seen=&sublevel; value=field.substr(13); }
    if (!seen) continue;
    if (*seen || value.empty() || value.size()>10 || value.find_first_not_of("0123456789")!=value.npos) return false;
    *seen=true;
  }
  return level && sublevel;
}
}
