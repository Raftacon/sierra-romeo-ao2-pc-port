#include "coop_connection_ui.h"
#include "coop_discovery.h"
#include "coop_directory_worker.h"
#include "coop_network_settings.h"
#include "coop_browser_filter.h"
#include <rex/ui/immediate_drawer.h>
#include <imgui.h>
#include <array>
#include <algorithm>
#include <cstring>
#include <utility>
#include <atomic>
#include <chrono>
#include <deque>
#include <fstream>
#include <memory>
#include <mutex>
#include <vector>

namespace aot {
namespace {
std::mutex mutex;
bool requested=false, visible=false, begin=false, close=false, private_room=false;
std::optional<CoopConnectionResult> submitted, completed;
std::deque<uint16_t> inputs;
uint16_t previous=0, blocked=0;
std::chrono::steady_clock::time_point repeat_at;
std::chrono::steady_clock::time_point open_after;
std::string room_address, room_invite;
std::string room_relay_invite;
std::string room_relay_notice;
std::string directory_notice;
bool copy_requested=false;

class Overlay final : public rex::ui::ImGuiDialog {
 public:
  Overlay(rex::ui::ImGuiDrawer* drawer,rex::ui::ImmediateDrawer* immediate,const std::filesystem::path& branding)
      : ImGuiDialog(drawer) {
    std::ifstream file(branding/"pc-font.rgba",std::ios::binary);
    uint32_t header[4]{}; file.read(reinterpret_cast<char*>(header),sizeof(header));
    if (!file || !header[0] || header[0]>4096 || !header[1] || header[1]>4096 || header[2]!=256) return;
    width_=header[0]; height_=header[1];
    file.read(reinterpret_cast<char*>(glyphs_.data()),sizeof(glyphs_));
    std::vector<uint8_t> pixels(width_*height_*4); file.read(reinterpret_cast<char*>(pixels.data()),pixels.size());
    if (file) font_=immediate->CreateTexture(width_,height_,rex::ui::ImmediateTextureFilter::kLinear,false,pixels.data());
  }
  void OnDraw(ImGuiIO& io) override {
    std::lock_guard lock(mutex);
    if (begin) { begin=false; selected_=key_=0; editing_=-1; browsing_=false; network_settings_=false; discovery_.Stop(); directory_.CancelSearch();directory_.SetEndpoint(CoopDirectoryEndpoint()); status_.clear(); }
    auto* draw=ImGui::GetForegroundDrawList();
    const auto scale=io.DisplaySize.y/720.f, cx=io.DisplaySize.x/2;
    if (!visible) {
      if (room_address.empty()) return;
      if (copy_requested) { ImGui::SetClipboardText(!room_relay_invite.empty()?room_relay_invite.c_str():room_invite.empty()?room_address.c_str():room_invite.c_str()); copy_requested=false; }
      draw->AddRectFilled({10*scale,40*scale},{590*scale,108*scale},IM_COL32(15,20,22,210));
      Text(draw,"Host address: "+room_address,20*scale,48*scale,.66f*scale);
      Text(draw,!room_relay_notice.empty()?room_relay_notice:!room_relay_invite.empty()?"Private online invitation ready. Share it with your partner.":room_invite.empty()?(directory_notice.empty()?"Public room - share your address with your partner.":directory_notice):"Invitation: "+room_invite,20*scale,68*scale,.56f*scale);
      Text(draw,!room_relay_invite.empty()?"Back: copy online invitation":room_invite.empty()?"Back: copy address":!room_relay_notice.empty()?"Back: copy local invitation":"Back: copy invitation",20*scale,88*scale,.6f*scale);
      return;
    }
    auto events=std::move(inputs); inputs.clear();
    for (auto buttons:events) { Handle(buttons); if (close) break; }
    const float x=cx-265*scale;
    if (browsing_) {
      discovery_.Poll(Now());
      const auto internet=directory_.Snapshot();
      const auto rooms=public_search_?FilterCoopRooms(internet.rooms,checkpoint_filter_,difficulty_filter_):discovery_.Rooms();
      room_selected_=rooms.empty()?0:std::min(room_selected_,rooms.size()-1);
      Text(draw,public_search_?"Public co-op games":"Games on your network",x,276*scale,.8f*scale);
      if(public_search_)Text(draw,(checkpoint_filter_.empty()?"Any checkpoint":checkpoint_filter_)+std::string("   |   ")+CoopDifficultyLabel(difficulty_filter_),x,301*scale,.58f*scale);
      const size_t per_page=public_search_?3:4,start=(room_selected_/per_page)*per_page;
      for(size_t i=start;i<std::min(rooms.size(),start+per_page);++i) {
        const auto y=((public_search_?326:309)+(i-start)*32)*scale;
        if(i==room_selected_)draw->AddRectFilled({x-9*scale,y-3*scale},{cx+274*scale,y+27*scale},IM_COL32(180,190,195,55));
        const auto map=CoopDiscoveryMap(rooms[i].map);
        Text(draw,rooms[i].name+" - "+(map=="Continue"?map:"Checkpoint "+map),x,y,.65f*scale);
        Text(draw,(rooms[i].relay?"Online relay":rooms[i].address+":"+std::to_string(rooms[i].port))+"  |  "+CoopDifficultyLabel(rooms[i].difficulty),x,y+16*scale,.5f*scale);
      }
      const auto error=public_search_?internet.search_error:discovery_.Error();
      Text(draw,(public_search_?internet.searching:discovery_.Searching())?"Searching...":!error.empty()?error:rooms.empty()?"No games found. Try Join by address.":private_room?"Select a host. Private games still need an invitation.":"Select a host, then Continue to join.",x,451*scale,.52f*scale);
      Text(draw,"D-pad: select   A: choose   X: refresh   B: back",x,479*scale,.58f*scale);
      if(public_search_)Text(draw,"Y: checkpoint   LB / RB: difficulty",x,464*scale,.52f*scale);
      return;
    }
    if (editing_>=0) {
      Text(draw,Label(editing_),x,276*scale,.8f*scale);
      const auto field=Field();
      Text(draw,editing_==4&&draft_.relay?"Online invitation loaded":(field.size()>58?"..."+field.substr(field.size()-58):field)+"_",x,301*scale,.72f*scale);
      const auto keys=Keys();
      for (unsigned i=0;i<keys.size();++i) {
        const float px=x+(i%10)*51*scale, py=(332+(i/10)*19)*scale;
        if (i==key_) draw->AddRectFilled({px-4*scale,py-2*scale},{px+43*scale,py+16*scale},IM_COL32(180,190,195,85));
        Text(draw,keys[i]==' '?"space":std::string(1,keys[i]),px,py,.68f*scale);
      }
      Text(draw,editing_>=8?"A: type  X: erase  Y: paste  LB: clear  Start: done  B: undo":"A: type   X: erase   Y: paste   Start: done   B: undo",x,479*scale,.58f*scale);
    } else {
      const auto rows=Rows();
      for (unsigned i=0;i<rows.size();++i) {
        const auto row=rows[i]; const float y=(282+i*23)*scale;
        if (i==selected_) draw->AddRectFilled({x-9*scale,y-3*scale},{cx+274*scale,y+20*scale},IM_COL32(180,190,195,55));
        Text(draw,Label(row),x,y,.76f*scale);
        auto value=Value(row);
        Text(draw,value,cx+260*scale-Width(value,.66f*scale),y,.66f*scale);
      }
      Text(draw,network_settings_?"D-pad: select   A: edit / apply   B: discard":"A: edit / continue   X: online settings   B: cancel",x,479*scale,.6f*scale);
    }
    const auto help=status_.empty()?(network_settings_?
        (CoopNetworkOverridesActive()?"Test overrides active; saved changes apply to normal launches.":"Blank disables that service. Apply saves to this profile."):
        editing_>=0?"Use the D-pad to select a character.":draft_.join?
        (draft_.relay?"Connect to the selected online game.":"Connect directly to your partner's host address."):"Next: choose the campaign checkpoint and difficulty."):status_;
    Text(draw,help,x,458*scale,.56f*scale);
  }
 private:
  std::vector<int> Rows() const {
    if(network_settings_)return {8,9,10};
    if (!draft_.join) return {0,1,3,5};
    return private_room?std::vector<int>{0,6,1,2,3,4,5}:std::vector<int>{0,7,6,1,2,3,5};
  }
  const char* Label(int row) const {
    static const char* labels[]={"Connection","Player name","Host address","Port","Invitation","Continue","Find on my network","Find public games","Public directory","Online relay","Apply settings"}; return labels[row];
  }
  std::string Value(int row) const {
    if(row==8||row==9){const auto& value=row==8?network_draft_.directory:network_draft_.relay;return value.empty()?"Not configured":value.size()>31?value.substr(0,28)+"...":value;}
    if(row==10)return "Save";
    switch(row) {case 0:return draft_.join?"Join game":"Host game";case 1:return draft_.name;case 2:return draft_.relay?"Online relay":draft_.address;
      case 3:return draft_.join&&draft_.relay?"Automatic":draft_.port;case 4:return draft_.relay?"Online invitation loaded":draft_.invite.empty()?"Enter private code":draft_.invite;case 6:return "Search LAN";case 7:return "Search online";default:return draft_.join?"Join lobby":"Campaign setup";}
  }
  std::string& Field() {
    if(editing_==8)return network_draft_.directory;
    if(editing_==9)return network_draft_.relay;
    switch(editing_) {case 1:return draft_.name;case 2:return draft_.address;case 3:return draft_.port;default:return draft_.invite;}
  }
  std::string_view Keys() const {
    if(editing_>=8)return "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/.-_";
    if (editing_==2) return "0123456789.";
    if (editing_==3) return "0123456789";
    if (editing_==4) return "0123456789abcdef";
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 _.-";
  }
  size_t Limit() const {return editing_>=8?320:editing_==2?15:editing_==3?5:32;}
  void Handle(uint16_t buttons) {
    if (submitted) return;
    if(browsing_) {
      if(buttons&0x2000){browsing_=false;discovery_.Stop();directory_.CancelSearch();return;}
      if(buttons&0x4000){room_selected_=0;if(public_search_)directory_.Search();else discovery_.Search(private_room?CoopVisibility::Private:CoopVisibility::Public,Now());return;}
      const auto internet=directory_.Snapshot();
      if(public_search_ && (buttons&(0x8000|0x0100|0x0200))){
        if(buttons&0x8000){const auto choices=CoopCheckpointChoices(internet.rooms);const auto current=std::find(choices.begin(),choices.end(),checkpoint_filter_);checkpoint_filter_=current==choices.end()||std::next(current)==choices.end()?choices.front():*std::next(current);}
        if(buttons&0x0200)difficulty_filter_=(difficulty_filter_+2)%4-1;
        if(buttons&0x0100)difficulty_filter_=(difficulty_filter_+4)%4-1;
        room_selected_=0;return;
      }
      const auto rooms=public_search_?FilterCoopRooms(internet.rooms,checkpoint_filter_,difficulty_filter_):discovery_.Rooms();
      if(rooms.empty())return;
      room_selected_=std::min(room_selected_,rooms.size()-1);
      if(buttons&1)room_selected_=(room_selected_+rooms.size()-1)%rooms.size();
      if(buttons&2)room_selected_=(room_selected_+1)%rooms.size();
      if(buttons&0x1000){
        const auto& room=rooms[room_selected_];draft_.address=room.address;draft_.port=std::to_string(room.port);
        draft_.relay=room.relay;
        draft_.invite.clear();browsing_=false;discovery_.Stop();status_=private_room?"Host selected. Enter the host's private invitation.":"Host selected. Choose Continue to join.";
      }
      return;
    }
    if (editing_>=0) {
      if (buttons&0x2000) {draft_=before_connection_;network_draft_=before_network_;editing_=-1;status_.clear();return;}
      if (buttons&0x0010) {editing_=-1;return;}
      if(editing_>=8 && (buttons&0x0100))Field().clear();
      if ((buttons&0x4000) && !Field().empty()) {Field().pop_back();if(editing_==4)draft_.relay.reset();}
      const auto keys=Keys();
      if (buttons&0x8000) {
        const char* clipboard=ImGui::GetClipboardText();
        if (clipboard) {
          const auto size=strnlen(clipboard,1025);
          const std::string_view text(clipboard,size);
          const auto allowed=editing_==4?std::string_view("0123456789abcdefABCDEF"):keys;
          if (editing_==2) {
            if (draft_.PasteAddress(text)) status_.clear();
            else status_="Clipboard must contain an IPv4 address or address:port.";
          }
          else if(editing_==4) {
            if(draft_.PasteInvitation(text))status_=draft_.relay?"Online invitation loaded. Choose Start, then Continue.":"Private invitation loaded.";
            else status_="Clipboard must contain a private code or an online invitation.";
          }
          else if (size<=Limit() && text.find_first_not_of(allowed)==text.npos) {Field()=text;status_.clear();}
          else status_="Clipboard text does not fit this field.";
        }
      }
      if (buttons&1) key_=(key_+keys.size()-std::min<size_t>(10,keys.size()))%keys.size();
      if (buttons&2) key_=(key_+10)%keys.size();
      if (buttons&4) key_=(key_+keys.size()-1)%keys.size();
      if (buttons&8) key_=(key_+1)%keys.size();
      if ((buttons&0x1000) && Field().size()<Limit()) {Field()+=keys[key_];if(editing_==4)draft_.relay.reset();}
      return;
    }
    if(network_settings_){
      if(buttons&0x2000){network_settings_=false;selected_=0;status_.clear();return;}
      if(buttons&1)selected_=(selected_+2)%3;
      if(buttons&2)selected_=(selected_+1)%3;
      if(buttons&0x1000){
        if(selected_==2){
          if(SaveCoopNetworkSettings(network_draft_,status_)){
            directory_.SetEndpoint(CoopDirectoryEndpoint());network_settings_=false;selected_=0;
            status_=CoopNetworkOverridesActive()?"Saved. Test overrides remain active for this launch.":"Online settings saved.";
          }
        }else{before_network_=network_draft_;before_connection_=draft_;editing_=8+int(selected_);key_=0;status_.clear();}
      }
      return;
    }
    if(buttons&0x4000){network_settings_=true;network_draft_=SavedCoopNetworkSettings();selected_=0;status_.clear();return;}
    if (buttons&0x2000) {submitted=CoopConnectionResult{};close=true;return;}
    auto rows=Rows();
    if (buttons&1) selected_=(selected_+rows.size()-1)%rows.size();
    if (buttons&2) selected_=(selected_+1)%rows.size();
    const auto row=rows[selected_];
    if (row==0 && (buttons&(0x1000|12))) {draft_.join=!draft_.join;status_.clear();return;}
    if (!(buttons&0x1000)) return;
    if(row==6||row==7){browsing_=true;public_search_=row==7;room_selected_=0;if(public_search_)directory_.Search();else discovery_.Search(private_room?CoopVisibility::Private:CoopVisibility::Public,Now());return;}
    if (row==5) {
      status_=draft_.Error(private_room);
      if (status_.empty()) {submitted=CoopConnectionResult{false,draft_};close=true;}
    } else {before_connection_=draft_;before_network_=network_draft_;if(row==2||row==3)draft_.relay.reset();editing_=row;key_=0;status_.clear();}
  }
  float Width(std::string_view text,float scale) const {
    float value=0; for (unsigned char c:text) value+=(c==' '?7:glyphs_[c].w+1)*scale; return value;
  }
  void Text(ImDrawList* draw,std::string_view text,float x,float y,float scale) {
    if (!font_) {draw->AddText({x,y},IM_COL32_WHITE,text.data(),text.data()+text.size());return;}
    for (unsigned char c:text) {
      const auto& g=glyphs_[c];
      if (c==' ') {x+=7*scale;continue;}
      if (g.w && g.h) draw->AddImage(reinterpret_cast<ImTextureID>(font_.get()),{x,y},{x+g.w*scale,y+g.h*scale},
          {float(g.u)/width_,float(g.v)/height_},{float(g.u+g.w)/width_,float(g.v+g.h)/height_});
      x+=(g.w+1)*scale;
    }
  }
  struct Glyph {uint32_t u,v,w,h;};
  static uint64_t Now(){return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
  CoopDiscovery discovery_;
  CoopDirectoryWorker directory_{CoopDirectoryEndpoint()};
  bool public_search_=false;
  std::string checkpoint_filter_;
  int difficulty_filter_=-1;
  bool browsing_=false;
  size_t room_selected_=0;
  std::array<Glyph,256> glyphs_{};
  std::unique_ptr<rex::ui::ImmediateTexture> font_;
  uint32_t width_=0,height_=0;
  CoopConnection draft_;
  CoopConnection before_connection_;
  bool network_settings_=false;
  CoopNetworkSettings network_draft_,before_network_;
  size_t selected_=0,key_=0;
  int editing_=-1;
  std::string status_;
};
}
void CreateCoopConnectionOverlay(rex::ui::ImGuiDrawer* drawer,rex::ui::ImmediateDrawer* immediate,const std::filesystem::path& branding) {
  new Overlay(drawer,immediate,branding);
}
void RequestCoopConnection(bool private_value) {
  std::lock_guard lock(mutex);private_room=private_value;requested=true;submitted.reset();completed.reset();
  // The original connection sequence opens its message box on the next tick.
  // Let that scene settle before putting our owned panel above it.
  open_after=std::chrono::steady_clock::now()+std::chrono::milliseconds(500);
}
bool TakeCoopConnectionOpenRequest() {
  std::lock_guard lock(mutex);
  return std::chrono::steady_clock::now()>=open_after && std::exchange(requested,false);
}
void BeginCoopConnection() {std::lock_guard lock(mutex);begin=visible=true;close=false;inputs.clear();}
bool TakeCoopConnectionCloseRequest() {std::lock_guard lock(mutex);return std::exchange(close,false);}
void EndCoopConnection() {std::lock_guard lock(mutex);visible=false;inputs.clear();blocked|=previous;completed=submitted.value_or(CoopConnectionResult{});submitted.reset();}
std::optional<CoopConnectionResult> TakeCoopConnectionResult() {std::lock_guard lock(mutex);return std::exchange(completed,std::nullopt);}
void SetCoopInvitation(std::string address,uint16_t port,std::string invite) {std::lock_guard lock(mutex);room_address=address+":"+std::to_string(port);room_invite=std::move(invite);}
void ClearCoopInvitation() {std::lock_guard lock(mutex);room_address.clear();room_invite.clear();room_relay_invite.clear();room_relay_notice.clear();copy_requested=false;}
void SetCoopRelayInvitation(std::string invitation,std::string notice) {std::lock_guard lock(mutex);room_relay_invite=std::move(invitation);room_relay_notice=std::move(notice);}
void SetCoopDirectoryNotice(std::string notice){std::lock_guard lock(mutex);directory_notice=std::move(notice);}
void FilterCoopConnectionInput(rex::input::X_INPUT_STATE& state) {
  std::lock_guard lock(mutex);
  uint16_t buttons=state.gamepad.buttons;
  if (state.gamepad.thumb_ly>18000) buttons|=1;
  if (state.gamepad.thumb_ly<-18000) buttons|=2;
  if (state.gamepad.thumb_lx<-18000) buttons|=4;
  if (state.gamepad.thumb_lx>18000) buttons|=8;
  uint16_t pressed=buttons&~previous;
  const auto now=std::chrono::steady_clock::now();
  if ((buttons&15)!=(previous&15)) repeat_at=now+std::chrono::milliseconds(400);
  else if ((buttons&15) && now>=repeat_at) {pressed|=buttons&15;repeat_at=now+std::chrono::milliseconds(130);}
  previous=buttons;blocked&=buttons;
  state.gamepad.buttons=state.gamepad.buttons&~blocked;
  if (!visible) {
    if (!room_address.empty()) {if (pressed&0x20) copy_requested=true;state.gamepad.buttons=state.gamepad.buttons&~0x20;}
    return;
  }
  if (pressed && inputs.size()<32) inputs.push_back(pressed);
  state.gamepad={};
}
}
