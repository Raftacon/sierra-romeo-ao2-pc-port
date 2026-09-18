// PC campaign service entered through the original Private/Public menus.
// Normal play configures it in-game; explicit environment settings retain
// isolated development probes and the offline menu diagnostic.
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>
#include "network_memory.h"
#include "network_campaign.h"
#include "coop_connection_ui.h"
#include "coop_discovery.h"
#include "coop_directory_worker.h"
#include "coop_relay_native_bridge.h"
#include "coop_relay_host_retry.h"
#include "coop_network_settings.h"
#include "coop_travel.h"
#include <rex/hook.h>
#include <rex/logging.h>
#include <atomic>
#include <array>
#include <cstdlib>
#include <string>
#include <chrono>
#include <deque>
#include <fstream>
#include <charconv>
#include <algorithm>
#include <cstring>
#include <rex/ppc/context.h>

REX_EXTERN(__imp__sub_823368A8);
REX_EXTERN(__imp__sub_8236A960);
REX_EXTERN(__imp__sub_82369A00);
REX_EXTERN(__imp__sub_82680098);
REX_EXTERN(__imp__sub_82942978);
REX_EXTERN(__imp__sub_82948910);
REX_EXTERN(__imp__sub_82948C70);
REX_EXTERN(__imp__sub_8295B508);
REX_EXTERN(__imp__sub_829558D0);
REX_EXTERN(__imp__sub_82955940);
REX_EXTERN(sub_82304FB8);
REX_EXTERN(sub_822DC790);
REX_EXTERN(sub_8247D8E0);
REX_EXTERN(sub_8295D148);
REX_EXTERN(sub_829554D8);
REX_EXTERN(sub_82960998);
REX_EXTERN(sub_82960A68);
REX_EXTERN(sub_82950CA0);
REX_EXTERN(sub_829590A8);
REX_EXTERN(sub_8294D928);
REX_EXTERN(sub_82350E60);
REX_EXTERN(sub_828833B0);
REX_EXTERN(sub_82945CE0);
REX_EXTERN(__imp__sub_829643E8);
REX_EXTERN(__imp__sub_8294BE68);
REX_EXTERN(__imp__sub_82964AC8);
REX_EXTERN(__imp__sub_823E1BB8);
REX_EXTERN(__imp__sub_8295F410);
REX_EXTERN(__imp__sub_82949B40);
REX_EXTERN(__imp__sub_8294D1A0);
REX_EXTERN(__imp__sub_8293CD70);
REX_EXTERN(__imp__sub_8293EB38);
REX_EXTERN(sub_822365E8);
REX_EXTERN(sub_822366A0);
REX_EXTERN(sub_82961330);
REX_EXTERN(__imp__sub_82945AA8);
REX_EXTERN(__imp__sub_82958050);
REX_EXTERN(__imp__sub_8295D1B0);
REX_EXTERN(__imp__sub_82965178);
REX_EXTERN(__imp__sub_8293E3A8);
REX_EXTERN(__imp__sub_8293C230);
REX_EXTERN(__imp__sub_822FD878);
REX_EXTERN(__imp__sub_827D93C0);
REX_EXTERN(__imp__sub_828BE4D0);
REX_EXTERN(__imp__sub_82965098);
REX_EXTERN(sub_82968E28);
REX_EXTERN(__imp__RtlEnterCriticalSection);
REX_EXTERN(__imp__RtlLeaveCriticalSection);
REX_EXTERN(__imp__sub_826AC6F0);
REX_EXTERN(sub_828B24F0);
REX_EXTERN(sub_82305110);
REX_EXTERN(__imp__sub_8294D2E0);
REX_EXTERN(__imp__sub_829573F8);
REX_EXTERN(__imp__sub_82952090);
REX_EXTERN(sub_82959E08);
REX_EXTERN(sub_8293F548);
REX_EXTERN(sub_82968128);
REX_EXTERN(__imp__sub_82955858);
REX_EXTERN(__imp__sub_82A3B0E0);
#define AOT_COOP_GETTER(address) REX_EXTERN(__imp__sub_##address);
AOT_COOP_GETTER(82956EC0) AOT_COOP_GETTER(82956F38)
AOT_COOP_GETTER(82956270) AOT_COOP_GETTER(829562F0)
AOT_COOP_GETTER(829574A8) AOT_COOP_GETTER(82957528)
AOT_COOP_GETTER(829570A8) AOT_COOP_GETTER(82955E60)
AOT_COOP_GETTER(82956038) AOT_COOP_GETTER(82947F10)
AOT_COOP_GETTER(82956FB0) AOT_COOP_GETTER(82962628)
AOT_COOP_GETTER(82955C38)
AOT_COOP_GETTER(82947F80)
AOT_COOP_GETTER(829626B0)
#undef AOT_COOP_GETTER

namespace {
std::atomic<bool> campaign_entry_selected{false};
std::atomic<bool> pending_setup_diagnostic{false};
std::atomic<int> ui_fixture_mode{-1};
aot::CoopLobby pc_lobby;
aot::CoopRelayNativeBridge service_relay;
bool service_relay_join_pending=false;
std::string service_relay_endpoint;
aot::CoopRelayHostRetry service_relay_retry;
bool service_relay_refreshed=false;
aot::CoopDiscovery lan_advertisement;
bool lan_advertising=false;
bool service_active = false;
bool service_interactive=false, service_setup_waiting=false;
aot::CoopConnection service_connection;
int service_mode = -1;
uint64_t service_revision = 0;
size_t service_members = 0;
uint64_t service_preparation=0;
bool service_prepared=false;
bool service_load_queued=false;
bool service_checkpoint_queued=false;
bool service_checkpoint_exporting=false;
std::vector<uint8_t> service_checkpoint_export;
HWND service_window=nullptr;
uint64_t service_barrier_applied=0;
uint64_t service_input_applied=0;
std::deque<std::string> service_events;
std::string service_notice;
// These allocations belong to the PC adapter. Keep them alive until the
// retail session reset has finished routing/removing its engine players.
uint32_t service_player_list=0;
std::array<uint32_t,4> service_player_records{};
bool service_players_retired=false;
bool service_ending=false;
bool service_return_pending=false;
void ResetServiceProgress() {
  service_relay.Cancel();service_relay_join_pending=false;service_relay_endpoint.clear();
  service_relay_retry.Reset();service_relay_refreshed=false;
  service_events.clear();
  service_revision=0; service_members=0; service_preparation=0;
  service_prepared=false; service_load_queued=false; service_checkpoint_queued=false;
  service_checkpoint_exporting=false; service_checkpoint_export.clear();
  service_barrier_applied=0; service_input_applied=0;
  service_ending=false; service_return_pending=false;
}
std::string Env(const char* name) { const auto* value=std::getenv(name); return value ? value : ""; }
bool PcServiceEnabled() {
  const auto value=Env("AOT_PC_COOP_SERVICE");
  return value=="1" || (value.empty() && Env("AOT_PC_COOP_UI_DIAGNOSTIC")!="1" && Env("AOT_PC_COOP_DIAGNOSTIC")!="1");
}
uint64_t Now() { return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count()); }
uint16_t ServicePort() {
  if (service_interactive) return service_connection.Port();
  auto text=Env("AOT_PC_COOP_PORT"); if (text.empty()) return 37001;
  unsigned port=0; auto parsed=std::from_chars(text.data(),text.data()+text.size(),port);
  return parsed.ec==std::errc{} && parsed.ptr==text.data()+text.size() && port>0 && port<=65535 ? uint16_t(port) : 0;
}
std::string ReadText(uint8_t* base,uint32_t pointer) {
  std::string text;
  for (uint64_t address=pointer; address<uint64_t(pointer)+512; ++address) {
    if (address>UINT32_MAX || !aot::NetworkMemorySpan(base,uint32_t(address),1,false)) return {};
    const auto ch=base[address]; if (!ch) return text;
    if (ch<32 || ch>126) return {};
    text+=char(ch);
  }
  return {};
}
uint32_t Read(uint8_t* base, uint32_t address) {
  return *reinterpret_cast<rex::be<uint32_t>*>(base + address);
}
void Write(uint8_t* base, uint32_t address, uint32_t value) {
  *reinterpret_cast<rex::be<uint32_t>*>(base + address) = value;
}
bool GuestString(PPCContext& ctx, uint8_t* base, uint32_t output,
                 const std::string& text, bool assign) {
  if (text.size()>192 || ctx.r1.u32<0x1400 ||
      !aot::NetworkMemorySpan(base,output,12,true)) return false;
  rex::CallFrame frame(ctx);
  const auto stack=frame.ctx.r1.u32-0x200;
  if (!aot::NetworkMemorySpan(base,stack-0x1000,0x1200,true)) return false;
  Write(base,stack,frame.ctx.r1.u32);
  std::memcpy(base+stack+0x80,text.c_str(),text.size()+1);
  frame.ctx.r1.u64=stack; frame.ctx.r3.u64=output; frame.ctx.r4.u64=stack+0x80;
  if (assign) sub_822DC790(frame.ctx,base); else sub_82304FB8(frame.ctx,base);
  return true;
}
int LocalMemberIndex() {
  const auto& snapshot=pc_lobby.Snapshot();
  for (size_t i=0;i<snapshot.members.size();++i)
    if (snapshot.members[i].id==snapshot.local_id) return int(i);
  return -1;
}
bool NativeLoadProbe() {
  return service_active && (service_interactive || Env("AOT_PC_COOP_LOAD_PROBE")=="1");
}
bool TraceInputTiming() {
  static const bool enabled=Env("AOT_PC_COOP_TIMING_TRACE")=="1";
  return enabled && NativeLoadProbe();
}
std::string LocalCoopAddress() {
  ULONG size=16384;
  constexpr ULONG flags=GAA_FLAG_SKIP_ANYCAST|GAA_FLAG_SKIP_MULTICAST|GAA_FLAG_SKIP_DNS_SERVER|GAA_FLAG_INCLUDE_GATEWAYS;
  std::vector<uint8_t> bytes(size);
  auto* adapters=reinterpret_cast<IP_ADAPTER_ADDRESSES*>(bytes.data());
  auto result=GetAdaptersAddresses(AF_INET,flags,nullptr,adapters,&size);
  if (result==ERROR_BUFFER_OVERFLOW && size<=65536) {
    bytes.resize(size);adapters=reinterpret_cast<IP_ADAPTER_ADDRESSES*>(bytes.data());
    result=GetAdaptersAddresses(AF_INET,flags,nullptr,adapters,&size);
  }
  std::string address="127.0.0.1"; ULONG metric=UINT32_MAX;
  bool selected_gateway=false;
  if (result!=NO_ERROR) return address;
  for (auto* adapter=adapters;adapter;adapter=adapter->Next) {
    if (adapter->OperStatus!=IfOperStatusUp || adapter->IfType==IF_TYPE_SOFTWARE_LOOPBACK) continue;
    const bool gateway=adapter->FirstGatewayAddress!=nullptr;
    // Prefer the routed LAN adapter over low-metric VM/host-only switches.
    if (selected_gateway && !gateway) continue;
    if (gateway==selected_gateway && adapter->Ipv4Metric>=metric) continue;
    for (auto* item=adapter->FirstUnicastAddress;item;item=item->Next) {
      if (!item->Address.lpSockaddr || item->Address.lpSockaddr->sa_family!=AF_INET) continue;
      char text[INET_ADDRSTRLEN]{};
      const auto* ipv4=reinterpret_cast<const sockaddr_in*>(item->Address.lpSockaddr);
      if (InetNtopA(AF_INET,const_cast<IN_ADDR*>(&ipv4->sin_addr),text,sizeof(text))) {
        address=text;metric=adapter->Ipv4Metric;selected_gateway=gateway;break;
      }
    }
  }
  return address;
}
void TraceNativeControl(const char* route,uint32_t caller,uint8_t* base,uint32_t bytes,uint32_t length) {
  static unsigned observed=0;
  if (observed>=128 || !length || length>65536 ||
      !aot::NetworkMemorySpan(base,bytes,length,false)) return;
  ++observed;
  constexpr char hex[]="0123456789ABCDEF";
  std::string prefix;
  for (unsigned i=0;i<std::min(length,32u);++i) {
    const auto value=base[bytes+i];
    prefix+=hex[value>>4]; prefix+=hex[value&15];
  }
  REXLOG_INFO("PC co-op native control observed: route={}, caller={:08X}, type={:02X}, bytes={}, prefix={}",
      route,caller,unsigned(base[bytes]^0x80),length,prefix);
}
void EndNativeSession(bool return_to_menu, std::string notice) {
  if (service_ending) return;
  service_ending=true;
  aot::ClearCoopInvitation();
  service_return_pending=return_to_menu;
  service_notice=std::move(notice);
  service_events.clear();
  pc_lobby.Stop();
  service_relay.Cancel();service_relay_join_pending=false;
  service_revision=pc_lobby.Snapshot().revision;
  REXLOG_INFO("PC co-op session ending: return_to_menu={}",return_to_menu);
}
void CheckNativeConnection() {
  if (!service_player_list || service_ending) return;
  const auto& state=pc_lobby.Snapshot();
  if (state.members.size()!=2 || state.phase==aot::CoopLobbyPhase::Failed ||
      state.phase==aot::CoopLobbyPhase::Disconnected)
    EndNativeSession(true,state.error.empty() ? "The other player left the co-op session." : state.error);
}
bool ReleaseNativePlayers(PPCContext& context,uint8_t* base) {
  if (!service_player_list) return true;
  if (!aot::NetworkMemorySpan(base,service_player_list,0x18,true)) return false;
  for (unsigned i=0;i<4;++i) if (Read(base,service_player_list+8+i*4)!=service_player_records[i]) {
    REXLOG_ERROR("PC co-op native player ownership changed; refusing to free unrelated records");
    return false;
  }
  rex::CallFrame call(context); call.ctx.r3.u64=service_player_list;
  __imp__sub_8293C230(call.ctx,base);
  REXLOG_INFO("PC co-op native player records released: remaining={}",Read(base,service_player_list+4));
  service_player_list=0; service_player_records.fill(0);
  service_players_retired=false;
  return true;
}
void PumpNativeBarrier(PPCContext& context,uint8_t* base) {
  if (!NativeLoadProbe()) return;
  pc_lobby.Poll(Now());
  CheckNativeConnection();
  if (service_ending) {
    // 82964CA0 checks this byte immediately after the backend tick, allowing
    // the input wait to unwind before DISCONNECT performs native cleanup.
    const auto backend=Read(base,0x831228E4);
    if (aot::NetworkMemorySpan(base,backend+0x6C4,1,true)) base[backend+0x6C4]=1;
    // The separate map-load barrier (8296500C) watches mode +44 instead.
    const auto mode=Read(base,0x831228D0);
    if (aot::NetworkMemorySpan(base,mode,0x54,true)) Write(base,mode+0x44,1);
    return;
  }
  const auto& state=pc_lobby.Snapshot();
  // Observational only: do not feed unvalidated timing into the native input
  // buffer. The first such experiment caused checksum failures under load.
  static uint64_t logged_timing_sample=0;
  if (state.timing_samples && logged_timing_sample!=state.timing_samples) {
    logged_timing_sample=state.timing_samples;
    REXLOG_INFO("PC co-op connection timing: sample={}, round_trip_ms={}",state.timing_samples,state.round_trip_ms);
  }
  while (auto total=pc_lobby.TakeCheckpointCash()) {
    const auto backend=Read(base,0x831228E4);
    if (!aot::NetworkMemorySpan(base,backend,0x6B4,false) || !Read(base,backend+0x6B0)) {
      EndNativeSession(true,"The partner's checkpoint information could not be applied."); return;
    }
    rex::CallFrame call(context); call.ctx.r3.u64=backend; call.ctx.r4.u64=*total;
    sub_8293F548(call.ctx,base); // Original OnCheckpointTotalCash event, not a wallet override.
    REXLOG_INFO("PC co-op native checkpoint cash received: total={}, sequence={}",*total,state.cash_received);
  }
  if (pc_lobby.TakeShoppingDone()) {
    rex::CallFrame call(context);
    sub_82959E08(call.ctx,base); // Original receive action for 0x52.
    REXLOG_INFO("PC co-op native shopping completion received");
  }
  if (service_player_list && state.preparation && !state.load_authorized) {
    const auto backend=Read(base,0x831228E4);
    if (aot::NetworkMemorySpan(base,backend+0x6C4,1,true)) base[backend+0x6C4]=1;
    return;
  }
  const auto queue=Read(base,0x831228E0);
  if (state.load_authorized && aot::NetworkMemorySpan(base,queue,0x314,true)) {
    const auto stack=context.r1.u32-0x1000;
    if (context.r1.u32>0x5000 && aot::NetworkMemorySpan(base,stack-0x3000,0x4000,true)) {
      for (unsigned n=0;n<16;++n) {
        auto packet=pc_lobby.TakeNativeInput(); if (!packet) break;
        // The packet has already passed the bounded codec validator. The
        // retail reader owns no memory and sees the payload after type 0x55.
        const auto stream=stack+0x80, bytes=stack+0x500;
        Write(base,stack,context.r1.u32);
        std::memcpy(base+bytes,packet->data()+1,packet->size()-1);
        rex::CallFrame call(context); call.ctx.r1.u64=stack;
        call.ctx.r3.u64=stream; call.ctx.r4.u64=bytes; call.ctx.r5.u64=packet->size()-1;
        sub_822365E8(call.ctx,base);
        call.ctx.r3.u64=queue; call.ctx.r4.u64=stream; sub_82961330(call.ctx,base);
        const auto error=Read(base,stream+8+0x414), used=Read(base,stream+8+0x410);
        call.ctx.r3.u64=stream; sub_822366A0(call.ctx,base);
        if (error || used!=packet->size()-1) {
          REXLOG_ERROR("PC co-op native input decode failed: error={}, used={}, bytes={}",error,used,packet->size()-1);
          pc_lobby.Stop(); return;
        }
        ++service_input_applied;
        if (service_input_applied<=8 || service_input_applied%600==0)
          REXLOG_INFO("PC co-op native input received: sequence={}, bytes={}",service_input_applied,packet->size());
      }
    }
  }
  // Early remote arrivals stay pending until our original native barrier has
  // actually sent its own 0x47 message. Never acknowledge on receipt alone.
  if (!state.load_authorized || state.barrier_received<=service_barrier_applied ||
      state.barrier_sent<=service_barrier_applied) return;
  const auto mode=Read(base,0x831228D0);
  if (!aot::NetworkMemorySpan(base,mode,0x54,true) || Read(base,mode+0x44)) return;
  rex::CallFrame call(context);
  sub_82945CE0(call.ctx,base); // Original receive action for message 0x47.
  ++service_barrier_applied;
  REXLOG_INFO("PC co-op native barrier received: id={}, sequence={}",state.preparation,service_barrier_applied);
}
void PlayerInfo(PPCContext& ctx,uint8_t* base,int index) {
  const auto output=ctx.r5.u32;
  if (!aot::NetworkMemorySpan(base,output,52,true)) return;
  const auto& snapshot=pc_lobby.Snapshot();
  const auto* player=index>=0 && size_t(index)<snapshot.members.size() ? &snapshot.members[index] : nullptr;
  if (!GuestString(ctx,base,output,player ? player->name : "",true)) return;
  std::memset(base+output+12,0,40);
  Write(base,output+0x0C,player ? uint32_t(index) : UINT32_MAX);
  Write(base,output+0x10,player && player->id==snapshot.local_id ? 0x80000000u : 0);
  Write(base,output+0x14,UINT32_MAX); Write(base,output+0x1C,UINT32_MAX);
  Write(base,output+0x20,player && player->ready ? 0x40000000u : 0);
  Write(base,output+0x24,player ? player->character : 0);
  *reinterpret_cast<rex::be<uint64_t>*>(base+output+0x2C)=player ? player->id : 0;
}
std::string ObjectPath(uint8_t* base, uint32_t object) {
  if (!aot::NetworkMemorySpan(base, 0x83101004, 8, false)) return {};
  const auto names = Read(base, 0x83101004), count = Read(base, 0x83101008);
  if (count > 500000 || !aot::NetworkMemorySpan(base, names, uint64_t(count)*4, false)) return {};
  std::string path;
  for (unsigned depth = 0; object && depth < 12; ++depth) {
    if (!aot::NetworkMemorySpan(base, object, 0x38, false)) return {};
    const auto index = Read(base, object + 0x2C);
    if (index >= count) return {};
    const auto entry = Read(base, names + index*4);
    if (!aot::NetworkMemorySpan(base, entry, 144, false)) return {};
    const char* text = reinterpret_cast<const char*>(base + entry + 16);
    size_t size = 0; while (size < 128 && text[size]) ++size;
    if (size == 128) return {};
    path = std::string(text, size) + (path.empty() ? "" : "." + path);
    const auto parent = Read(base, object + 0x28);
    if (parent == object) return {};
    object = parent;
  }
  return path;
}
bool Trace() { const auto* path = std::getenv("AOT_NETWORK_LOG"); return path && *path; }
bool PcMenuDiagnostic() {
  const auto* value = std::getenv("AOT_PC_COOP_DIAGNOSTIC");
  return (value && std::string_view(value) == "1") || PcServiceEnabled();
}
bool PcUiDiagnostic() {
  const auto* value = std::getenv("AOT_PC_COOP_UI_DIAGNOSTIC");
  return PcMenuDiagnostic() && Trace() && value && std::string_view(value) == "1";
}
bool CampaignEntry(const std::string& path) {
  // Retail widget identifiers do not match the visible labels: the widget
  // named Public is labelled Private Co-op Game in this revision.
  return path.starts_with("Transient.MissionSetupScene.Private.") ||
         path.starts_with("Transient.MissionSetupScene.Public.");
}
bool SelectOutput(uint8_t* base, uint32_t action, uint32_t expected, uint32_t selected) {
  if (!aot::NetworkMemorySpan(base, action, 0xA0, false)) return false;
  const auto links = Read(base, action + 0x94), count = Read(base, action + 0x98);
  if (count != expected || selected >= count || !aot::NetworkMemorySpan(base, links, count*0x34, true)) return false;
  if (Read(base, links+selected*0x34+0x18) & 0x40000000) return false;
  for (uint32_t i=0; i<count; ++i) {
    auto* flags = reinterpret_cast<rex::be<uint32_t>*>(base+links+i*0x34+0x18);
    *flags = (uint32_t(*flags) & ~0x80000000u) | (i == selected ? 0x80000000u : 0);
  }
  return true;
}
void Observe(const char* operation, PPCContext& ctx, uint8_t* base) {
  if (!Trace()) return;
  static std::atomic<unsigned> count{0};
  if (count.fetch_add(1) >= 512) return;
  REXLOG_INFO("Campaign menu: {} object={:08X} path={} caller={:08X} r4={:08X} r5={:08X}",
      operation, ctx.r3.u32, ObjectPath(base, ctx.r3.u32), uint32_t(ctx.lr), ctx.r4.u32, ctx.r5.u32);
}
void ObserveBackend(const char* operation, const char* phase, uint32_t object,
                    PPCContext& ctx, uint8_t* base) {
  if (!Trace()) return;
  static std::atomic<unsigned> count{0};
  if (count.fetch_add(1) >= 512 ||
      !aot::NetworkMemorySpan(base, object, 0x6CC, false)) return;
  // Native Plasma service object, not a UObject. Offsets are observed in
  // 82942978 (initialization), 82948910 (connect), and 82948C70 (host).
  // Report the raw state without declaring authentication or connection.
  REXLOG_INFO("Campaign backend: {} {} object={:08X} initialized={} ready={} connecting={} error={:08X} result={:08X} caller={:08X}",
      operation, phase, object, unsigned(base[object+0x6B8]),
      unsigned(base[object+0x6B9]), unsigned(base[object+0x6BA]),
      Read(base, object+0x6C8), ctx.r3.u32, uint32_t(ctx.lr));
}
bool ObserveSetupRequest(const char* operation, PPCContext& ctx, uint8_t* base,
                         uint32_t difficulty) {
  static std::atomic<unsigned> count{0};
  const bool record = Trace() && count.fetch_add(1) < 512;
  if (record) {
    std::string map;
    for (uint64_t address = ctx.r4.u32; address < uint64_t(ctx.r4.u32) + 512; ++address) {
      if (address > UINT32_MAX || !aot::NetworkMemorySpan(base, uint32_t(address), 1, false)) break;
      const char ch = char(base[address]);
      if (!ch) break;
      if (ch < 32 || ch > 126) { map = "<non-printable>"; break; }
      map += ch;
    }
    REXLOG_INFO("Campaign request: {} map={} difficulty={} ui_fixture_mode={}",
        operation, map, difficulty, ui_fixture_mode.load());
  }
  if (service_active && PcServiceEnabled()) {
    if (difficulty>2 || !ServicePort()) {
      service_notice="Could not create the co-op lobby: invalid campaign settings or port.";
      REXLOG_ERROR("PC co-op host request has invalid difficulty/port"); return true;
    }
    const auto bind=service_interactive ? "0.0.0.0" : Env("AOT_PC_COOP_BIND");
    const auto name=service_interactive ? service_connection.name : Env("AOT_PC_COOP_NAME");
    const aot::CoopLobbySettings settings{service_mode==1 ? aot::CoopVisibility::Private : aot::CoopVisibility::Public,
                                        ReadText(base,ctx.r4.u32),uint8_t(difficulty)};
    // Explicit diagnostic opt-in also supports the real connection panel, so
    // relay buffering can be tested without bypassing normal host/join UI.
    const bool buffer_probe=Env("AOT_PC_COOP_BUFFER_PROBE")=="1";
    unsigned fixed_probe_frames=0;
    const auto fixed_text=buffer_probe ? Env("AOT_PC_COOP_BUFFER_FRAMES") : "";
    if (!fixed_text.empty()) {
      const auto parsed=std::from_chars(fixed_text.data(),fixed_text.data()+fixed_text.size(),fixed_probe_frames);
      if (parsed.ec!=std::errc{} || parsed.ptr!=fixed_text.data()+fixed_text.size() || fixed_probe_frames<3 || fixed_probe_frames>16) {
        REXLOG_ERROR("PC co-op diagnostic input buffer must be 3 through 16 frames"); return true;
      }
    }
    if (!pc_lobby.Host(settings,name.empty()?"PC Host":name,bind.empty()?"127.0.0.1":bind,ServicePort(),Now(),
                       buffer_probe,uint8_t(fixed_probe_frames))) {
      REXLOG_ERROR("PC co-op host failed: {}",pc_lobby.Snapshot().error); return true;
    }
    service_revision=pc_lobby.Snapshot().revision; service_members=1;
    if(service_interactive) {
      service_relay_endpoint=aot::EffectiveCoopNetworkSettings().relay;
      if(!service_relay_endpoint.empty() && !service_relay.Host(service_relay_endpoint,pc_lobby.Port()))
        service_notice="The online relay could not start. Local joining remains available.";
    }
    const auto invite_path=Env("AOT_PC_COOP_INVITE_FILE");
    if (!invite_path.empty()) { std::ofstream file(invite_path,std::ios::trunc); file << pc_lobby.InviteCode(); }
    service_events.push_back("@pc-coop-created");
    if (service_interactive) aot::SetCoopInvitation(LocalCoopAddress(),pc_lobby.Port(),
        service_mode==1 ? pc_lobby.InviteCode() : "");
    REXLOG_INFO("PC co-op lobby listening: port={}, private={}, members=1",pc_lobby.Port(),service_mode==1);
    return true;
  }
  if (!PcUiDiagnostic() || ui_fixture_mode.load() < 0) return false;
  // The UI fixture has no matchmaking objects. Entering retail StartCoop
  // without them dereferences a null object. Stop at the observed service
  // request, leaving state offline; a real backend must complete this request.
  if (record) REXLOG_INFO("Campaign UI fixture: {} request captured; service operation not executed", operation);
  return true;
}
}

REX_HOOK_RAW(sub_823368A8) {
  const uint32_t action = ctx.r3.u32;
  Observe("CheckPermission", ctx, base);
  const auto path = PcMenuDiagnostic() ? ObjectPath(base, action) : std::string{};
  __imp__sub_823368A8(ctx, base);
  // Replace only the selected campaign button's service permission result.
  // The original sequence still selects the private/public co-op configuration.
  if (!CampaignEntry(path)) return;
  if (!aot::NetworkMemorySpan(base, action, 0xA0, false) || Read(base, action) != 0x8203DF58) return;
  if (!SelectOutput(base, action, 7, 0)) return;
  campaign_entry_selected = true;
  service_mode = path.starts_with("Transient.MissionSetupScene.Public.") ? 1 : 0;
  if (PcUiDiagnostic()) ui_fixture_mode = path.starts_with("Transient.MissionSetupScene.Public.") ? 1 : 0;
  REXLOG_INFO("Campaign menu diagnostic: PC permission branch selected for {}; no Xbox Live/EA authentication performed", path);
}
REX_HOOK_RAW(sub_8236A960) { Observe("HostAO2CoopGame", ctx, base); __imp__sub_8236A960(ctx, base); }
REX_HOOK_RAW(sub_82369A00) {
  const uint32_t action = ctx.r3.u32;
  Observe("GetLoginStatus", ctx, base);
  const auto path = PcMenuDiagnostic() ? ObjectPath(base, action) : std::string{};
  __imp__sub_82369A00(ctx, base);
  if (CampaignEntry(path) && SelectOutput(base, action, 3, 2))
    REXLOG_INFO("Campaign menu diagnostic: PC network-capable branch selected for {}", path);
}
REX_HOOK_RAW(sub_82680098) { Observe("StartCoopMatch", ctx, base); __imp__sub_82680098(ctx, base); }
REX_HOOK_RAW(sub_829558D0) {
  Observe("PlasmaConnect", ctx, base);
  if (PcServiceEnabled() && campaign_entry_selected.exchange(false)) {
    if (service_player_list && (!service_players_retired || !ReleaseNativePlayers(ctx,base))) {
      service_notice="The previous co-op session is still closing. Please try again.";
      return;
    }
    ResetServiceProgress(); service_notice.clear();
    // Stop leaves a Disconnected snapshot. It is not a new disconnect event
    // for this connection; a subsequent Host/Join publishes its own revision.
    service_revision=pc_lobby.Snapshot().revision;
    service_active=true;
    service_interactive=Env("AOT_PC_COOP_SERVICE").empty();
    aot::ClearCoopInvitation();
    if (service_interactive) {
      service_setup_waiting=true;
      aot::RequestCoopConnection(service_mode==1);
      REXLOG_INFO("PC co-op connection setup requested: private={}",service_mode==1);
      return;
    }
    service_setup_waiting=false;
    const auto peer=Env("AOT_PC_COOP_JOIN");
    if (peer.empty()) service_events.push_back("@pc-coop-connected");
    else {
      std::string invite; const auto invite_path=Env("AOT_PC_COOP_INVITE_FILE");
      if (!invite_path.empty()) { std::ifstream file(invite_path); std::getline(file,invite); }
      const auto name=Env("AOT_PC_COOP_NAME");
      if (!pc_lobby.Join(peer,ServicePort(),service_mode==1 ? aot::CoopVisibility::Private : aot::CoopVisibility::Public,
                         invite,name.empty()?"PC Partner":name,Now()))
        REXLOG_ERROR("PC co-op join failed: {}",pc_lobby.Snapshot().error);
    }
    return;
  }
  if (PcUiDiagnostic() && campaign_entry_selected.exchange(false)) {
    // Inspect the native co-op setup after the console service boundary. The
    // fixture neither changes Plasma ready state nor pretends to create a peer.
    pending_setup_diagnostic = true;
    REXLOG_INFO("Campaign UI fixture: queued retail setup continuation; no service connection or campaign session exists");
    return;
  }
  __imp__sub_829558D0(ctx, base);
}
REX_HOOK_RAW(sub_82955940) {
  Observe("PlasmaDisconnect", ctx, base);
  campaign_entry_selected = false;
  pending_setup_diagnostic = false;
  ui_fixture_mode = -1;
  if (service_active) {
    aot::ClearCoopInvitation();
    if (service_player_list && !service_players_retired) { EndNativeSession(true,{}); return; }
    pc_lobby.Stop(); service_active=false; service_mode=-1; service_events.clear();
    ResetServiceProgress(); return;
  }
  __imp__sub_82955940(ctx, base);
}
REX_HOOK_RAW(sub_82956EC0) {
  if (!service_active) { __imp__sub_82956EC0(ctx,base); return; }
  PlayerInfo(ctx,base,ctx.r4.s32);
}
REX_HOOK_RAW(sub_82955858) {
  if (!service_active) {__imp__sub_82955858(ctx,base);return;}
  const auto& state=pc_lobby.Snapshot();
  const auto index=ctx.r4.u32;
  const bool accepted=index<state.members.size() && pc_lobby.Kick(state.members[index].id);
  REXLOG_INFO("PC co-op lobby kick: index={}, accepted={}",index,accepted);
  if (!accepted) service_notice="A partner can only be removed by the host before starting the campaign.";
}
REX_HOOK_RAW(sub_82A3B0E0) {
  if (!service_active) {__imp__sub_82A3B0E0(ctx,base);return;}
  const auto& state=pc_lobby.Snapshot();
  if (state.phase==aot::CoopLobbyPhase::Hosting && !state.preparation) {
    const auto address=LocalCoopAddress();
    const bool private_room=state.settings.visibility==aot::CoopVisibility::Private;
    aot::SetCoopInvitation(address,pc_lobby.Port(),private_room ? pc_lobby.InviteCode() : "");
    service_notice="Your partner should choose "+std::string(private_room?"Private":"Public")+
      " Co-op Game, then Join game.\n\nHost: "+address+":"+std::to_string(pc_lobby.Port());
    if (private_room) service_notice+="\nInvitation: "+pc_lobby.InviteCode();
    service_notice+="\n\nUse Back to copy "+std::string(private_room?"the invitation.":"the host address.");
  } else service_notice="Only the host can invite or remove players from the co-op lobby.";
  REXLOG_INFO("PC co-op invitation details requested");
  ctx.r3.u64=0;
}
REX_HOOK_RAW(sub_82956F38) {
  if (!service_active) { __imp__sub_82956F38(ctx,base); return; }
  PlayerInfo(ctx,base,ctx.r4.u32==0 ? LocalMemberIndex() : -1);
}
REX_HOOK_RAW(sub_82956270) {
  if (!service_active) { __imp__sub_82956270(ctx,base); return; }
  GuestString(ctx,base,ctx.r3.u32,pc_lobby.Snapshot().settings.map,false);
}
REX_HOOK_RAW(sub_829574A8) {
  if (!service_active) { __imp__sub_829574A8(ctx,base); return; }
  const auto& members=pc_lobby.Snapshot().members;
  GuestString(ctx,base,ctx.r3.u32,members.empty() ? "" : members.front().name,false);
}
#define AOT_SERVICE_GETTER(address, value) \
  REX_HOOK_RAW(sub_##address) { \
    if (!service_active) { __imp__sub_##address(ctx,base); return; } \
    ctx.r3.u64=(value); \
  }
AOT_SERVICE_GETTER(829562F0, pc_lobby.Snapshot().settings.difficulty)
AOT_SERVICE_GETTER(82957528, pc_lobby.Snapshot().members.size())
AOT_SERVICE_GETTER(829570A8, uint32_t(LocalMemberIndex()))
AOT_SERVICE_GETTER(82955E60, LocalMemberIndex()>=0 && ctx.r4.s32==LocalMemberIndex())
AOT_SERVICE_GETTER(82956038, pc_lobby.Snapshot().phase!=aot::CoopLobbyPhase::Failed && pc_lobby.Snapshot().phase!=aot::CoopLobbyPhase::Disconnected)
AOT_SERVICE_GETTER(82947F10, pc_lobby.Snapshot().phase==aot::CoopLobbyPhase::Hosting)
AOT_SERVICE_GETTER(82947F80, pc_lobby.Snapshot().phase==aot::CoopLobbyPhase::Joined)
// Retail 829460C0's campaign branch counts admitted non-guest players; the
// explicit ready flags belong to competitive multiplayer, not this lobby.
AOT_SERVICE_GETTER(82956FB0, pc_lobby.Snapshot().members.size()==2)
#undef AOT_SERVICE_GETTER
REX_HOOK_RAW(sub_82962628) {
  if (!service_active) { __imp__sub_82962628(ctx,base); return; }
  if (LocalMemberIndex()>=0 && ctx.r4.s32==LocalMemberIndex()) pc_lobby.SetReady(ctx.r5.u32!=0);
}
REX_HOOK_RAW(sub_829626B0) {
  if (!service_active) { __imp__sub_829626B0(ctx,base); return; }
  // OnGameStart applies the host profile's preferred character here. The
  // legacy setter dereferences its absent EA player record at 82962710.
  // Player identity remains the admitted slot; only the character changes.
  const auto index=ctx.r4.s32, character=ctx.r5.s32;
  if (index==0 && character>=0 && character<=1 && pc_lobby.SetHostCharacter(uint8_t(character)))
    REXLOG_INFO("PC co-op character preference: host={}, partner={}",character,1-character);
  else REXLOG_WARN("PC co-op character preference rejected: player={}, character={}",index,character);
}
REX_HOOK_RAW(sub_82955C38) {
  if (!service_active) { __imp__sub_82955C38(ctx,base); return; }
  if (pc_lobby.Snapshot().phase!=aot::CoopLobbyPhase::Hosting || pc_lobby.Snapshot().members.size()!=2) return;
  if (pc_lobby.RequestPreparation())
    REXLOG_INFO("PC co-op campaign preparation requested: id={}",pc_lobby.Snapshot().preparation);
  else if (pc_lobby.Snapshot().prepared)
    service_notice="Both players' campaign equipment is prepared. Shared gameplay loading is still being integrated.";
}
#define AOT_OBSERVE_BACKEND(address, operation) \
  REX_HOOK_RAW(sub_##address) { \
    const uint32_t object = ctx.r3.u32; \
    ObserveBackend(operation, "enter", object, ctx, base); \
    __imp__sub_##address(ctx, base); \
    ObserveBackend(operation, "leave", object, ctx, base); \
  }
AOT_OBSERVE_BACKEND(82942978, "Initialize")
AOT_OBSERVE_BACKEND(82948910, "Connect")
#undef AOT_OBSERVE_BACKEND
REX_HOOK_RAW(sub_82948C70) {
  if (ObserveSetupRequest("HostCoop", ctx, base, ctx.r6.u32)) return;
  const auto object = ctx.r3.u32;
  ObserveBackend("HostCoop", "enter", object, ctx, base);
  __imp__sub_82948C70(ctx, base);
  ObserveBackend("HostCoop", "leave", object, ctx, base);
}
REX_HOOK_RAW(sub_8295B508) {
  if (ObserveSetupRequest("StartCoop", ctx, base, ctx.r5.u32)) return;
  const auto object = ctx.r3.u32;
  ObserveBackend("StartCoop", "enter", object, ctx, base);
  __imp__sub_8295B508(ctx, base);
  ObserveBackend("StartCoop", "leave", object, ctx, base);
}
REX_HOOK_RAW(sub_823E1BB8) {
  static unsigned observations=0;
  static const bool trace_callers=Env("AOT_PC_COOP_RNG_TRACE")=="1";
  static thread_local bool checksum_thread=false;
  static thread_local unsigned caller_observations=0;
  const auto caller=uint32_t(ctx.lr);
  const bool checksum=caller==0x82964BE4;
  const bool selected=(checksum || (trace_callers && checksum_thread && caller_observations<8192)) &&
      TraceInputTiming() && observations<256;
  const auto sim=selected ? Read(base,0x831228C8) : 0;
  const bool trace=selected && aot::NetworkMemorySpan(base,sim,0x13C,false) &&
      aot::NetworkMemorySpan(base,ctx.r13.u32,4,false) && int32_t(Read(base,sim+0x5C))<240;
  const auto tls=trace ? Read(base,ctx.r13.u32) : 0;
  const bool readable=trace && aot::NetworkMemorySpan(base,tls,8,false);
  const auto before=readable ? Read(base,tls+4) : 0;
  const auto scope=trace ? Read(base,0x830E6E38) : 0;
  const auto produced=trace ? int32_t(Read(base,sim+0x5C)) : -1;
  const bool trace_caller=readable && trace_callers && produced>=30 && produced<=60 && caller_observations<8192;
  __imp__sub_823E1BB8(ctx,base);
  if (readable && checksum) {
    checksum_thread=true;
    ++observations;
    REXLOG_INFO("PC co-op checksum RNG probe: produced={}, consumed={}, scoped={}, tls_before={:08X}, tls_after={:08X}, value={:04X}",
        int32_t(Read(base,sim+0x5C)),int32_t(Read(base,sim+0x64)),scope,before,Read(base,tls+4),ctx.r3.u32);
  }
  if (trace_caller) {
    // Observe other draws on the checksum-producing thread around the opening
    // load boundary. Never repair the seed or suppress a mismatch. The cap and
    // frame window keep this diagnostic bounded even for a stalled load.
    REXLOG_INFO("PC co-op RNG caller probe: sequence={}, caller={:08X}, produced={}, consumed={}, scoped={}, tls_before={:08X}, tls_after={:08X}, value={:04X}",
        ++caller_observations,caller,produced,int32_t(Read(base,sim+0x64)),scope,before,Read(base,tls+4),ctx.r3.u32);
  }
}
REX_HOOK_RAW(sub_8294BE68) {
  const auto sim=ctx.r3.u32, output=ctx.r4.u32;
  __imp__sub_8294BE68(ctx,base);
  const auto delay=pc_lobby.Snapshot().input_delay_frames;
  if (!NativeLoadProbe() || delay<3 || delay>16 || !pc_lobby.Snapshot().load_authorized ||
      !aot::NetworkMemorySpan(base,sim,0x13C,false) ||
      !aot::NetworkMemorySpan(base,output,4,true) || !Read(base,sim+0x28)) return;
  const auto mode=Read(base,0x831228D0);
  if (!aot::NetworkMemorySpan(base,mode,0x54,false) || !Read(base,mode+8) || Read(base,mode+0x44)) return;
  // Both peers accepted this fixed lead before loading. Independently changing
  // the lead from local timing measurements shifts consumed inputs relative
  // to produced frames and causes native checksum mismatches. Retain the
  // original wait, input records, simulation, pause rules and checksums.
  const auto required=int32_t(Read(base,sim+0x5C))-int32_t(delay);
  Write(base,output,uint32_t(required));
  ctx.r3.u64=required>0 && int32_t(Read(base,sim+0x60))<required;
}
REX_HOOK_RAW(sub_82964AC8) {
  const auto sim=ctx.r3.u32;
  static unsigned observations=0;
  static int32_t last_produced=-2;
  const bool trace=TraceInputTiming() && observations<512 && pc_lobby.Snapshot().load_authorized &&
      aot::NetworkMemorySpan(base,sim,0x13C,false) && int32_t(Read(base,sim+0x5C))<240;
  const auto before=trace ? Read(base,sim+0x64) : 0;
  const auto rng=trace ? Read(base,0x830AB53C) : 0;
  __imp__sub_82964AC8(ctx,base);
  if (trace && last_produced!=int32_t(Read(base,sim+0x5C))) {
    last_produced=int32_t(Read(base,sim+0x5C));++observations;
    REXLOG_INFO("PC co-op frame timing probe: produced={}, before={}, consumed={}, input_ms={}, buffer_ms={}, rng_before={:08X}, rng_after={:08X}, consecutive={}",
      int32_t(Read(base,sim+0x5C)),int32_t(before),int32_t(Read(base,sim+0x64)),Read(base,sim+0xD4),Read(base,sim+0x104),rng,Read(base,0x830AB53C),Read(base,sim+0x134));
  }
}
#define AOT_TRACE_SIMULATION(address) \
  REX_HOOK_RAW(sub_##address) { \
    static std::atomic<unsigned> observed{0}; \
    const bool trace=NativeLoadProbe() && pc_lobby.Snapshot().load_authorized && observed.fetch_add(1)<8; \
    if (trace) REXLOG_INFO("PC co-op simulation trace: " #address " enter object={:08X} caller={:08X}",ctx.r3.u32,uint32_t(ctx.lr)); \
    if constexpr (0x##address==0x829643E8) PumpNativeBarrier(ctx,base); \
    __imp__sub_##address(ctx,base); \
    if (trace) REXLOG_INFO("PC co-op simulation trace: " #address " leave"); \
  }
AOT_TRACE_SIMULATION(829643E8)
AOT_TRACE_SIMULATION(8295F410)
AOT_TRACE_SIMULATION(82949B40)
AOT_TRACE_SIMULATION(8294D1A0)
#undef AOT_TRACE_SIMULATION
#define AOT_TRACE_SESSION_END(address) \
  REX_HOOK_RAW(sub_##address) { \
    const bool trace=NativeLoadProbe(); \
    if (trace) REXLOG_INFO("PC co-op session end trace: " #address " enter object={:08X} caller={:08X}",ctx.r3.u32,uint32_t(ctx.lr)); \
    __imp__sub_##address(ctx,base); \
    if (trace) { \
      const auto backend=Read(base,0x831228E4), sim=Read(base,0x831228C8), mode=Read(base,0x831228D0); \
      const auto players=Read(base,backend+0xB80); \
      REXLOG_INFO("PC co-op session end trace: " #address " leave active={}, players={}, local={}, remote={}", \
          Read(base,mode+8),Read(base,players+4),Read(base,sim+0x10),Read(base,sim+0x20)); \
    } \
  }
AOT_TRACE_SESSION_END(8293E3A8)
AOT_TRACE_SESSION_END(8293C230)
#undef AOT_TRACE_SESSION_END
REX_HOOK_RAW(sub_82965178) {
  const bool owned=service_player_list!=0 && !service_players_retired;
  __imp__sub_82965178(ctx,base);
  if (!owned) return;
  // Retail deliberately keeps player identity beyond simulation reset.
  // OnWaitingForOtherPlayerClosed later calls 829570A8, whose original local
  // record lookup is unchecked. Retire these records now and free them when
  // the next original co-op menu connection starts, after world teardown.
  service_players_retired=true;
  const auto backend=Read(base,0x831228E4);
  if (aot::NetworkMemorySpan(base,backend+0x6C4,1,true)) base[backend+0x6C4]=0;
  pc_lobby.Stop(); service_active=false; service_mode=-1;
  ResetServiceProgress();
  REXLOG_INFO("PC co-op native session reset complete; player identity retained for closing callbacks");
}
REX_HOOK_RAW(sub_8295D1B0) {
  const auto sim=ctx.r3.u32;
  __imp__sub_8295D1B0(ctx,base);
  if (!NativeLoadProbe() || !pc_lobby.Snapshot().load_authorized ||
      !aot::NetworkMemorySpan(base,sim,0x13C,false) || !Read(base,sim+0x34)) return;
  static unsigned mismatches=0;
  if (++mismatches>16 && mismatches%600!=0) return;
  REXLOG_WARN("PC co-op checksum mismatch: observed={}, produced={}, consumed={}, consecutive={}",
      mismatches,int32_t(Read(base,sim+0x5C)),int32_t(Read(base,sim+0x64)),Read(base,sim+0x134));
  for (unsigned i=0;i<7;++i) {
    const auto controller=Read(base,sim+(i<4 ? i*4 : 0x14+(i-4)*4));
    if (!controller || !aot::NetworkMemorySpan(base,controller,0x4AC,false)) continue;
    REXLOG_WARN("PC co-op checksum record: remote={}, slot={}, id={}, frame={}, checksum={:08X}",
        i>=4,i,Read(base,controller+0x258+0x250),int32_t(Read(base,controller+0x258+0x2C)),Read(base,controller+0x258));
  }
}
REX_HOOK_RAW(sub_822FD878) {
  const auto actor=ctx.r3.u32, caller=uint32_t(ctx.lr);
  __imp__sub_822FD878(ctx,base);
  if (caller!=0x826F860C || !ctx.r3.u32 || !NativeLoadProbe() || service_barrier_applied<2) return;
  const auto simulation=Read(base,0x831228C8);
  if (!aot::NetworkMemorySpan(base,simulation,0x13C,false) ||
      !aot::NetworkMemorySpan(base,actor,0xFC,false)) return;
  const auto frame=int32_t(Read(base,simulation+0x5C));
  static unsigned observed=0;
  if (frame<105 || frame>115 || observed>=256) return;
  ++observed;
  std::array<uint32_t,6> transform;
  uint32_t contribution=0;
  for (unsigned i=0;i<6;++i) contribution^=(transform[i]=Read(base,actor+0xE4+i*4));
  REXLOG_INFO("PC co-op actor checksum input: frame={}, actor={:08X}, path={}, contribution={:04X}, transform={:08X},{:08X},{:08X},{:08X},{:08X},{:08X}",
      frame,actor,ObjectPath(base,actor),contribution&0xFFFF,transform[0],transform[1],transform[2],transform[3],transform[4],transform[5]);
}
REX_HOOK_RAW(sub_827D93C0) {
  const auto actor=ctx.r3.u32, frame=ctx.r4.u32;
  __imp__sub_827D93C0(ctx,base);
  if (!NativeLoadProbe() || service_barrier_applied<2 ||
      !aot::NetworkMemorySpan(base,actor,0xFC,false) ||
      !aot::NetworkMemorySpan(base,frame,0x18,false)) return;
  const auto path=ObjectPath(base,actor);
  if (!path.starts_with("Shell.") || path.find("AO2PlayerController")==std::string::npos) return;
  static unsigned observed=0;
  if (++observed>32) return;
  REXLOG_INFO("PC co-op shop controller location: actor={:08X}, path={}, node={}, owner={}, code={:08X}, transform={:08X},{:08X},{:08X},{:08X},{:08X},{:08X}",
      actor,path,ObjectPath(base,Read(base,frame+4)),ObjectPath(base,Read(base,frame+8)),Read(base,frame+0x10),
      Read(base,actor+0xE4),Read(base,actor+0xE8),Read(base,actor+0xEC),Read(base,actor+0xF0),Read(base,actor+0xF4),Read(base,actor+0xF8));
}
REX_HOOK_RAW(sub_828BE4D0) {
  const auto caller=uint32_t(ctx.lr), output=ctx.r3.u32;
  __imp__sub_828BE4D0(ctx,base);
  if (NativeLoadProbe() && (caller==0x826AE794 || caller==0x826AE964) &&
      aot::NetworkMemorySpan(base,output,12,false))
    REXLOG_INFO("PC co-op native loader URL: caller={:08X}, text={}",caller,ReadText(base,Read(base,output)));
}
REX_HOOK_RAW(sub_826AC6F0) {
  const auto url=ctx.r4.u32;
  if (NativeLoadProbe() && service_player_list && !service_players_retired &&
      aot::NetworkMemorySpan(base,url,0x50,true) && ctx.r1.u32>0x2200) {
    rex::CallFrame call(ctx);
    const auto stack=ctx.r1.u32-0x200, output=stack+0x80;
    if (aot::NetworkMemorySpan(base,stack-0x2000,0x2200,true)) {
      Write(base,stack,ctx.r1.u32); std::memset(base+output,0,12);
      call.ctx.r1.u64=stack; call.ctx.r3.u64=output; call.ctx.r4.u64=url; call.ctx.r5.u64=0;
      __imp__sub_828BE4D0(call.ctx,base);
      const auto text=ReadText(base,Read(base,output));
      call.ctx.r3.u64=output; sub_82305110(call.ctx,base);
      if (aot::CoopNeedsMidmissionOption(text)) {
        // Preserve the retail request and checkpoint coordinates. Add the
        // missing phase option through FURL::AddOption; 826AE934 then invokes
        // the original non-gameplay simulation setup for the shop.
        constexpr char option[]="midmission";
        std::memcpy(base+stack+0xA0,option,sizeof(option));
        call.ctx.r3.u64=url; call.ctx.r4.u64=stack+0xA0;
        sub_828B24F0(call.ctx,base);
        REXLOG_INFO("PC co-op mid-mission travel option restored: {}",text);
      }
    }
  }
  __imp__sub_826AC6F0(ctx,base);
}
REX_HOOK_RAW(sub_82965098) {
  const auto simulation=ctx.r3.u32;
  if (NativeLoadProbe()) {
    REXLOG_INFO("PC co-op native loader simulation: caller={:08X}, synchronized={}",uint32_t(ctx.lr),ctx.r5.u32);
    const auto queue=Read(base,0x831228E0);
    if (pc_lobby.Snapshot().load_authorized && simulation==Read(base,0x831228C8) &&
        aot::NetworkMemorySpan(base,queue,0x314,true)) {
      const auto lock=Read(base,queue+0xA4);
      if (aot::NetworkMemorySpan(base,lock,32,true)) {
        rex::CallFrame call(ctx); call.ctx.r3.u64=lock+4;
        __imp__RtlEnterCriticalSection(call.ctx,base);
        const auto pending=Read(base,queue+4), capacity=Read(base,queue+8);
        const bool clear=pending && pending<=capacity && capacity<=4096 &&
            aot::NetworkMemorySpan(base,Read(base,queue),uint64_t(pending)*0x254,true);
        if (clear) {
          // Retail 8294F3A0 resets the history array at queue+0xC, but leaves
          // the unsent array at queue+0 intact. The PC startup can generate a
          // Shell input before this loader runs. Retaining it serializes two
          // different frame-zero records after the native simulation reset.
          // Empty only these obsolete pending records using the original
          // element destructors/allocator and lock before the loader reset.
          call.ctx.r3.u64=queue; call.ctx.r4.u64=0;
          sub_82968E28(call.ctx,base);
        }
        const auto remaining=Read(base,queue+4);
        call.ctx.r3.u64=lock+4;
        __imp__RtlLeaveCriticalSection(call.ctx,base);
        if (clear) REXLOG_INFO("PC co-op loader pending inputs cleared: count={}, remaining={}",pending,remaining);
      }
    }
  }
  __imp__sub_82965098(ctx,base);
  if (NativeLoadProbe() && aot::NetworkMemorySpan(base,simulation,0x2C,false))
    REXLOG_INFO("PC co-op native loader simulation applied: synchronized={}",Read(base,simulation+0x28));
}
REX_HOOK_RAW(sub_8294D2E0) {
  if (!NativeLoadProbe() || !service_player_list || service_players_retired) {
    __imp__sub_8294D2E0(ctx,base); return;
  }
  auto settings=pc_lobby.Snapshot().settings;
  settings.map=ReadText(base,ctx.r4.u32);
  if (LocalMemberIndex()!=0 || !pc_lobby.RequestTransition(settings)) {
    REXLOG_ERROR("PC co-op native transition rejected: local={}, map={}",LocalMemberIndex(),settings.map);
    EndNativeSession(true,"The requested co-op campaign transition could not be prepared."); return;
  }
  const auto backend=Read(base,0x831228E4);
  if (aot::NetworkMemorySpan(base,backend+0x6C4,1,true)) base[backend+0x6C4]=1;
  REXLOG_INFO("PC co-op native transition requested: id={}, map={}",pc_lobby.Snapshot().preparation,settings.map);
}
#define AOT_SHOPPING_DONE(address) \
  REX_HOOK_RAW(sub_##address) { \
    if (NativeLoadProbe() && pc_lobby.Snapshot().shopping_done_sent) return; \
    __imp__sub_##address(ctx,base); \
  }
AOT_SHOPPING_DONE(829573F8)
AOT_SHOPPING_DONE(82952090)
#undef AOT_SHOPPING_DONE
REX_HOOK_RAW(sub_82958050) {
  if (NativeLoadProbe()) {
    const auto& state=pc_lobby.Snapshot();
    const auto sim=Read(base,0x831228C8), queue=Read(base,0x831228E0);
    REXLOG_ERROR("PC co-op native failure origin: caller={:08X}, sent={}, received={}, applied={}",
        uint32_t(ctx.lr),state.input_sent,state.input_received,service_input_applied);
    if (aot::NetworkMemorySpan(base,sim,0x13C,false))
      REXLOG_ERROR("PC co-op native failure simulation: produced={}, available={}, consumed={}, status30={}, status34={}, consecutive={}, checksum={:08X}",
          int32_t(Read(base,sim+0x5C)),int32_t(Read(base,sim+0x60)),int32_t(Read(base,sim+0x64)),
          Read(base,sim+0x30),Read(base,sim+0x34),Read(base,sim+0x134),Read(base,sim+0x2C));
    if (aot::NetworkMemorySpan(base,queue,0xB4,false)) {
      for (unsigned i=0;i<3;++i) {
        const auto entry=queue+0x24+i*0x24;
        REXLOG_ERROR("PC co-op native failure queue: slot={}, id={}, buffered={}, highest={}, ack={}, await_zero={}",
            i,int32_t(Read(base,entry)),Read(base,entry+8),int32_t(Read(base,entry+0x10)),
            int32_t(Read(base,entry+0x14)),Read(base,entry+0x1C));
      }
    }
  }
  __imp__sub_82958050(ctx,base);
}
REX_HOOK_RAW(sub_8293EB38) {
  if (NativeLoadProbe() && service_player_list && pc_lobby.Snapshot().preparation &&
      !pc_lobby.Snapshot().load_authorized) { ctx.r3.s64=0; return; }
  if (!NativeLoadProbe() || !pc_lobby.Snapshot().load_authorized) { __imp__sub_8293EB38(ctx,base); return; }
  const auto stream=ctx.r5.u32;
  bool accepted=false;
  uint32_t length=0, type=0;
  int32_t frame=-3, record_frame=-3;
  if (ctx.r4.u32==uint32_t(1-LocalMemberIndex()) && aot::NetworkMemorySpan(base,stream,8,false) && Read(base,stream)==0x821A41DC) {
    const auto buffer=Read(base,stream+4);
    if (aot::NetworkMemorySpan(base,buffer,0x41C,false)) {
      const auto bytes=Read(base,buffer+4); length=Read(base,buffer+0x408);
      if (length && length<=0x4AF && !Read(base,buffer+0x414) && aot::NetworkMemorySpan(base,bytes,length,false)) {
        type=base[bytes];
        if (type==0xD5 && length>=5) {
          frame=int32_t(Read(base,bytes+1)^0x80000000u);
          if (frame>=0 && length>=8) record_frame=frame-(int(base[bytes+7])-128);
          if (!service_barrier_applied) {
            // A preparation starts in Shell. Its inputs must not cross into
            // the new campaign before the original load barrier completes.
            // The loader also clears unsent records, so a cached Shell input
            // cannot reappear inside the first post-barrier packet.
            static unsigned loading_inputs=0;
            if (++loading_inputs<=8)
              REXLOG_INFO("PC co-op loading input discarded: frame={}, first_record_frame={}",frame,record_frame);
            ctx.r3.s64=0; return;
          }
        }
        if (type!=0xD5) TraceNativeControl("peer",uint32_t(ctx.lr),base,bytes,length);
        accepted=pc_lobby.SendNativeInput(pc_lobby.Snapshot().preparation,{base+bytes,length});
      }
    }
  }
  const auto sequence=pc_lobby.Snapshot().input_sent;
  static unsigned failures=0;
  if ((!accepted && failures++<8) || (accepted && (sequence<=8 || sequence%600==0)))
    REXLOG_INFO("PC co-op native input sent: sequence={}, bytes={}, type={:02X}, accepted={}, frame={}, first_record_frame={}",sequence,length,type,accepted,frame,record_frame);
  ctx.r3.s64=accepted ? 0 : -1;
}
REX_HOOK_RAW(sub_8293CD70) {
  if (service_checkpoint_exporting) {
    const auto stream=ctx.r4.u32;
    bool accepted=false;
    if (aot::NetworkMemorySpan(base,stream,8,false) && Read(base,stream)==0x821A41DC) {
      const auto buffer=Read(base,stream+4);
      if (aot::NetworkMemorySpan(base,buffer,0x41C,false)) {
        const auto bytes=Read(base,buffer+4), length=Read(base,buffer+0x408);
        if (length>5 && length<=aot::CoopLobby::kMaxCheckpointBytes+5 && !Read(base,buffer+0x414) &&
            aot::NetworkMemorySpan(base,bytes,length,false) && base[bytes]==0xD4 &&
            (Read(base,bytes+1)^0x80000000u)==length-5 && service_checkpoint_export.empty()) {
          service_checkpoint_export.assign(base+bytes+5,base+bytes+length); accepted=true;
        }
      }
    }
    ctx.r3.s64=accepted ? 0 : -1; return;
  }
  if (NativeLoadProbe() && pc_lobby.Snapshot().load_authorized) {
    const auto stream=ctx.r4.u32;
    if (aot::NetworkMemorySpan(base,stream,8,false) && Read(base,stream)==0x821A41DC) {
      const auto buffer=Read(base,stream+4);
      if (aot::NetworkMemorySpan(base,buffer,0x41C,false)) {
        const auto bytes=Read(base,buffer+4), length=Read(base,buffer+0x408);
        // Retail 82236360 encodes signed bytes with a 0x80 bias.
        if (length==1 && aot::NetworkMemorySpan(base,bytes,1,false) && base[bytes]==(0x47^0x80)) {
          const auto accepted=pc_lobby.SignalNativeBarrier(pc_lobby.Snapshot().preparation);
          REXLOG_INFO("PC co-op native barrier sent: id={}, sequence={}, accepted={}",
              pc_lobby.Snapshot().preparation,pc_lobby.Snapshot().barrier_sent,accepted);
          ctx.r3.s64=accepted ? 0 : -1;
          return;
        }
        if (length==1 && !Read(base,buffer+0x414) && aot::NetworkMemorySpan(base,bytes,1,false) && base[bytes]==0xD2) {
          const auto accepted=pc_lobby.SignalShoppingDone(pc_lobby.Snapshot().preparation);
          REXLOG_INFO("PC co-op native shopping completion sent: accepted={}",accepted);
          if (!accepted) EndNativeSession(true,"The shop completion could not be sent to the other player.");
          ctx.r3.s64=accepted ? 0 : -1; return;
        }
        if (length==5 && !Read(base,buffer+0x414) && aot::NetworkMemorySpan(base,bytes,5,false) && base[bytes]==0xDC) {
          const auto total=Read(base,bytes+1)^0x80000000u;
          const auto accepted=pc_lobby.SendCheckpointCash(pc_lobby.Snapshot().preparation,total);
          REXLOG_INFO("PC co-op native checkpoint cash sent: total={}, accepted={}",total,accepted);
          if (!accepted) EndNativeSession(true,"Checkpoint information could not be sent to the other player.");
          ctx.r3.s64=accepted ? 0 : -1; return;
        }
        if (!Read(base,buffer+0x414)) TraceNativeControl("broadcast",uint32_t(ctx.lr),base,bytes,length);
      }
    }
  }
  __imp__sub_8293CD70(ctx,base);
}
REX_HOOK_RAW(sub_82945AA8) {
  if (!NativeLoadProbe()) { __imp__sub_82945AA8(ctx,base); return; }
  // DISCONNECT, simulation failure and map-load barrier failure share this
  // entry point. The latter sets mode +44 at 82965068 after its timed wait.
  const auto caller=uint32_t(ctx.lr);
  const bool failed=caller==0x829580A8 || caller==0x82965070;
  const bool closed_travel=caller==0x826B8CE0;
  REXLOG_INFO("PC co-op native leave: caller={:08X}, synchronization_failure={}",uint32_t(ctx.lr),failed);
  EndNativeSession(!closed_travel,failed ? "Campaign synchronization failed. The co-op session has ended." :
      closed_travel ? "" : "The co-op session has ended.");
}
namespace aot {
std::string PollCampaignService() {
  static aot::CoopDirectoryWorker directory(aot::CoopDirectoryEndpoint());
  directory.SetEndpoint(aot::CoopDirectoryEndpoint());
  const auto& lan_state=pc_lobby.Snapshot();
  const bool advertise=service_active && service_interactive &&
      lan_state.phase==CoopLobbyPhase::Hosting && lan_state.members.size()==1 && !lan_state.preparation;
  auto relay=service_relay.Snapshot();
  const bool relay_host=!service_relay_endpoint.empty();
  if(service_relay_retry.Poll(advertise&&relay_host,relay.state,Now()) &&
      service_relay.Host(service_relay_endpoint,pc_lobby.Port())) {
    service_relay_refreshed=true;relay=service_relay.Snapshot();
    REXLOG_INFO("PC co-op online relay host retry started");
  }
  const bool relay_available=relay.state==CoopRelayBridgeState::WaitingForPeer;
  SetCoopRelayInvitation(advertise&&service_mode==1&&relay_host&&relay_available?
      CoopPrivateInvitation{relay.route,pc_lobby.InviteCode()}.Encode():"",
      advertise&&service_mode==1&&relay_host?
        (relay_available?(service_relay_refreshed?"Online invitation refreshed. Copy the current invitation.":""):
        "Online invitation unavailable. Reconnecting; local joining is available."):"");
  if(!service_active || (relay_host && !advertise && relay_available))service_relay.Cancel();
  if(advertise && lan_state.settings.visibility==CoopVisibility::Public && (!relay_host||relay_available)) {
    aot::CoopDiscoveredRoom room{"",lan_state.members.front().name,lan_state.settings.map,pc_lobby.Port(),lan_state.settings.difficulty,CoopVisibility::Public};
    if(relay_host)room.relay=relay.route;
    directory.SetRoom(std::move(room));
  }
  else directory.SetRoom(std::nullopt);
  const auto directory_status=directory.Snapshot();
  aot::SetCoopDirectoryNotice(!advertise?"":relay_host&&!relay_available?
      (relay.state==CoopRelayBridgeState::Failed||relay.state==CoopRelayBridgeState::Stopped||relay.state==CoopRelayBridgeState::Idle?
        "Online relay unavailable. Retrying shortly; local joining is available.":"Connecting to online relay..."):
      !directory_status.host_error.empty()?directory_status.host_error:
      directory_status.advertised?"Public room listed. Waiting for a partner.":"Publishing public room...");
  if(advertise!=lan_advertising) {
    lan_advertising=advertise;
    if(advertise) {
      if(!lan_advertisement.Advertise(lan_state.settings,lan_state.members.front().name,pc_lobby.Port()))
        REXLOG_ERROR("PC co-op LAN discovery: {}",lan_advertisement.Error());
    } else lan_advertisement.Stop();
  }
  lan_advertisement.Poll(Now());
  if (!service_active) return {};
  if (service_setup_waiting) {
    const auto choice=TakeCoopConnectionResult();
    if (!choice) return {};
    service_setup_waiting=false;
    if (choice->cancelled) {
      REXLOG_INFO("PC co-op connection setup cancelled");
      return "@pc-coop-disconnected";
    }
    service_connection=choice->connection;
    const auto error=service_connection.Error(service_mode==1);
    if (!error.empty()) {service_notice=error;return "@pc-coop-disconnected";}
    REXLOG_INFO("PC co-op connection setup accepted: join={}, private={}, port={}",service_connection.join,service_mode==1,ServicePort());
    if (!service_connection.join) return "@pc-coop-connected";
    if(service_connection.relay) {
      if(!service_relay.Peer(*service_connection.relay)) {service_notice="The online connection could not start. Please try again.";return "@pc-coop-disconnected";}
      service_relay_join_pending=true;
      REXLOG_INFO("PC co-op online relay join started; awaiting local bridge");
    }
    else if (!pc_lobby.Join(service_connection.address,ServicePort(),service_mode==1 ? CoopVisibility::Private : CoopVisibility::Public,
        service_mode==1 ? service_connection.invite : "",service_connection.name,Now()))
      REXLOG_ERROR("PC co-op join failed: {}",pc_lobby.Snapshot().error);
  }
  if(service_relay_join_pending) {
    const auto pending=service_relay.Snapshot();
    if(pending.state==CoopRelayBridgeState::Failed || pending.state==CoopRelayBridgeState::Stopped) {
      service_relay_join_pending=false;service_notice=pending.error.empty()?"The online connection ended.":pending.error;
      return "@pc-coop-disconnected";
    }
    if(pending.state!=CoopRelayBridgeState::WaitingForGame)return {};
    service_relay_join_pending=false;
    REXLOG_INFO("PC co-op online relay paired; starting native admission on loopback port {}",pending.local_port);
    if(!pc_lobby.Join("127.0.0.1",pending.local_port,service_mode==1?CoopVisibility::Private:CoopVisibility::Public,
        service_mode==1?service_connection.invite:"",service_connection.name,Now()))service_relay.Cancel();
  }
  pc_lobby.Poll(Now());
  CheckNativeConnection();
  if (service_ending) {
    if (!service_return_pending) return {};
    service_return_pending=false;
    // Retail UGameEngine::Exec DISCONNECT (826B8B78) performs closed travel,
    // the same path used by the original Quit Game confirmation.
    return "disconnect";
  }
  const auto& snapshot=pc_lobby.Snapshot();
  if (snapshot.revision!=service_revision) {
    if (snapshot.phase==CoopLobbyPhase::Failed || snapshot.phase==CoopLobbyPhase::Disconnected) {
      service_events.push_back("@pc-coop-disconnected");
      service_notice=snapshot.error.empty() ? "The co-op session ended." : snapshot.error;
    }
    else if (snapshot.phase==CoopLobbyPhase::Joined && !service_members) {
      service_events.push_back("@pc-coop-joined");
      service_events.push_back("@pc-coop-attributes");
      service_events.push_back("@pc-coop-updated");
    }
    else if (snapshot.members.size()>service_members) {
      service_events.push_back("@pc-coop-player-joined");
      service_events.push_back("@pc-coop-updated");
    }
    else if (snapshot.members.size()<service_members) service_events.push_back("@pc-coop-player-left");
    else if (service_members) service_events.push_back("@pc-coop-updated");
    service_members=snapshot.members.size(); service_revision=snapshot.revision;
    REXLOG_INFO("PC co-op lobby state: phase={}, members={}, revision={}",int(snapshot.phase),service_members,service_revision);
  }
  if (snapshot.preparation!=service_preparation) {
    if (snapshot.preparation) ClearCoopInvitation();
    service_preparation=snapshot.preparation; service_prepared=false; service_load_queued=false; service_checkpoint_queued=false;
    REXLOG_INFO("PC co-op input buffer agreed: preparation={}, frames={}, round_trip_ms={}, samples={}",
        snapshot.preparation,snapshot.input_delay_frames,snapshot.round_trip_ms,snapshot.timing_samples);
    if (service_preparation) service_events.push_back("@pc-coop-prepare");
  }
  if (snapshot.prepared && !service_prepared) {
    service_prepared=true; service_events.push_back("@pc-coop-prepared");
  }
  if (snapshot.checkpoint_ready && LocalMemberIndex()==1 && !service_checkpoint_queued) {
    service_checkpoint_queued=true; service_events.push_back("@pc-coop-checkpoint");
  }
  if (snapshot.load_authorized && !service_load_queued) {
    service_load_queued=true;
    REXLOG_INFO("PC co-op native imports acknowledged by both players: id={}",snapshot.preparation);
    if (NativeLoadProbe()) service_events.push_back("@pc-coop-load");
  }
  if (service_events.empty()) return {};
  auto event=std::move(service_events.front()); service_events.pop_front(); return event;
}
const CoopLobbySnapshot* CampaignServiceSnapshot() { return service_active ? &pc_lobby.Snapshot() : nullptr; }
bool ResetCampaignLoadout(PPCContext& context,uint32_t plasma,uint8_t* base) {
  const auto& state=pc_lobby.Snapshot();
  if (!service_active || !state.preparation || state.prepared || state.load_authorized ||
      !NetworkMemorySpan(base,plasma,0x1AC,true) || Read(base,plasma)!=0x820E90C8) return false;
  // Both retail transition paths (8294D2E0 / 8294D500) empty the Plasma
  // equipment arrays before OnGameStart appends the current profile loadout.
  for (const auto [offset,stride,limit]:{std::array<uint32_t,3>{0x194,56,160},
                                      std::array<uint32_t,3>{0x1A0,12,4}}) {
    const auto count=Read(base,plasma+offset+4), capacity=Read(base,plasma+offset+8);
    if (count>limit || capacity<count || capacity>1024 ||
        (capacity && !NetworkMemorySpan(base,Read(base,plasma+offset),uint64_t(capacity)*stride,true))) {
      EndNativeSession(true,"The previous campaign equipment could not be released."); return false;
    }
  }
  rex::CallFrame call(context);
  call.ctx.r3.u64=plasma+0x194; call.ctx.r4.u64=0;
  sub_82968128(call.ctx,base); // Destroys weapon strings and clears their array.
  // Armor entries contain only three integers; retain their allocation for
  // the original AddRemoteArmor / local profile append routines to reuse.
  Write(base,plasma+0x1A4,0);
  REXLOG_INFO("PC co-op native equipment cleared: id={}",state.preparation);
  return Read(base,plasma+0x198)==0;
}
static bool ReadCampaignLoadout(uint32_t plasma,uint8_t* base,int index,CoopLoadout& equipment) {
  if (!service_active || index<0 || !aot::NetworkMemorySpan(base,plasma,0x1AC,false) || Read(base,plasma)!=0x820E90C8) return false;
  const auto weapons=Read(base,plasma+0x194), count=Read(base,plasma+0x198);
  const auto armor=Read(base,plasma+0x1A0), armor_count=Read(base,plasma+0x1A4);
  if (count>160 || armor_count>4 || !aot::NetworkMemorySpan(base,weapons,uint64_t(count)*56,false) ||
      !aot::NetworkMemorySpan(base,armor,uint64_t(armor_count)*12,false)) return false;
  const auto string=[&](uint32_t address) {
    const auto length=Read(base,address+4), capacity=Read(base,address+8), pointer=Read(base,address);
    if (!length || length>129 || capacity<length || !aot::NetworkMemorySpan(base,pointer,length,false) || base[pointer+length-1]) return std::string{};
    return std::string(reinterpret_cast<char*>(base+pointer),length-1);
  };
  for (uint32_t i=0;i<count;++i) {
    const auto entry=weapons+i*56;
    if (Read(base,entry)!=uint32_t(index)) continue;
    CoopWeapon weapon; weapon.archetype=string(entry+4); weapon.class_name=string(entry+0x10);
    for (unsigned n=0;n<7;++n) weapon.upgrades[n]=Read(base,entry+0x1C+n*4);
    equipment.weapons.push_back(std::move(weapon));
  }
  unsigned matches=0;
  for (uint32_t i=0;i<armor_count;++i) if (Read(base,armor+i*12)==uint32_t(index)) {
    ++matches; equipment.armor=Read(base,armor+i*12+4); equipment.mask=Read(base,armor+i*12+8);
  }
  const auto valid=matches==1 && CoopLobby::ValidLoadout(equipment);
  REXLOG_INFO("PC co-op native equipment: player={}, weapons={}, armor_records={}, armor={}, mask={}, valid={}",
      index,equipment.weapons.size(),matches,equipment.armor,equipment.mask,valid);
  return valid;
}
bool SubmitCampaignLoadout(uint32_t plasma,uint8_t* base) {
  CoopLoadout equipment;
  if (!ReadCampaignLoadout(plasma,base,LocalMemberIndex(),equipment) ||
      !pc_lobby.SubmitLoadout(pc_lobby.Snapshot().preparation,equipment)) {
    service_notice="Could not prepare the local campaign equipment."; return false;
  }
  return true;
}
bool AcknowledgeCampaignImport(uint32_t plasma,uint8_t* base) {
  const auto& snapshot=pc_lobby.Snapshot();
  if (!service_active || !snapshot.prepared) return false;
  for (unsigned index=0;index<2;++index) {
    CoopLoadout native;
    if (!snapshot.loadouts[index] || !ReadCampaignLoadout(plasma,base,int(index),native) || native!=*snapshot.loadouts[index]) {
      service_notice="The imported campaign equipment did not match the connected players.";
      REXLOG_ERROR("PC co-op native import verification failed: player={}",index); return false;
    }
  }
  RECT bounds{};
  DWORD owner=0;
  GetWindowThreadProcessId(service_window,&owner);
  if (owner!=GetCurrentProcessId() || !GetClientRect(service_window,&bounds)) return false;
  const CoopViewport viewport{uint32_t(bounds.right-bounds.left),uint32_t(bounds.bottom-bounds.top)};
  const auto accepted=pc_lobby.AcknowledgeImport(snapshot.preparation,viewport);
  REXLOG_INFO("PC co-op native import verified: id={}, accepted={}",snapshot.preparation,accepted);
  return accepted;
}
bool PrepareCampaignCheckpoint(PPCContext& context,uint8_t* base) {
  const auto& snapshot=pc_lobby.Snapshot();
  if (!NativeLoadProbe() || !snapshot.prepared || snapshot.members.size()!=2) return false;
  if (!CoopLobby::RequiresCheckpoint(snapshot.settings) || LocalMemberIndex()!=0) return true;
  const auto backend=Read(base,0x831228E4);
  if (!NetworkMemorySpan(base,backend,0xD58,false)) return false;
  service_checkpoint_export.clear(); service_checkpoint_exporting=true;
  rex::CallFrame call(context); call.ctx.r3.u64=backend; sub_8294D928(call.ctx,base);
  service_checkpoint_exporting=false;
  const auto size=service_checkpoint_export.size();
  const auto accepted=pc_lobby.SubmitCheckpoint(snapshot.preparation,service_checkpoint_export) &&
                      pc_lobby.AcknowledgeCheckpoint(snapshot.preparation);
  service_checkpoint_export.clear();
  REXLOG_INFO("PC co-op native checkpoint exported: bytes={}, accepted={}",size,accepted);
  if (!accepted) {
    service_notice="The host's saved checkpoint could not be prepared. The campaign was not started.";
    pc_lobby.Stop();
  }
  return accepted;
}
bool ImportCampaignCheckpoint(PPCContext& context,uint8_t* base) {
  const auto& snapshot=pc_lobby.Snapshot();
  if (!NativeLoadProbe() || LocalMemberIndex()!=1 || !snapshot.prepared ||
      !snapshot.checkpoint_ready || snapshot.members.size()!=2 || snapshot.load_authorized) return false;
  const auto checkpoint=pc_lobby.Checkpoint();
  if (checkpoint.empty() || checkpoint.size()>CoopLobby::kMaxCheckpointBytes || context.r1.u32<0x4000) return false;
  const auto stack=context.r1.u32-0x200;
  if (!NetworkMemorySpan(base,stack-0x3000,0x3200,true)) return false;
  rex::CallFrame call(context); call.ctx.r1.u64=stack; Write(base,stack,context.r1.u32);
  call.ctx.r3.u64=checkpoint.size(); sub_8247D8E0(call.ctx,base);
  const auto bytes=call.ctx.r3.u32, array=stack+0x80;
  if (!NetworkMemorySpan(base,bytes,checkpoint.size(),true)) return false;
  std::memcpy(base+bytes,checkpoint.data(),checkpoint.size());
  Write(base,array,bytes); Write(base,array+4,uint32_t(checkpoint.size())); Write(base,array+8,uint32_t(checkpoint.size()));
  call.ctx.r3.u64=array; sub_82350E60(call.ctx,base);
  const auto imported=Read(base,0x830AB2E4), size=Read(base,0x830AB2E8);
  const auto exact=size==checkpoint.size() && NetworkMemorySpan(base,imported,size,false) &&
                   !std::memcmp(base+imported,checkpoint.data(),size);
  call.ctx.r3.u64=array; sub_828833B0(call.ctx,base);
  const auto accepted=exact && pc_lobby.AcknowledgeCheckpoint(snapshot.preparation);
  REXLOG_INFO("PC co-op native checkpoint imported: bytes={}, exact={}, accepted={}",size,exact,accepted);
  if (!accepted) { service_notice="The host's checkpoint could not be imported. The campaign was not started."; pc_lobby.Stop(); }
  return accepted;
}
bool BeginCampaignLoad(PPCContext& context,uint8_t* base) {
  const auto& snapshot=pc_lobby.Snapshot();
  const auto local=LocalMemberIndex();
  if (!NativeLoadProbe() || !snapshot.load_authorized ||
      snapshot.members.size()!=2 || local<0) return false;
  service_barrier_applied=0;
  service_input_applied=0;
  const auto backend=Read(base,0x831228E4), mode=Read(base,0x831228D0), simulation=Read(base,0x831228C8);
  if (!NetworkMemorySpan(base,backend,0xD58,true) || !NetworkMemorySpan(base,mode,0x54,true) ||
      !NetworkMemorySpan(base,simulation,0x13C,true)) return false;
  const auto players=Read(base,backend+0xB80);
  if (!NetworkMemorySpan(base,players,0x18,true)) return false;
  const auto descriptor=context.r1.u32-0x100;
  const auto queue=Read(base,0x831228E0);
  if (context.r1.u32<0x1100 || !NetworkMemorySpan(base,descriptor-0x1000,0x1100,true) ||
      !NetworkMemorySpan(base,queue,0x314,true) ||
      !CoopLobby::ValidViewport(snapshot.viewports[0]) ||
      !CoopLobby::ValidViewport(snapshot.viewports[1])) return false;
  // This isolated probe enters the native network campaign state. Player
  // records describe actually admitted PC peers using the layout consumed by
  // 82956C58 and 8293C440/C4C0; no EA account/session object is fabricated.
  const bool reuse=service_player_list==players && !service_players_retired;
  for (unsigned index=0;index<4;++index) {
    const auto record=Read(base,players+8+index*4);
    if (record!=(reuse ? service_player_records[index] : 0)) {
      REXLOG_ERROR("PC co-op load found unrelated native player records"); return false;
    }
    if (reuse && index<2 && (!NetworkMemorySpan(base,record,0x58,true) ||
        Read(base,record+4)!=index ||
        uint64_t(*reinterpret_cast<rex::be<uint64_t>*>(base+record+8))!=snapshot.members[index].id)) {
      REXLOG_ERROR("PC co-op transition player identity changed"); return false;
    }
  }
  if (!reuse) { service_player_list=players; service_player_records.fill(0); }
  service_players_retired=false;
  rex::CallFrame call(context);
  for (unsigned index=0;index<2;++index) {
    if (reuse) {
      Write(base,service_player_records[index]+0x30,snapshot.members[index].character);
      continue;
    }
    call.ctx.r3.u64=0x58; sub_8247D8E0(call.ctx,base);
    const auto record=call.ctx.r3.u32;
    if (!NetworkMemorySpan(base,record,0x58,true)) {
      ReleaseNativePlayers(context,base);
      EndNativeSession(true,"There was not enough memory to start the co-op campaign.");
      return false;
    }
    std::memset(base+record,0,0x58);
    Write(base,record,unsigned(local)==index ? 0 : UINT32_MAX);
    Write(base,record+4,index);
    *reinterpret_cast<rex::be<uint64_t>*>(base+record+8)=snapshot.members[index].id;
    Write(base,record+0x10,unsigned(local)==index ? 1 : 0);
    Write(base,record+0x2C,1); Write(base,record+0x30,snapshot.members[index].character);
    std::memcpy(base+record+0x38,snapshot.members[index].name.data(),std::min(size_t(16),snapshot.members[index].name.size()));
    *reinterpret_cast<rex::be<uint64_t>*>(base+record+0x4C)=snapshot.members[index].id;
    Write(base,players+8+index*4,record);
    service_player_records[index]=record;
  }
  // The original viewport-descriptor receiver stores dimensions and the
  // simulation IDs at backend +0xBB8 + index*16. LoadMap's original
  // RegisterRemotePlayers path (8294D1A0) consumes these to route views.
  // Dimensions come from each connected peer's actual game client area.
  Write(base,descriptor-0x100,context.r1.u32);
  for (unsigned index=0;index<2;++index) {
    Write(base,descriptor,0x820E7DC0);
    Write(base,descriptor+4,index); Write(base,descriptor+8,UINT32_MAX);
    Write(base,descriptor+12,snapshot.viewports[index].width);
    Write(base,descriptor+16,snapshot.viewports[index].height);
    call.ctx.r1.u64=descriptor-0x100;
    call.ctx.r3.u64=descriptor; sub_829590A8(call.ctx,base);
    REXLOG_INFO("PC co-op native viewport descriptor: player={}, size={}x{}, guest=none",index,
        snapshot.viewports[index].width,snapshot.viewports[index].height);
  }
  call.ctx.r1.u64=context.r1.u64;
  Write(base,players+4,2);
  base[backend+0x6C4]=0;
  Write(base,backend+0x6B4,2); Write(base,backend+0x6C0,2);
  std::memcpy(base+backend+0x6F0,snapshot.settings.map.c_str(),snapshot.settings.map.size()+1);
  Write(base,mode+0x14,local==0); Write(base,mode+0x18,local==1);
  // Live reflection identifies 82674AB0 as IsPrivateMatch; it dispatches
  // through Plasma slot +0x1F8 to 829483F0, which reads mode +0x38.
  // Campaign mode is +0x2C. Preserve the actual menu's visibility choice.
  Write(base,mode+0x2C,1); Write(base,mode+0x30,0);
  Write(base,mode+0x38,snapshot.settings.visibility==CoopVisibility::Private);
  // The post-start values below are the writes at 82961934..82961990.
  for (const auto offset:{0x0C,0x44,0x48,0x1C,0x20}) Write(base,mode+offset,0);
  for (const auto offset:{0x08,0x10,0x24,0x4C}) Write(base,mode+offset,1);
  Write(base,0x83114654,1);
  REXLOG_INFO("PC co-op native load probe: id={}, local={}, map={}, reused_players={}",snapshot.preparation,local,snapshot.settings.map,reuse);
  call.ctx.r3.u64=simulation; sub_8295D148(call.ctx,base);
  call.ctx.r3.u64=simulation; call.ctx.r4.u64=0; sub_82960998(call.ctx,base);
  call.ctx.r3.u64=simulation; call.ctx.r4.u64=1-local; call.ctx.r5.u64=0; sub_82960A68(call.ctx,base);
  // A new remote queue accepts frame zero first (82961330). Shell simulation
  // has already advanced thousands of frames independently on each machine.
  // Use the original match reset, including controller state and RNG seed,
  // before exposing the new network controllers to the next engine tick.
  call.ctx.r3.u64=simulation; sub_829554D8(call.ctx,base);
  REXLOG_INFO("PC co-op native simulation reset: produced={}, available={}, consumed={}",
      int32_t(Read(base,simulation+0x5C)),int32_t(Read(base,simulation+0x60)),int32_t(Read(base,simulation+0x64)));
  // TCP carries the original uncompressed 0x55 codec. Compression is only
  // a transport choice; retain every serialized simulation input field.
  Write(base,queue+0xB0,0);
  REXLOG_INFO("PC co-op native simulation controllers: local_count={}, remote_count={}",Read(base,simulation+0x10),Read(base,simulation+0x20));
  call.ctx.r3.u64=backend; sub_82950CA0(call.ctx,base);
  REXLOG_INFO("PC co-op native campaign travel requested: id={}; gameplay synchronization unverified",snapshot.preparation);
  return true;
}
std::string ConsumeCampaignNotice() {
  if (service_ending && service_player_list) return {};
  auto result=std::move(service_notice); service_notice.clear(); return result;
}
std::string CampaignConnectionStatus() {
  if (!service_active || !service_interactive) return {};
  if (service_setup_waiting) return " "; // The controller panel supplies the body.
  if (pc_lobby.Snapshot().phase==CoopLobbyPhase::Connecting) return "Connecting to your partner...";
  return {};
}
bool ConsumeCampaignSetupDiagnostic() {
  return pending_setup_diagnostic.exchange(false);
}
void InitializeCampaignNetwork(void* native_window) {
  service_window=static_cast<HWND>(native_window);
  if (Trace()) REXLOG_INFO("Campaign menu tracing enabled; PC permission diagnostic={}", PcMenuDiagnostic());
}
}
#endif
