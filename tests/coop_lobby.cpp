#include "src/coop_lobby.h"
#include "src/coop_connection.h"
#include "src/coop_input_packet.h"
#include "src/coop_travel.h"
#include <winsock2.h>
#include <windows.h>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <thread>
#include <array>
#include <algorithm>
#include <source_location>

using namespace aot;
namespace {
void Check(bool value,const char* message) { if (!value) throw std::runtime_error(message); }
uint64_t Now() { return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count()); }
std::vector<uint8_t> InputPacket(unsigned sender) {
  // Type 0x55, frame 0, ack offset 0, one record with no variable events.
  std::vector<uint8_t> packet(23,0x80);
  packet[0]=0xD5; packet[1]=0x80; packet[2]=packet[3]=packet[4]=0;
  packet[6]=0x81;
  packet[8]=0; // Unsigned field.
  packet[10]=0; // Signed 16-bit zero = 0x8000.
  packet[12]=packet[13]=packet[14]=packet[15]=0; // Unsigned buttons.
  packet[18]=uint8_t(0x80+sender);
  return packet;
}
void InputBounds() {
  CoopConnection connection;
  Check(connection.PasteAddress("10.0.0.136:37002") && connection.address=="10.0.0.136" && connection.Port()==37002,"Pasted endpoint split");
  Check(!connection.PasteAddress("10.0.0.136:0") && connection.Port()==37002,"Invalid paste changed port");
  Check(!connection.PasteAddress("10.0.0.136:123:45") && connection.address=="10.0.0.136","Malformed endpoint accepted");
  Check(connection.PasteAddress("127.0.0.1") && connection.Port()==37002,"Address-only paste changed port");
  connection.port="37001";
  Check(connection.Error(true).empty() && connection.Port()==37001,"Default host connection");
  connection.join=true;
  Check(!connection.Error(true).empty() && connection.Error(false).empty(),"Private invitation requirement");
  connection.invite="0123456789abcdef0123456789ABCDEF";
  Check(connection.Error(true).empty(),"Valid private connection");
  connection.relay=CoopRelayRoute{"tls://relay.example:37004",std::string(32,'0'),std::string(43,'a')};
  connection.address="unused";
  Check(connection.Error(true).empty(),"Valid relay invitation rejected");
  const CoopPrivateInvitation invitation{*connection.relay,connection.invite};
  const auto share=invitation.Encode();
  for (const auto* stale_port:{"", "0", "65536", "37001x"}) {
    CoopConnection recovered;recovered.join=true;recovered.address="unfinished";recovered.port=stale_port;
    Check(recovered.PasteInvitation(share)&&recovered.Error(true).empty()&&recovered.Error(false).empty(),
          "Valid online invitation blocked by unused direct endpoint");
    recovered.join=false;
    Check(!recovered.Error(false).empty(),"Relay selection bypassed host listening port validation");
    recovered.join=true;
    Check(recovered.PasteInvitation(connection.invite)&&!recovered.Error(true).empty(),
          "Switching back to direct invitation bypassed endpoint validation");
    Check(recovered.PasteAddress("127.0.0.1:37001")&&recovered.Error(true).empty(),
          "Valid direct endpoint did not repair draft after online invitation");
  }
  CoopConnection pasted;pasted.join=true;
  Check(pasted.PasteInvitation(share)&&pasted.relay==connection.relay&&pasted.invite==connection.invite&&pasted.Error(true).empty(),"Private relay invitation round trip");
  for(const auto& invalid:{share+"|extra",std::string("aot2")+share.substr(4),share.substr(0,share.size()-1),std::string(513,'a')})
    Check(!pasted.PasteInvitation(invalid)&&pasted.relay==connection.relay&&pasted.invite==connection.invite,"Invalid invitation changed connection");
  Check(pasted.PasteInvitation(connection.invite)&&!pasted.relay,"Direct private code retained stale relay");
  auto plaintext=share;plaintext.replace(5,3,"tcp");Check(!pasted.PasteInvitation(plaintext),"Plaintext invitation accepted");
  Check(!connection.PasteAddress("invalid")&&connection.relay.has_value(),"Bad direct address replaced relay selection");
  connection.relay->endpoint="tcp://127.0.0.1:37004";
  Check(!connection.Error(false).empty(),"Unencrypted relay accepted by game connection");
  Check(connection.PasteAddress("127.0.0.1")&&!connection.relay,"Direct address did not clear relay selection");
  for (const auto* address:{"", "0.0.0.0", "255.255.255.255", "224.0.0.1", "192.168.1", "192.168.1.256", "127.0.0.1:37001", "127.0.0.01", "127.0.0.1.extra"}) {
    connection.address=address; Check(!connection.Error(false).empty(),"Invalid connection address accepted");
  }
  connection.address="192.168.1.20";
  for (const auto* port:{"", "0", "65536", "37001x", "-1", "999999999999"}) {
    connection.port=port;Check(!connection.Error(false).empty(),"Invalid connection port accepted");
  }
  connection.port="65535";Check(connection.Error(true).empty(),"Port upper bound rejected");
  connection.invite.back()='z';Check(!connection.Error(true).empty(),"Nonhex invitation accepted");
  connection.join=false;connection.name=std::string(33,'x');Check(!connection.Error(false).empty(),"Oversized name accepted");
  connection.name="";Check(!connection.Error(false).empty(),"Empty name accepted");
  Check(CoopNeedsMidmissionOption("shell?Name=Player?MMSCP_LVL=2?MMSCP_SUBLVL=3?LocalPlayers=1"),
        "Retail shopping coordinates were not recognized");
  Check(CoopNeedsMidmissionOption("Shell.ao2?MMSCP_LVL=2?MMSCP_SUBLVL=3"),"Qualified Shell travel rejected");
  for (auto url:{"shell", "shell?MMSCP_LVL=2", "shell?MMSCP_LVL=2?MMSCP_SUBLVL=",
                 "shell?MMSCP_LVL=2?MMSCP_SUBLVL=3?midmission",
                 "shell?MMSCP_LVL=2?MMSCP_SUBLVL=3?midmission=1",
                 "shell?MMSCP_LVL=2?MMSCP_LVL=2?MMSCP_SUBLVL=3",
                 "Checkpoint?MMSCP_LVL=2?MMSCP_SUBLVL=3"})
    Check(!CoopNeedsMidmissionOption(url),"Non-shopping or already marked travel was changed");
  for (unsigned sender=0;sender<2;++sender) {
    auto packet=InputPacket(sender);
    Check(ValidCoopInputPacket(packet,sender),"Valid native input rejected");
    Check(!ValidCoopInputPacket(packet,1-sender),"Native input impersonated partner");
    for (size_t length=0;length<packet.size();++length)
      Check(!ValidCoopInputPacket(std::span(packet).first(length),sender),"Truncated input accepted");
    packet.push_back(0); Check(!ValidCoopInputPacket(packet,sender),"Trailing input accepted"); packet.pop_back();
    packet[6]=0x8D; Check(!ValidCoopInputPacket(packet,sender),"Static input array overflow accepted"); packet[6]=0x81;
    packet[16]=0x10; Check(!ValidCoopInputPacket(packet,sender),"Axis array overflow accepted"); packet[16]=0x80;
    packet[22]=0; Check(!ValidCoopInputPacket(packet,sender),"Negative dynamic command count accepted");
    const std::vector<uint8_t> throttle{0xD5,0x7F,0xFF,0xFF,0xFE};
    Check(ValidCoopInputPacket(throttle,sender),"Native rate packet rejected");
    std::vector<uint8_t> ack{0xD5,0x7F,0xFF,0xFF,0xFF,0x80,0,0,0,0x81,uint8_t(0x80+sender)};
    Check(ValidCoopInputPacket(ack,sender),"Native acknowledgement rejected");
    ack.back()=uint8_t(0x80+1-sender); Check(!ValidCoopInputPacket(ack,sender),"Wrong acknowledgement source accepted");
  }
}
CoopLobbySettings Settings(CoopVisibility visibility) {
  return {visibility,"Checkpoint?LoadSaveGame?CheckpointToLoad=01_100?Difficulty=1?",1};
}
CoopLoadout Equipment(unsigned player) {
  // The retail profile uses -1 for no equipped armor; preserve that sentinel
  // through the wire instead of rejecting a normal campaign profile.
  CoopLoadout result; result.armor=player ? UINT32_MAX : player+1; result.mask=player+2;
  // Exercise payloads spanning several reads, including the largest admitted
  // inventory. Values differ by peer so accidental local-profile reuse fails.
  for (unsigned i=0;i<40;++i) {
    CoopWeapon weapon;
    weapon.archetype="AO2Weapons_"+std::string(75,'a')+".Weapon_"+std::to_string(i);
    weapon.class_name="AO2Game.AO2Weap_"+std::string(65,'b')+std::to_string(i);
    weapon.upgrades.fill(player+i%3); result.weapons.push_back(std::move(weapon));
  }
  return result;
}
template<class Predicate> void Until(Predicate predicate, std::initializer_list<CoopLobby*> lobbies,
    uint64_t timeout=4000, std::source_location site=std::source_location::current()) {
  const auto end=Now()+timeout;
  while (Now()<end) {
    for (auto* lobby:lobbies) lobby->Poll(Now());
    if (predicate()) return;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  throw std::runtime_error("Timed out waiting for actual lobby state at line "+std::to_string(site.line()));
}
void Admission(CoopVisibility visibility) {
  CoopLobby host,peer,third;
  Check(host.Host(Settings(visibility),"Host","127.0.0.1",0,Now(),true),"Host failed");
  Check(!host.RequestPreparation(),"Campaign prepared without a partner");
  const auto invite=host.InviteCode();
  Check((invite.size()==32)==(visibility==CoopVisibility::Private),"Invitation visibility");
  if (visibility==CoopVisibility::Private) {
    std::string wrong=invite; wrong[0]=wrong[0]=='0'?'1':'0';
    Check(peer.Join("127.0.0.1",host.Port(),visibility,wrong,"Wrong",Now()),"Wrong invitation transport start");
    Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Failed;},{&host,&peer});
    Check(host.Snapshot().members.size()==1,"Rejected peer was admitted");
    Check(!peer.Snapshot().error.empty(),"Rejection reason missing");
  }
  const auto other=visibility==CoopVisibility::Public?CoopVisibility::Private:CoopVisibility::Public;
  Check(peer.Join("127.0.0.1",host.Port(),other,other==CoopVisibility::Private?std::string(32,'0'):"","Wrong mode",Now()),"Mode rejection transport start");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Failed;},{&host,&peer});
  Check(host.Snapshot().members.size()==1,"Wrong visibility was admitted");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,invite,"Partner",Now()),"Join start failed");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  Check(peer.Snapshot().settings==host.Snapshot().settings,"Campaign metadata mismatch");
  Check(peer.Snapshot().room==host.Snapshot().room,"Room mismatch");
  Check(peer.Snapshot().members==host.Snapshot().members && host.Snapshot().members.size()==2,"Membership mismatch");
  Check(host.Snapshot().local_id!=peer.Snapshot().local_id,"Peers have duplicate identities");
  Check(third.Join("127.0.0.1",host.Port(),visibility,invite,"Third",Now()),"Third transport start");
  Until([&]{return third.Snapshot().phase==CoopLobbyPhase::Failed;},{&host,&peer,&third});
  Check(host.Snapshot().members.size()==2,"Campaign capacity exceeded");
  Check(host.SetReady(true) && peer.SetReady(true),"Ready request failed");
  Until([&]{return host.Snapshot().members[1].ready && peer.Snapshot().members[0].ready && peer.Snapshot().members[1].ready;},{&host,&peer});
  Check(host.Snapshot().members==peer.Snapshot().members,"Ready replication mismatch");
  Check(!peer.SetHostCharacter(1) && !host.SetHostCharacter(2),"Invalid character authority/value accepted");
  Check(host.SetHostCharacter(1),"Host character preference rejected");
  Until([&]{return peer.Snapshot().members[0].character==1;},{&host,&peer});
  Check(host.Snapshot().members==peer.Snapshot().members && peer.Snapshot().members[1].character==0,
        "Character preference did not assign complementary roles");
  Check(!peer.RequestPreparation(),"Client initiated host campaign start");
  Check(!peer.SubmitLoadout(1,Equipment(1)),"Equipment accepted before preparation");
  const bool unmeasured=!host.Snapshot().timing_samples;
  Check(host.RequestPreparation(),"Host preparation request failed");
  const auto delay=host.Snapshot().input_delay_frames;
  Check(delay>=3 && delay<=16,"Host input buffer outside native bounds");
  Check(!unmeasured || delay==16,"Unmeasured connection did not use the conservative buffer");
  Check(!host.RequestPreparation(),"Duplicate preparation accepted");
  Until([&]{return peer.Snapshot().preparation!=0;},{&host,&peer});
  const auto preparation=host.Snapshot().preparation;
  Check(!host.AcknowledgeImport(preparation,{1280,720}) && !peer.AcknowledgeImport(preparation,{1280,720}),"Import accepted before equipment exchange");
  Check(preparation==peer.Snapshot().preparation,"Preparation ID mismatch");
  Check(peer.Snapshot().input_delay_frames==delay,"Preparation input buffer disagrees between peers");
  Check(!host.SubmitLoadout(preparation+1,Equipment(0)),"Stale preparation accepted");
  auto invalid=Equipment(0); invalid.weapons[0].archetype="../bad";
  Check(!host.SubmitLoadout(preparation,invalid),"Invalid equipment accepted");
  Check(host.SubmitLoadout(preparation,Equipment(0)),"Host equipment submission failed");
  Check(!host.SubmitLoadout(preparation,Equipment(0)),"Duplicate host equipment accepted");
  Until([&]{return peer.Snapshot().loadouts[0].has_value();},{&host,&peer});
  Check(!host.Snapshot().prepared && !peer.Snapshot().prepared,"Barrier released with one loadout");
  Check(peer.SubmitLoadout(preparation,Equipment(1)),"Client equipment submission failed");
  Check(!peer.SubmitLoadout(preparation,Equipment(1)),"Duplicate client equipment accepted");
  Until([&]{return host.Snapshot().prepared && peer.Snapshot().prepared;},{&host,&peer});
  Check(host.Snapshot().loadouts==peer.Snapshot().loadouts &&
        *peer.Snapshot().loadouts[0]==Equipment(0) && *peer.Snapshot().loadouts[1]==Equipment(1),"Equipment replication mismatch");
  Check(!host.Snapshot().load_authorized && !peer.Snapshot().load_authorized,"Loading authorized before native import");
  Check(!host.SignalNativeBarrier(preparation),"Native barrier accepted before import");
  Check(!host.SendNativeInput(preparation,InputPacket(0)),"Input accepted before import");
  Check(!host.SendCheckpointCash(preparation,7000),"Checkpoint cash accepted before import");
  Check(!host.AcknowledgeImport(preparation+1,{1280,720}),"Stale import accepted");
  Check(!host.AcknowledgeImport(preparation,{0,720}) && !peer.AcknowledgeImport(preparation,{1920,16385}),
        "Invalid viewport acknowledged");
  auto& first=visibility==CoopVisibility::Private ? host : peer;
  auto& second=visibility==CoopVisibility::Private ? peer : host;
  const unsigned first_index=visibility==CoopVisibility::Private ? 0 : 1;
  Check(first.AcknowledgeImport(preparation,{1280,720}),"First import acknowledgement rejected");
  Check(!first.AcknowledgeImport(preparation,{1280,720}),"Duplicate import accepted");
  Until([&]{return host.Snapshot().imported[first_index] && peer.Snapshot().imported[first_index];},{&host,&peer});
  Check(!host.Snapshot().load_authorized && !peer.Snapshot().load_authorized,"One import released loading barrier");
  Check(second.AcknowledgeImport(preparation,{1920,1080}),"Second import acknowledgement rejected");
  Until([&]{return host.Snapshot().load_authorized && peer.Snapshot().load_authorized;},{&host,&peer});
  Check(host.Snapshot().viewports==peer.Snapshot().viewports &&
        host.Snapshot().viewports[first_index]==CoopViewport{1280,720} &&
        host.Snapshot().viewports[1-first_index]==CoopViewport{1920,1080},"Remote viewport dimensions mismatch");
  Check(!host.SignalNativeBarrier(preparation+1),"Stale native barrier accepted");
  Check(host.SignalNativeBarrier(preparation) && !host.SignalNativeBarrier(preparation),"Native barrier overrun");
  Until([&]{return peer.Snapshot().barrier_received==1;},{&host,&peer});
  Check(host.Snapshot().barrier_received==0 && peer.Snapshot().barrier_sent==0,"Unsent barrier fabricated");
  Check(peer.SignalNativeBarrier(preparation),"Peer native barrier rejected");
  Until([&]{return host.Snapshot().barrier_received==1;},{&host,&peer});
  Check(peer.SignalNativeBarrier(preparation),"Next peer barrier rejected");
  Until([&]{return host.Snapshot().barrier_received==2;},{&host,&peer});
  Check(host.SignalNativeBarrier(preparation),"Next host barrier rejected");
  Until([&]{return peer.Snapshot().barrier_received==2;},{&host,&peer});
  Check(!host.SendNativeInput(preparation+1,InputPacket(0)),"Stale input accepted");
  Check(!host.SendNativeInput(preparation,InputPacket(1)),"Input sender spoof accepted");
  Check(host.SendNativeInput(preparation,InputPacket(0)) && peer.SendNativeInput(preparation,InputPacket(1)),"Input transport rejected");
  Until([&]{return host.Snapshot().input_received==1 && peer.Snapshot().input_received==1;},{&host,&peer});
  Check(host.TakeNativeInput()==InputPacket(1) && peer.TakeNativeInput()==InputPacket(0),"Input payload changed in transit");
  Check(!host.TakeNativeInput() && !peer.TakeNativeInput(),"Input delivered twice");
  Check(!host.SendCheckpointCash(preparation+1,7000) && !host.SendCheckpointCash(preparation,0x80000000u),
        "Invalid checkpoint cash total or preparation accepted");
  Check(host.SendCheckpointCash(preparation,7000) && host.SendCheckpointCash(preparation,0) &&
        peer.SendCheckpointCash(preparation,4500),"Checkpoint cash send rejected");
  Until([&]{return peer.Snapshot().cash_received==2 && host.Snapshot().cash_received==1;},{&host,&peer});
  Check(peer.TakeCheckpointCash()==7000 && peer.TakeCheckpointCash()==0 &&
        host.TakeCheckpointCash()==4500 && !peer.TakeCheckpointCash() && !host.TakeCheckpointCash(),
        "Checkpoint cash changed, reordered or delivered twice");
  Check(!peer.SignalShoppingDone(preparation+1),"Shopping done accepted for another load");
  Check(host.SignalShoppingDone(preparation) && !host.SignalShoppingDone(preparation),"Shopping done duplicate send");
  Check(!host.SendCheckpointCash(preparation,4500),"Cash crossed the shopping completion boundary");
  Until([&]{return peer.Snapshot().shopping_done_received;},{&host,&peer});
  Check(peer.TakeShoppingDone() && !peer.TakeShoppingDone() && !host.TakeShoppingDone(),"Shopping done delivery/ownership");
  Check(!host.Snapshot().shopping_done_received && !peer.Snapshot().shopping_done_sent,"Partner readiness fabricated");
  Check(peer.SignalShoppingDone(preparation),"Partner shopping done rejected");
  Until([&]{return host.Snapshot().shopping_done_received;},{&host,&peer});
  Check(host.TakeShoppingDone(),"Host did not receive partner shopping done");
  auto next=Settings(visibility);
  next.map="Checkpoint?LoadSaveGame?CheckpointToLoad=05_03?Difficulty=1";
  Check(!peer.RequestTransition(next),"Client initiated campaign travel");
  auto invalid_transition=next; invalid_transition.visibility=other;
  Check(!host.RequestTransition(invalid_transition),"Travel changed session visibility");
  invalid_transition=next; invalid_transition.difficulty=2;
  Check(!host.RequestTransition(invalid_transition),"Travel changed difficulty");
  // Queue a final old-world input without polling it through before travel.
  Check(peer.SendNativeInput(preparation,InputPacket(1)),"Last old-world input rejected");
  const auto members=host.Snapshot().members;
  const auto room=host.Snapshot().room;
  Check(host.RequestTransition(next),"Host campaign transition rejected");
  const auto next_preparation=host.Snapshot().preparation;
  Check(next_preparation>preparation && !host.Snapshot().load_authorized &&
        !host.RequestTransition(next),"Transition did not close the load gate");
  Check(!host.SignalShoppingDone(next_preparation),"Shopping completion accepted while preparing travel");
  Check(!host.SendCheckpointCash(next_preparation,4500),"Cash accepted while preparing travel");
  Until([&]{return peer.Snapshot().preparation==next_preparation;},{&host,&peer});
  Check(host.Snapshot().input_delay_frames==peer.Snapshot().input_delay_frames &&
        host.Snapshot().input_delay_frames>=3 && host.Snapshot().input_delay_frames<=16,
        "Transition input buffer disagrees between peers");
  Check(host.Snapshot().members==members && peer.Snapshot().members==members &&
        host.Snapshot().room==room && peer.Snapshot().room==room && peer.Snapshot().settings==next,
        "Transition lost admitted session identity or settings");
  Check(!host.TakeNativeInput() && !peer.TakeNativeInput() && !host.Snapshot().input_received &&
        !peer.Snapshot().input_received && !host.Snapshot().barrier_sent && !peer.Snapshot().barrier_sent,
        "Old-world input or barriers leaked into transition");
  Check(!host.Snapshot().shopping_done_sent && !peer.Snapshot().shopping_done_received &&
        !host.TakeShoppingDone() && !peer.TakeShoppingDone(),"Shopping completion leaked into next load");
  Check(!host.Snapshot().cash_sent && !peer.Snapshot().cash_received &&
        !host.TakeCheckpointCash() && !peer.TakeCheckpointCash(),"Cash leaked into next load");
  Check(!host.SendNativeInput(preparation,InputPacket(0)) &&
        !peer.SubmitLoadout(preparation,Equipment(1)),"Retired preparation remained writable");
  auto changed=Equipment(1); changed.mask=19; changed.weapons[0].upgrades[0]=7;
  Check(host.SubmitLoadout(next_preparation,Equipment(0)) && peer.SubmitLoadout(next_preparation,changed),
        "Transition equipment rejected");
  Until([&]{return host.Snapshot().prepared && peer.Snapshot().prepared;},{&host,&peer});
  Check(host.Snapshot().loadouts[1]==changed && peer.Snapshot().loadouts[1]==changed,
        "New equipment was replaced by the old loadout");
  Check(!host.Snapshot().load_authorized && !peer.Snapshot().load_authorized,"Transition loaded before import");
  Check(host.AcknowledgeImport(next_preparation,{1280,720}) && peer.AcknowledgeImport(next_preparation,{1920,1080}),
        "Transition import rejected");
  Until([&]{return host.Snapshot().load_authorized && peer.Snapshot().load_authorized;},{&host,&peer});
  Check(host.SendNativeInput(next_preparation,InputPacket(0)) && peer.SendNativeInput(next_preparation,InputPacket(1)),
        "New-world input rejected");
  Until([&]{return host.Snapshot().input_received==1 && peer.Snapshot().input_received==1;},{&host,&peer});
  Check(host.TakeNativeInput()==InputPacket(1) && peer.TakeNativeInput()==InputPacket(0),"New-world input payload changed");
  Check(peer.SendCheckpointCash(next_preparation,INT32_MAX),"Next-world cash rejected");
  Until([&]{return host.Snapshot().cash_received==1;},{&host,&peer});
  Check(host.TakeCheckpointCash()==INT32_MAX,"Maximum native cash total changed");
  // A stalled native consumer cannot grow an unbounded queue.
  for (unsigned i=0;i<33;++i) Check(peer.SendCheckpointCash(next_preparation,i),"Cash overflow setup rejected");
  Until([&]{return host.Snapshot().members.size()==1;},{&host,&peer});
  Check(!host.TakeCheckpointCash() && !host.Snapshot().cash_received,"Cash queue retained after overflow disconnect");
  peer.Stop();
  Until([&]{return host.Snapshot().members.size()==1;},{&host});
  Check(!host.Snapshot().preparation && !host.Snapshot().prepared && !host.Snapshot().loadouts[0] &&
        !host.Snapshot().load_authorized && !host.Snapshot().imported[0] &&
        host.Snapshot().viewports[0]==CoopViewport{} && host.Snapshot().viewports[1]==CoopViewport{} &&
        !host.Snapshot().barrier_sent && !host.Snapshot().barrier_received,
        "Disconnected preparation retained");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,invite,"Rejoined",Now()),"Rejoin start");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  Check(host.RequestPreparation(),"Rejoined preparation request failed");
  Until([&]{return peer.Snapshot().preparation!=0;},{&host,&peer});
  Check(host.Snapshot().preparation>preparation,"Rejoined preparation reused old ID");
  Check(!peer.SubmitLoadout(preparation,Equipment(1)),"Old session preparation accepted after rejoin");
  host.Poll(Now()+31001);
  Check(host.Snapshot().phase==CoopLobbyPhase::Failed && !host.Snapshot().prepared &&
        host.Snapshot().error=="Campaign equipment preparation timed out","Incomplete equipment did not time out");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
  Check(host.Host(Settings(visibility),"Host","127.0.0.1",0,Now()),"Restart after preparation timeout failed");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,host.InviteCode(),"Rejoined",Now()),"Join after preparation timeout failed");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  host.Stop();
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
  Check(peer.Snapshot().members.empty(),"Disconnected lobby retains members");
}
void FixedProbeBuffer(CoopVisibility visibility) {
  CoopLobby host,peer;
  for (auto invalid:{1,2,17,255})
    Check(!host.Host(Settings(visibility),"Host","127.0.0.1",0,Now(),true,uint8_t(invalid)),"Invalid fixed probe buffer accepted");
  Check(!host.Host(Settings(visibility),"Host","127.0.0.1",0,Now(),false,4),"Fixed buffer enabled without diagnostic opt-in");
  for (auto frames:{3,4,16,0}) {
    Check(host.Host(Settings(visibility),"Host","127.0.0.1",0,Now(),frames!=0,uint8_t(frames)),"Fixed buffer host");
    Check(peer.Join("127.0.0.1",host.Port(),visibility,host.InviteCode(),"Partner",Now()),"Fixed buffer join");
    Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
    Check(host.RequestPreparation(),"Fixed buffer preparation");
    Until([&]{return peer.Snapshot().preparation!=0;},{&host,&peer});
    Check(host.Snapshot().input_delay_frames==frames && peer.Snapshot().input_delay_frames==frames,
          "Fixed host choice not shared, or diagnostic choice survived normal restart");
    host.Stop();
    Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
  }
}
void ConnectionTiming(CoopVisibility visibility) {
  CoopLobby host,peer;
  auto clock=Now();
  Check(host.Host(Settings(visibility),"Host","127.0.0.1",0,clock,true),"Timing host");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,host.InviteCode(),"Partner",clock),"Timing join");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  // Local clocks need no agreement: hold the peer's event processing and
  // advance only the host clock while its request is outstanding.
  clock=Now()+2000;
  host.Poll(clock);
  peer.Poll(Now());
  clock+=200;
  Until([&]{host.Poll(clock);peer.Poll(Now());return host.Snapshot().timing_samples!=0;},{});
  Check(host.Snapshot().round_trip_ms>=200,"Timing omitted delayed application processing");
  const auto first=host.Snapshot().timing_samples;
  clock+=1000;
  Until([&]{host.Poll(clock);peer.Poll(Now());return host.Snapshot().timing_samples>first;},{});
  Check(host.Snapshot().round_trip_ms==0,"Stale timing retained after recovered connection");
  const auto recovered=host.Snapshot().timing_samples;
  clock+=1000;host.Poll(clock);peer.Poll(Now());clock+=20000;
  Until([&]{host.Poll(clock);peer.Poll(Now());return host.Snapshot().timing_samples>recovered;},{});
  Check(host.Snapshot().round_trip_ms==10000,"Timing upper bound not enforced");
  Check(host.RequestPreparation(),"Measured high-delay preparation failed");
  Until([&]{host.Poll(clock);peer.Poll(Now());return peer.Snapshot().preparation!=0;},{});
  Check(host.Snapshot().input_delay_frames==16 && peer.Snapshot().input_delay_frames==16,
        "Measured buffer maximum was not agreed by both peers");
  const auto prepared_samples=host.Snapshot().timing_samples;clock+=1000;
  Until([&]{host.Poll(clock);peer.Poll(Now());return host.Snapshot().timing_samples>prepared_samples;},{});
  Check(host.Snapshot().round_trip_ms==0 && host.Snapshot().input_delay_frames==16 &&
        peer.Snapshot().input_delay_frames==16,"Connection measurement changed an agreed input buffer");
  host.Stop();
  Check(!host.Snapshot().timing_samples && !host.Snapshot().round_trip_ms,"Stopped timing retained");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
  Check(!peer.Snapshot().timing_samples && !peer.Snapshot().input_delay_frames,"Disconnected timing retained");
}
void TimingBounds() {
  // A valid admitted raw peer exercises rejection of malformed and replayed
  // timing messages without exposing a production protocol injection API.
  for (unsigned test=0;test<7;++test) {
    CoopLobby host;
    Check(host.Host(Settings(CoopVisibility::Public),"Host","127.0.0.1",0,Now()),"Timing bounds host");
    SOCKET socket=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(host.Port());address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    Check(connect(socket,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"Timing bounds connect");
    auto send_frame=[&](uint8_t kind,const std::vector<uint8_t>& payload) {
      std::vector<uint8_t> frame{'A','O','T','C',11,kind,uint8_t(payload.size()>>8),uint8_t(payload.size())};
      frame.insert(frame.end(),payload.begin(),payload.end());
      Check(send(socket,reinterpret_cast<const char*>(frame.data()),int(frame.size()),0)==int(frame.size()),"Timing frame send");
    };
    std::vector<uint8_t> join(9,0);join[8]=host.Snapshot().local_id==1 ? 2 : 1;
    join.insert(join.end(),{0,3,'R','a','w'});join.resize(join.size()+16,0);
    send_frame(1,join);
    Until([&]{return host.Snapshot().members.size()==2;},{&host});
    std::vector<uint8_t> timing(9,0);timing[8]=1;
    switch (test) {
      case 0:timing.clear();break;
      case 1:timing.pop_back();break;
      case 2:timing.push_back(0);break;
      case 3:timing[0]=2;break;
      case 4:timing[0]=1;break; // Unsolicited echo.
      case 5:timing[8]=0;break;
      case 6:send_frame(6,timing);break; // Replay the accepted request.
    }
    send_frame(6,timing);
    Until([&]{return host.Snapshot().members.size()==1;},{&host});
    Check(!host.Snapshot().timing_samples,"Invalid timing populated a measurement");
    closesocket(socket);
  }
}
void ProtocolBounds() {
  CoopLobby host;
  Check(host.Host(Settings(CoopVisibility::Public),"Host","127.0.0.1",0,Now()),"Bounds host");
  SOCKET socket=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  sockaddr_in address{}; address.sin_family=AF_INET; address.sin_port=htons(host.Port()); address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  Check(connect(socket,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"Malformed transport connect");
  const std::array<char,8> invalid{'A','O','T','C',11,1,0x7F,char(0xFF)};
  Check(send(socket,invalid.data(),int(invalid.size()),0)==int(invalid.size()),"Malformed send");
  u_long nonblocking=1; Check(ioctlsocket(socket,FIONBIO,&nonblocking)==0,"Raw nonblocking socket");
  char byte;
  Until([&]{const int result=recv(socket,&byte,1,0); return result==0 || (result==SOCKET_ERROR && WSAGetLastError()==WSAECONNRESET);},{&host});
  Check(host.Snapshot().members.size()==1,"Oversized frame changed membership");
  closesocket(socket);
  // A silent peer cannot reserve the pending-admission budget forever.
  socket=::socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  Check(connect(socket,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"Silent connect");
  Check(ioctlsocket(socket,FIONBIO,&nonblocking)==0,"Silent nonblocking socket");
  auto simulated=Now();
  // connect completion does not guarantee that the listener can accept on the
  // very next poll. Keep servicing it while advancing its clock; once accepted,
  // a silent peer must expire even though no receive event ever arrives.
  Until([&]{
    simulated+=6000; host.Poll(simulated);
    const int silent=recv(socket,&byte,1,0);
    return silent==0 || (silent==SOCKET_ERROR && WSAGetLastError()==WSAECONNRESET);
  },{});
  closesocket(socket);
}
void ImportTimeout() {
  CoopLobby host,peer;
  Check(host.Host(Settings(CoopVisibility::Public),"Host","127.0.0.1",0,Now()),"Import timeout host");
  Check(peer.Join("127.0.0.1",host.Port(),CoopVisibility::Public,"","Partner",Now()),"Import timeout join");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  Check(host.RequestPreparation(),"Import timeout preparation");
  Until([&]{return peer.Snapshot().preparation!=0;},{&host,&peer});
  Check(host.SubmitLoadout(host.Snapshot().preparation,Equipment(0)) &&
        peer.SubmitLoadout(peer.Snapshot().preparation,Equipment(1)),"Import timeout equipment");
  Until([&]{return host.Snapshot().prepared && peer.Snapshot().prepared;},{&host,&peer});
  Check(peer.AcknowledgeImport(peer.Snapshot().preparation,{1920,1080}),"Import timeout single acknowledgement");
  Until([&]{return host.Snapshot().imported[1];},{&host,&peer});
  host.Poll(Now()+31001);
  Check(host.Snapshot().phase==CoopLobbyPhase::Failed && !host.Snapshot().load_authorized &&
        !host.Snapshot().imported[1],"Incomplete native import did not expire and clear state");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
}
void IndependentProcess(const wchar_t* executable,CoopVisibility visibility) {
  CoopLobby host;
  Check(host.Host(Settings(visibility),"Process host","127.0.0.1",0,Now()),"Process host failed");
  std::wstring command=L"\""+std::wstring(executable)+L"\" --peer "+std::to_wstring(host.Port())+L" "+
      std::to_wstring(unsigned(visibility))+L" ";
  const auto invite=host.InviteCode(); command+=invite.empty()?L"-":std::wstring(invite.begin(),invite.end());
  STARTUPINFOW startup{}; startup.cb=sizeof(startup); PROCESS_INFORMATION process{};
  Check(CreateProcessW(executable,command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,nullptr,&startup,&process),"Create peer process");
  CloseHandle(process.hThread);
  try {
    Until([&]{return host.Snapshot().members.size()==2 && host.Snapshot().members[1].ready;},{&host});
    Check(host.RequestPreparation(),"Independent host preparation failed");
    Check(host.SubmitLoadout(host.Snapshot().preparation,Equipment(0)),"Independent host equipment failed");
    Until([&]{return host.Snapshot().prepared;},{&host});
    Check(*host.Snapshot().loadouts[1]==Equipment(1),"Independent client loadout mismatch");
    Check(host.AcknowledgeImport(host.Snapshot().preparation,{1280,720}),"Independent host import acknowledgement failed");
    Until([&]{return host.Snapshot().load_authorized;},{&host});
    Check(host.SendNativeInput(host.Snapshot().preparation,InputPacket(0)),"Independent host input send");
    Until([&]{return host.Snapshot().input_received==1;},{&host});
    Check(host.TakeNativeInput()==InputPacket(1),"Independent child input mismatch");
    // The child changes ready only after observing the actual host input.
    Until([&]{return !host.Snapshot().members[1].ready;},{&host});
    host.Stop();
    Check(WaitForSingleObject(process.hProcess,4000)==WAIT_OBJECT_0,"Peer process did not observe host disconnect");
    DWORD exit_code=1; Check(GetExitCodeProcess(process.hProcess,&exit_code) && exit_code==0,"Peer process failed");
  } catch (...) {
    // This handle belongs solely to the child created above.
    TerminateProcess(process.hProcess,1); WaitForSingleObject(process.hProcess,1000); CloseHandle(process.hProcess); throw;
  }
  CloseHandle(process.hProcess);
}
}
void CheckpointTransfer(CoopVisibility visibility) {
  CoopLobby host,peer;
  auto settings=Settings(visibility);
  Check(!CoopLobby::RequiresCheckpoint(settings),"Explicit retail checkpoint needs no host save");
  settings.map="Checkpoint?LoadSaveGame?FromCheckpoint=05_03?Difficulty=1";
  Check(CoopLobby::ValidSettings(settings) && CoopLobby::RequiresCheckpoint(settings),"Continue checkpoint classification");
  Check(host.Host(settings,"Host","127.0.0.1",0,Now()),"Checkpoint host");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,host.InviteCode(),"Partner",Now()),"Checkpoint peer");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  Check(host.RequestPreparation(),"Checkpoint preparation");
  Check(host.Snapshot().input_delay_frames==0,"Normal campaign enabled experimental buffering");
  const auto epoch=host.Snapshot().preparation;
  Until([&]{return peer.Snapshot().preparation==epoch;},{&host,&peer});
  Check(host.SubmitLoadout(epoch,Equipment(0)) && peer.SubmitLoadout(epoch,Equipment(1)),"Checkpoint equipment");
  Until([&]{return host.Snapshot().prepared && peer.Snapshot().prepared;},{&host,&peer});
  Check(host.AcknowledgeImport(epoch,{1280,720}) && peer.AcknowledgeImport(epoch,{1920,1080}),"Checkpoint equipment import");
  Until([&]{return peer.Snapshot().imported[0] && peer.Snapshot().imported[1];},{&host,&peer});
  Check(!host.Snapshot().load_authorized && !peer.Snapshot().load_authorized,"Loaded without host checkpoint");
  std::vector<uint8_t> bytes(CoopLobby::kMaxCheckpointBytes);
  for (size_t i=0;i<bytes.size();++i) bytes[i]=uint8_t(i*31+(i>>9));
  Check(!peer.SubmitCheckpoint(epoch,bytes) && !peer.AcknowledgeCheckpoint(epoch),"Client checkpoint authority/premature ack");
  Check(!host.SubmitCheckpoint(epoch+1,bytes) && !host.SubmitCheckpoint(epoch,{}),"Stale/empty checkpoint accepted");
  bytes.push_back(1); Check(!host.SubmitCheckpoint(epoch,bytes),"Oversized checkpoint accepted"); bytes.pop_back();
  Check(host.SubmitCheckpoint(epoch,bytes) && !host.SubmitCheckpoint(epoch,bytes),"Checkpoint duplicate submission");
  Check(host.AcknowledgeCheckpoint(epoch) && !host.AcknowledgeCheckpoint(epoch),"Host checkpoint duplicate ack");
  Until([&]{return peer.Snapshot().checkpoint_ready;},{&host,&peer},20000);
  Check(peer.Checkpoint().size()==bytes.size() && std::equal(bytes.begin(),bytes.end(),peer.Checkpoint().begin()),"Chunked checkpoint bytes changed");
  Check(!host.Snapshot().load_authorized && !peer.Snapshot().load_authorized,"Loaded before native checkpoint acknowledgement");
  Check(!peer.AcknowledgeCheckpoint(epoch+1) && peer.AcknowledgeCheckpoint(epoch),"Checkpoint acknowledgement epoch");
  Until([&]{return host.Snapshot().load_authorized && peer.Snapshot().load_authorized;},{&host,&peer});
  Check(host.Snapshot().checkpoint_imported==peer.Snapshot().checkpoint_imported,"Checkpoint import agreement");
  host.Stop(); Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
  Check(peer.Checkpoint().empty() && !peer.Snapshot().checkpoint_ready && !peer.Snapshot().load_authorized,"Checkpoint retained after disconnect");
}
void LobbyKick(CoopVisibility visibility) {
  CoopLobby host,peer;
  Check(host.Host(Settings(visibility),"Host","127.0.0.1",0,Now()),"Kick host failed");
  const auto room=host.Snapshot().room;
  const auto invite=host.InviteCode();
  const auto host_id=host.Snapshot().local_id;
  Check(peer.Join("127.0.0.1",host.Port(),visibility,invite,"Partner",Now()),"Kick peer join");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  const auto peer_id=peer.Snapshot().local_id;
  Check(!peer.Kick(host_id) && !host.Kick(host_id) && !host.Kick(0),"Invalid kick authority/identity");
  Check(host.Kick(peer_id) && !host.Kick(peer_id),"Kick/duplicate kick contract");
  Check(!host.RequestPreparation(),"Campaign started while partner removal was pending");
  host.Poll(Now());
  Check(peer.SetReady(true),"Could not queue readiness during removal");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Failed && host.Snapshot().members.size()==1;},{&host,&peer});
  Check(peer.Snapshot().error=="The host removed you from the lobby." && peer.Snapshot().members.empty(),"Kick notice or membership missing");
  Check(host.Snapshot().phase==CoopLobbyPhase::Hosting && host.Snapshot().room==room &&
        host.Snapshot().local_id==host_id && host.InviteCode()==invite,"Kick destroyed host room");
  Check(peer.Join("127.0.0.1",host.Port(),visibility,invite,"Rejoining partner",Now()),"Kick rejoin start");
  Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&host,&peer});
  Check(host.RequestPreparation(),"Campaign could not start after replacement admission");
  Check(!host.Kick(peer.Snapshot().local_id),"Lobby kick crossed into campaign preparation");
}
int wmain(int argc,wchar_t** argv) {
  try {
    if(argc==3 && std::wstring(argv[1])==L"--relay-host") {
      const auto visibility=CoopVisibility(std::stoi(argv[2]));CoopLobby host;
      Check(host.Host(Settings(visibility),"Process host","127.0.0.1",0,Now()),"Relay host listen");
      std::printf("%u %s\n",unsigned(host.Port()),host.InviteCode().empty()?"-":host.InviteCode().c_str());std::fflush(stdout);
      Until([&]{return host.Snapshot().members.size()==2&&host.Snapshot().members[1].ready;},{&host});
      Check(host.RequestPreparation(),"Relay preparation");
      Check(host.SubmitLoadout(host.Snapshot().preparation,Equipment(0)),"Relay host loadout");
      Until([&]{return host.Snapshot().prepared;},{&host});
      Check(*host.Snapshot().loadouts[1]==Equipment(1),"Relay peer loadout");
      Check(host.AcknowledgeImport(host.Snapshot().preparation,{1280,720}),"Relay host import");
      Until([&]{return host.Snapshot().load_authorized;},{&host});
      Check(host.SendNativeInput(host.Snapshot().preparation,InputPacket(0)),"Relay input send");
      Until([&]{return host.Snapshot().input_received==1;},{&host});
      Check(host.TakeNativeInput()==InputPacket(1),"Relay input altered");
      Until([&]{return !host.Snapshot().members[1].ready;},{&host});host.Stop();return 0;
    }
    if (argc==5 && std::wstring(argv[1])==L"--peer") {
      CoopLobby peer; const std::wstring wide=argv[4]; const std::string invite(wide.begin(),wide.end());
      const auto visibility=CoopVisibility(std::stoi(argv[3]));
      Check(peer.Join("127.0.0.1",uint16_t(std::stoi(argv[2])),visibility,invite=="-"?"":invite,"Process partner",Now()),"Child join failed");
      Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Joined;},{&peer});
      Check(peer.Snapshot().settings==Settings(visibility) && peer.Snapshot().members.size()==2,"Child metadata");
      Check(peer.SetReady(true),"Child ready");
      Until([&]{return peer.Snapshot().preparation!=0;},{&peer});
      Check(peer.SubmitLoadout(peer.Snapshot().preparation,Equipment(1)),"Child equipment submit");
      Until([&]{return peer.Snapshot().prepared;},{&peer});
      Check(*peer.Snapshot().loadouts[0]==Equipment(0),"Child host equipment mismatch");
      Check(peer.AcknowledgeImport(peer.Snapshot().preparation,{1920,1080}),"Child import acknowledgement failed");
      Until([&]{return peer.Snapshot().load_authorized;},{&peer});
      Check(peer.SendNativeInput(peer.Snapshot().preparation,InputPacket(1)),"Independent child input send");
      Until([&]{return peer.Snapshot().input_received==1;},{&peer});
      Check(peer.TakeNativeInput()==InputPacket(0),"Independent host input mismatch");
      Check(peer.SetReady(false),"Child preparation receipt acknowledgement");
      Until([&]{return peer.Snapshot().phase==CoopLobbyPhase::Disconnected;},{&peer});
      return 0;
    }
    for (auto visibility:{CoopVisibility::Private,CoopVisibility::Public}) {
      Check(CoopLobby::ValidSettings(Settings(visibility)),"Valid retail map rejected");
      auto invalid=Settings(visibility); invalid.map+="exec=untrusted";
      Check(!CoopLobby::ValidSettings(invalid),"Unknown map option accepted");
      invalid=Settings(visibility); invalid.difficulty=2;
      Check(!CoopLobby::ValidSettings(invalid),"Difficulty mismatch accepted");
      auto returning=Settings(visibility);
      returning.map="Checkpoint?LoadSaveGame?Difficulty=1?UseGlobalCheckpoint";
      Check(CoopLobby::ValidSettings(returning) && CoopLobby::RequiresCheckpoint(returning),
            "Native shopping return did not require the shared checkpoint");
      returning.map+="?UseGlobalCheckpoint";
      Check(!CoopLobby::ValidSettings(returning),"Duplicate global checkpoint selector accepted");
      Admission(visibility);
      ConnectionTiming(visibility);
      FixedProbeBuffer(visibility);
      LobbyKick(visibility);
      CheckpointTransfer(visibility);
      wchar_t executable[32768]; Check(GetModuleFileNameW(nullptr,executable,32768)!=0,"Executable path");
      IndependentProcess(executable,visibility);
    }
    ProtocolBounds();
    TimingBounds();
    InputBounds();
    ImportTimeout();
    std::puts("PC co-op lobby: admission, capacity, ready state, equipment preparation barrier, timeout, reconnect, protocol bounds and independent processes passed");
  } catch (const std::exception& e) { std::fprintf(stderr,"%s\n",e.what()); return 1; }
}
