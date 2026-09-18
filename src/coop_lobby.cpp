#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <bcrypt.h>
#include "coop_lobby.h"
#include <algorithm>
#include <charconv>
#include <cstring>
#include <span>
#include <deque>
#include "coop_input_packet.h"

namespace aot {
namespace {
constexpr size_t kMaxPayload = 16384, kMaxBuffered = 65536;
constexpr uint64_t kHandshakeTimeout = 5000, kPeerTimeout = 30000;
enum class Message : uint8_t { Join = 1, Welcome, Reject, Ready, Members, Ping, Prepare, Loadout, Prepared, Imported, AuthorizeLoad, NativeBarrier, NativeInput, CheckpointChunk, CheckpointImported, ShoppingDone, CheckpointCash };
struct Winsock {
  WSADATA data{};
  bool ready = WSAStartup(MAKEWORD(2, 2), &data) == 0;
  ~Winsock() { if (ready) WSACleanup(); }
};
bool NetworkReady() { static Winsock winsock; return winsock.ready; }
bool Random(std::span<uint8_t> bytes) {
  return BCryptGenRandom(nullptr, bytes.data(), ULONG(bytes.size()), BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0;
}
void Close(SOCKET& socket) {
  if (socket != INVALID_SOCKET) { closesocket(socket); socket = INVALID_SOCKET; }
}
bool NameValid(const std::string& text) {
  return !text.empty() && text.size() <= 32 && std::all_of(text.begin(), text.end(),
      [](unsigned char c) { return c >= 32 && c < 127; });
}
bool Nonblocking(SOCKET socket) { u_long mode = 1; return ioctlsocket(socket, FIONBIO, &mode) == 0; }
bool PreparePeerSocket(SOCKET socket) {
  const DWORD enabled=1;
  // Native simulation inputs are small and time-sensitive. Do not wait for
  // another packet or acknowledgement merely to coalesce them into a segment.
  return Nonblocking(socket) && setsockopt(socket,IPPROTO_TCP,TCP_NODELAY,
      reinterpret_cast<const char*>(&enabled),sizeof(enabled))==0;
}
bool Endpoint(const std::string& ip, uint16_t port, sockaddr_in& result) {
  result.sin_family = AF_INET; result.sin_port = htons(port);
  return inet_pton(AF_INET, ip.c_str(), &result.sin_addr) == 1;
}
using Bytes = std::vector<uint8_t>;
void Number(Bytes& bytes, uint64_t value, unsigned size) {
  for (unsigned i = size; i; --i) bytes.push_back(uint8_t(value >> ((i-1)*8)));
}
void String(Bytes& bytes, const std::string& value) {
  Number(bytes, value.size(), 2); bytes.insert(bytes.end(), value.begin(), value.end());
}
struct Reader {
  std::span<const uint8_t> bytes;
  size_t offset = 0;
  bool ok = true;
  uint64_t Number(unsigned size) {
    if (!ok || size > bytes.size()-offset) { ok = false; return 0; }
    uint64_t value = 0;
    while (size--) value = (value << 8) | bytes[offset++];
    return value;
  }
  std::string String(size_t maximum) {
    const auto length = Number(2);
    if (!ok || length > maximum || length > bytes.size()-offset) { ok = false; return {}; }
    std::string result(reinterpret_cast<const char*>(bytes.data()+offset), size_t(length));
    offset += size_t(length); return result;
  }
  bool End() const { return ok && offset == bytes.size(); }
};
Bytes Frame(Message type, const Bytes& payload) {
  Bytes result{'A','O','T','C',11,uint8_t(type)};
  Number(result, payload.size(), 2);
  result.insert(result.end(), payload.begin(), payload.end());
  return result;
}
bool DecodeInvite(const std::string& text, std::array<uint8_t,16>& bytes) {
  if (text.size() != 32) return false;
  for (size_t i=0; i<bytes.size(); ++i) {
    unsigned value = 0;
    auto parsed = std::from_chars(text.data()+i*2, text.data()+i*2+2, value, 16);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data()+i*2+2) return false;
    bytes[i] = uint8_t(value);
  }
  return true;
}
}

struct CoopLobby::Impl {
  struct Peer {
    SOCKET socket = INVALID_SOCKET;
    Bytes incoming, outgoing;
    size_t sent = 0;
    uint64_t id = 0, created = 0, last_receive = 0;
    bool admitted = false, close_after_send = false;
    uint64_t timing_sent=0, timing_received=0, timing_started=0;
    bool timing_pending=false;
  };
  SOCKET listener = INVALID_SOCKET;
  std::vector<Peer> peers;
  CoopLobbySnapshot view;
  std::array<uint8_t,16> invite{};
  std::string local_name;
  uint16_t port = 0;
  uint64_t now = 0, last_ping = 0;
  bool host = false, connected = false;
  bool experimental_input_buffer=false;
  uint8_t fixed_probe_frames=0;
  bool submitted=false;
  bool import_submitted=false;
  uint64_t next_preparation=0;
  uint64_t preparation_started=0;
  std::deque<Bytes> native_inputs;
  size_t native_input_bytes=0;
  Bytes checkpoint;
  size_t checkpoint_sent=0;
  bool checkpoint_ack_submitted=false;
  uint64_t retired_preparation=0, retired_input_received=0;
  bool shopping_done_pending=false;
  std::deque<uint32_t> checkpoint_cash;

  ~Impl() { CloseAll(); }
  uint8_t ChooseInputDelay() const {
    if (!experimental_input_buffer) return 0;
    if (fixed_probe_frames) return fixed_probe_frames;
    // Budget for the 60 Hz target plus two scheduling frames. An immediate
    // Start before any echo completes gets the conservative bounded maximum.
    return view.timing_samples ? uint8_t(std::clamp((view.round_trip_ms*60+999)/1000+2,3u,16u)) : 16;
  }
  bool SendTiming(Peer& peer) {
    if (peer.timing_pending || peer.close_after_send) return true;
    if (peer.timing_sent==UINT64_MAX) return false;
    Bytes bytes{0}; Number(bytes,peer.timing_sent+1,8);
    if (!Queue(peer,Message::Ping,bytes)) return false;
    ++peer.timing_sent; peer.timing_started=now; peer.timing_pending=true;
    return true;
  }
  bool ReceiveTiming(Peer& peer,Reader& reader) {
    const auto kind=reader.Number(1), sequence=reader.Number(8);
    if (!peer.admitted || !reader.End() || kind>1 || !sequence) return false;
    if (!kind) {
      if (peer.timing_received==UINT64_MAX || sequence!=peer.timing_received+1) return false;
      Bytes bytes{1}; Number(bytes,sequence,8);
      if (!Queue(peer,Message::Ping,bytes)) return false;
      peer.timing_received=sequence; return true;
    }
    if (!peer.timing_pending || sequence!=peer.timing_sent) return false;
    view.round_trip_ms=uint32_t(std::min<uint64_t>(now-peer.timing_started,10000));
    if (view.timing_samples!=UINT64_MAX) ++view.timing_samples;
    peer.timing_pending=false; return true;
  }
  void ClearPreparation() {
    retired_preparation=0; retired_input_received=0;
    view.shopping_done_sent=false; view.shopping_done_received=false; shopping_done_pending=false;
    view.cash_sent=0; view.cash_received=0; checkpoint_cash.clear();
    view.preparation=0; view.input_delay_frames=0; view.loadouts={}; view.prepared=false;
    view.imported={}; view.viewports={}; view.load_authorized=false; submitted=false; import_submitted=false;
    view.barrier_sent=0; view.barrier_received=0;
    view.input_sent=0; view.input_received=0;
    native_inputs.clear(); native_input_bytes=0;
    checkpoint.clear(); checkpoint_sent=0; checkpoint_ack_submitted=false;
    view.checkpoint_ready=false; view.checkpoint_bytes=0; view.checkpoint_imported={};
  }
  void CloseAll() {
    Close(listener);
    for (auto& peer : peers) Close(peer.socket);
    peers.clear(); port = 0; connected = false;
    view.round_trip_ms=0; view.timing_samples=0;
    SecureZeroMemory(invite.data(), invite.size());
  }
  bool Fail(const char* text) {
    CloseAll(); view.phase = CoopLobbyPhase::Failed; view.error = text;
    view.members.clear(); ClearPreparation();
    ++view.revision; return false;
  }
  void Reset() { CloseAll(); view = {}; ClearPreparation(); local_name.clear(); host = false; experimental_input_buffer=false; fixed_probe_frames=0; next_preparation=0; now = last_ping = 0; }
  bool Identity(const std::string& name) {
    if (!NameValid(name) || !NetworkReady()) return Fail("Invalid name or network initialization failed");
    std::array<uint8_t,8> bytes{};
    if (!Random(bytes)) return Fail("Identity generation failed");
    Reader reader{bytes}; view.local_id = reader.Number(8);
    if (!view.local_id) return Fail("Identity generation failed");
    local_name = name; return true;
  }
  bool Queue(Peer& peer, Message type, const Bytes& data = {}) {
    if (data.size() > kMaxPayload || peer.outgoing.size()-peer.sent+data.size()+8 > kMaxBuffered) return false;
    if (peer.sent) {
      peer.outgoing.erase(peer.outgoing.begin(), peer.outgoing.begin()+peer.sent); peer.sent = 0;
    }
    const auto frame = Frame(type, data);
    peer.outgoing.insert(peer.outgoing.end(), frame.begin(), frame.end()); return true;
  }
  Bytes Description() const {
    Bytes bytes(view.room.begin(), view.room.end());
    Number(bytes, uint8_t(view.settings.visibility), 1);
    Number(bytes, view.settings.difficulty, 1); String(bytes, view.settings.map);
    Number(bytes, view.members.size(), 1);
    for (const auto& member : view.members) {
      Number(bytes, member.id, 8); String(bytes, member.name);
      Number(bytes, member.character, 1); Number(bytes, member.ready, 1);
    }
    return bytes;
  }
  void Broadcast() {
    const auto description = Description();
    for (auto& peer : peers)
      if (peer.admitted && !Queue(peer, Message::Members, description)) Close(peer.socket);
  }
  Bytes Equipment(uint8_t index) const {
    Bytes bytes; Number(bytes,view.preparation,8); Number(bytes,index,1);
    const auto& equipment=*view.loadouts[index];
    Number(bytes,equipment.armor,4); Number(bytes,equipment.mask,4);
    Number(bytes,equipment.weapons.size(),1);
    for (const auto& weapon:equipment.weapons) {
      String(bytes,weapon.archetype); String(bytes,weapon.class_name);
      for (const auto upgrade:weapon.upgrades) Number(bytes,upgrade,4);
    }
    return bytes;
  }
  bool ReadEquipment(Reader& reader,uint8_t& index,CoopLoadout& equipment) {
    const auto preparation=reader.Number(8); index=uint8_t(reader.Number(1));
    equipment.armor=uint32_t(reader.Number(4)); equipment.mask=uint32_t(reader.Number(4));
    const auto count=reader.Number(1);
    if (!reader.ok || !view.preparation || preparation!=view.preparation || index>1 || count>40) return false;
    for (unsigned i=0;i<count;++i) {
      CoopWeapon weapon; weapon.archetype=reader.String(128); weapon.class_name=reader.String(128);
      for (auto& upgrade:weapon.upgrades) upgrade=uint32_t(reader.Number(4));
      equipment.weapons.push_back(std::move(weapon));
    }
    return reader.End() && CoopLobby::ValidLoadout(equipment);
  }
  bool PublishEquipment(uint8_t index) {
    const auto bytes=Equipment(index);
    for (auto& peer:peers) if (peer.admitted && !Queue(peer,Message::Loadout,bytes)) return false;
    if (view.loadouts[0] && view.loadouts[1]) {
      Bytes prepared; Number(prepared,view.preparation,8);
      for (auto& peer:peers) if (peer.admitted && !Queue(peer,Message::Prepared,prepared)) return false;
      view.prepared=true;
    }
    ++view.revision; return true;
  }
  bool Reject(Peer& peer, const char* reason) {
    Bytes bytes; String(bytes, reason); peer.close_after_send = true;
    return Queue(peer, Message::Reject, bytes);
  }
  bool AuthorizeLoad() {
    if (view.load_authorized || !view.imported[0] || !view.imported[1] ||
        (CoopLobby::RequiresCheckpoint(view.settings) &&
         (!view.checkpoint_imported[0] || !view.checkpoint_imported[1]))) return true;
    Bytes bytes; Number(bytes,view.preparation,8);
    for (auto& peer:peers) if (peer.admitted && !Queue(peer,Message::AuthorizeLoad,bytes)) return false;
    view.load_authorized=true; ++view.revision; return true;
  }
  bool PublishCheckpointImport(uint8_t index) {
    Bytes bytes; Number(bytes,view.preparation,8); Number(bytes,index,1);
    Number(bytes,view.checkpoint_bytes,4);
    for (auto& peer:peers) if (peer.admitted && !Queue(peer,Message::CheckpointImported,bytes)) return false;
    ++view.revision; return AuthorizeLoad();
  }
  bool SendCheckpointChunk(Peer& peer) {
    if (!host || !peer.admitted || !view.checkpoint_ready || checkpoint_sent==checkpoint.size()) return true;
    const auto count=std::min(size_t(8192),checkpoint.size()-checkpoint_sent);
    // Apply backpressure instead of filling the bounded socket output queue.
    if (peer.outgoing.size()-peer.sent+count+24>kMaxBuffered) return true;
    Bytes bytes; Number(bytes,view.preparation,8); Number(bytes,checkpoint.size(),4); Number(bytes,checkpoint_sent,4);
    bytes.insert(bytes.end(),checkpoint.begin()+checkpoint_sent,checkpoint.begin()+checkpoint_sent+count);
    if (!Queue(peer,Message::CheckpointChunk,bytes)) return false;
    checkpoint_sent+=count; return true;
  }
  bool ReceiveCheckpointChunk(Reader& reader) {
    const auto preparation=reader.Number(8), total=reader.Number(4), offset=reader.Number(4);
    if (!reader.ok || !view.prepared || preparation!=view.preparation || !CoopLobby::RequiresCheckpoint(view.settings) ||
        view.checkpoint_ready || !total || total>CoopLobby::kMaxCheckpointBytes || offset!=checkpoint.size() ||
        (view.checkpoint_bytes && total!=view.checkpoint_bytes)) return false;
    const auto chunk=reader.bytes.subspan(reader.offset);
    if (chunk.empty() || chunk.size()>8192 || offset+chunk.size()>total) return false;
    view.checkpoint_bytes=uint32_t(total);
    checkpoint.insert(checkpoint.end(),chunk.begin(),chunk.end());
    if (checkpoint.size()==total) { view.checkpoint_ready=true; ++view.revision; }
    return true;
  }
  bool PublishImport(uint8_t index) {
    Bytes bytes; Number(bytes,view.preparation,8); Number(bytes,index,1);
    Number(bytes,view.viewports[index].width,4); Number(bytes,view.viewports[index].height,4);
    for (auto& peer:peers) if (peer.admitted && !Queue(peer,Message::Imported,bytes)) return false;
    ++view.revision; return AuthorizeLoad();
  }
  bool ReceiveBarrier(Reader& reader) {
    const auto preparation=reader.Number(8), sequence=reader.Number(8);
    if (!reader.End() || !view.load_authorized || preparation!=view.preparation ||
        !sequence || sequence!=view.barrier_received+1 || sequence>view.barrier_sent+1) return false;
    view.barrier_received=sequence; return true;
  }
  bool ReceiveInput(Reader& reader) {
    const auto preparation=reader.Number(8), sequence=reader.Number(8);
    // A peer can have old-world input in flight when the host begins travel.
    // Drain only validated, consecutive packets from that immediately retired
    // world, before the peer submits its new equipment. Never apply them.
    if (reader.ok && host && preparation && preparation==retired_preparation &&
        !view.load_authorized && !view.loadouts[1] && sequence==retired_input_received+1 &&
        ValidCoopInputPacket(reader.bytes.subspan(reader.offset),1)) {
      retired_input_received=sequence; return true;
    }
    if (!reader.ok || !view.load_authorized || preparation!=view.preparation ||
        !sequence || sequence!=view.input_received+1) return false;
    const auto packet=reader.bytes.subspan(reader.offset);
    if (!ValidCoopInputPacket(packet,host ? 1 : 0) || native_inputs.size()>=128 ||
        native_input_bytes+packet.size()>kMaxBuffered) return false;
    native_inputs.emplace_back(packet.begin(),packet.end());
    native_input_bytes+=packet.size(); view.input_received=sequence; return true;
  }
  bool ReceiveShoppingDone(Reader& reader) {
    const auto preparation=reader.Number(8);
    if (!reader.End() || !preparation || preparation!=view.preparation || !view.load_authorized ||
        view.shopping_done_received) return false;
    view.shopping_done_received=true; shopping_done_pending=true; return true;
  }
  bool ReceiveCheckpointCash(Reader& reader) {
    const auto preparation=reader.Number(8), sequence=reader.Number(8), total=reader.Number(4);
    if (!reader.End() || !preparation || preparation!=view.preparation || !view.load_authorized ||
        view.shopping_done_received || !sequence || sequence!=view.cash_received+1 ||
        total>INT32_MAX || checkpoint_cash.size()>=32) return false;
    checkpoint_cash.push_back(uint32_t(total)); view.cash_received=sequence; return true;
  }
  bool ReceiveHost(Peer& peer, Message type, const Bytes& payload) {
    Reader reader{payload};
    if (!peer.admitted) {
      if (type != Message::Join || peer.close_after_send) return false;
      const auto visibility = reader.Number(1), id = reader.Number(8);
      const auto name = reader.String(32);
      unsigned mismatch = 0;
      for (auto expected : invite) mismatch |= unsigned(reader.Number(1)) ^ expected;
      if (!reader.End() || visibility > 1 || !id || !NameValid(name)) return false;
      if (visibility != uint8_t(view.settings.visibility) || mismatch)
        return Reject(peer, "The invitation does not match this session");
      if (view.members.size() != 1 || id == view.local_id)
        return Reject(peer, "The campaign session is full");
      peer.id = id; peer.admitted = true;
      view.members.push_back({id,name,uint8_t(1-view.members[0].character),false}); ++view.revision;
      return Queue(peer, Message::Welcome, Description());
    }
    if (type == Message::Ping) return ReceiveTiming(peer,reader);
    if (type==Message::NativeBarrier) return ReceiveBarrier(reader);
    if (type==Message::NativeInput) return ReceiveInput(reader);
    if (type==Message::ShoppingDone) return ReceiveShoppingDone(reader);
    if (type==Message::CheckpointCash) return ReceiveCheckpointCash(reader);
    if (type==Message::CheckpointImported) {
      const auto preparation=reader.Number(8), index=reader.Number(1), size=reader.Number(4);
      if (!reader.End() || preparation!=view.preparation || index!=1 || !view.checkpoint_ready ||
          checkpoint_sent!=checkpoint.size() || size!=checkpoint.size() || view.checkpoint_imported[1]) return false;
      view.checkpoint_imported[1]=true; return PublishCheckpointImport(1);
    }
    if (type==Message::Imported) {
      const auto preparation=reader.Number(8), index=reader.Number(1);
      const CoopViewport viewport{uint32_t(reader.Number(4)),uint32_t(reader.Number(4))};
      if (!reader.End() || !CoopLobby::ValidViewport(viewport) || !view.prepared ||
          preparation!=view.preparation || index!=1 || view.imported[1]) return false;
      view.viewports[1]=viewport; view.imported[1]=true; return PublishImport(1);
    }
    if (type==Message::Loadout) {
      CoopLoadout equipment; uint8_t index=0;
      if (!ReadEquipment(reader,index,equipment) || index!=1 || view.loadouts[1]) return false;
      view.loadouts[1]=std::move(equipment); return PublishEquipment(1);
    }
    if (type != Message::Ready) return false;
    const auto ready = reader.Number(1);
    if (!reader.End() || ready > 1) return false;
    auto member = std::find_if(view.members.begin(),view.members.end(),[&](const auto& m){return m.id==peer.id;});
    if (member == view.members.end()) return false;
    if (member->ready != bool(ready)) { member->ready = bool(ready); ++view.revision; Broadcast(); }
    return true;
  }
  bool ReceiveClient(Peer& peer, Message type, const Bytes& payload) {
    Reader reader{payload};
    if (type == Message::Ping) return ReceiveTiming(peer,reader);
    if (peer.admitted && type==Message::NativeBarrier) return ReceiveBarrier(reader);
    if (peer.admitted && type==Message::NativeInput) return ReceiveInput(reader);
    if (peer.admitted && type==Message::ShoppingDone) return ReceiveShoppingDone(reader);
    if (peer.admitted && type==Message::CheckpointCash) return ReceiveCheckpointCash(reader);
    if (peer.admitted && type==Message::CheckpointChunk) return ReceiveCheckpointChunk(reader);
    if (peer.admitted && type==Message::CheckpointImported) {
      const auto preparation=reader.Number(8), index=reader.Number(1), size=reader.Number(4);
      if (!reader.End() || !view.prepared || preparation!=view.preparation || !CoopLobby::RequiresCheckpoint(view.settings) ||
          index>1 || !size || size>CoopLobby::kMaxCheckpointBytes || view.checkpoint_imported[index] ||
          (view.checkpoint_bytes && size!=view.checkpoint_bytes) ||
          (index==1 && (!view.checkpoint_ready || !checkpoint_ack_submitted))) return false;
      view.checkpoint_bytes=uint32_t(size); view.checkpoint_imported[index]=true; ++view.revision; return true;
    }
    if (peer.admitted && type==Message::Prepare) {
      const auto preparation=reader.Number(8);
      auto settings=view.settings;
      settings.difficulty=uint8_t(reader.Number(1));
      const auto delay=reader.Number(1); settings.map=reader.String(192);
      if (!reader.End() || !preparation || preparation<=view.preparation ||
          (delay!=0 && (delay<3 || delay>16)) ||
          (view.preparation && !view.load_authorized) || !CoopLobby::ValidSettings(settings) ||
          (view.preparation && settings.difficulty!=view.settings.difficulty)) return false;
      ClearPreparation(); view.settings=std::move(settings); view.preparation=preparation;
      view.input_delay_frames=uint8_t(delay);
      preparation_started=now; ++view.revision; return true;
    }
    if (peer.admitted && type==Message::Loadout) {
      CoopLoadout equipment; uint8_t index=0;
      if (!ReadEquipment(reader,index,equipment) || view.loadouts[index]) return false;
      view.loadouts[index]=std::move(equipment); ++view.revision; return true;
    }
    if (peer.admitted && type==Message::Prepared) {
      const auto preparation=reader.Number(8);
      if (!reader.End() || preparation!=view.preparation || !preparation ||
          !view.loadouts[0] || !view.loadouts[1] || view.prepared) return false;
      view.prepared=true; ++view.revision; return true;
    }
    if (peer.admitted && type==Message::Imported) {
      const auto preparation=reader.Number(8), index=reader.Number(1);
      const CoopViewport viewport{uint32_t(reader.Number(4)),uint32_t(reader.Number(4))};
      if (!reader.End() || !CoopLobby::ValidViewport(viewport) || !view.prepared || preparation!=view.preparation || index>1 ||
          view.imported[index] || (index==1 && !import_submitted)) return false;
      if (index==1 && viewport!=view.viewports[1]) return false;
      view.viewports[index]=viewport; view.imported[index]=true; ++view.revision; return true;
    }
    if (peer.admitted && type==Message::AuthorizeLoad) {
      const auto preparation=reader.Number(8);
      if (!reader.End() || !view.prepared || preparation!=view.preparation || !view.imported[0] ||
          !view.imported[1] || view.load_authorized || (CoopLobby::RequiresCheckpoint(view.settings) &&
          (!view.checkpoint_imported[0] || !view.checkpoint_imported[1]))) return false;
      view.load_authorized=true; ++view.revision; return true;
    }
    // The host may also reject an already admitted member from its lobby.
    // Once preparation begins, session teardown must use the campaign path.
    if (type == Message::Reject && (!peer.admitted || !view.preparation)) {
      const auto reason = reader.String(120);
      if (!reader.End()) return false;
      view.phase = CoopLobbyPhase::Failed; view.error = reason; view.members.clear(); ++view.revision;
      return false;
    }
    if ((!peer.admitted && type != Message::Welcome) ||
        (peer.admitted && type != Message::Members)) return false;
    CoopLobbySnapshot candidate = view;
    for (auto& byte : candidate.room) byte = uint8_t(reader.Number(1));
    candidate.settings.visibility = CoopVisibility(reader.Number(1));
    candidate.settings.difficulty = uint8_t(reader.Number(1));
    candidate.settings.map = reader.String(192);
    const auto count = reader.Number(1);
    if (!reader.ok || count != 2 || !CoopLobby::ValidSettings(candidate.settings) ||
        candidate.settings.visibility != view.settings.visibility ||
        std::all_of(candidate.room.begin(),candidate.room.end(),[](auto b){return !b;})) return false;
    if (peer.admitted && (candidate.room != view.room || candidate.settings != view.settings)) return false;
    candidate.members.clear();
    for (unsigned i=0; i<count; ++i) {
      CoopMember member; member.id = reader.Number(8); member.name = reader.String(32);
      member.character = uint8_t(reader.Number(1)); const auto ready = reader.Number(1);
      if (!reader.ok || !member.id || !NameValid(member.name) || member.character > 1 || ready > 1) return false;
      member.ready = bool(ready); candidate.members.push_back(std::move(member));
    }
    if (!reader.End() || candidate.members[0].character == candidate.members[1].character ||
        candidate.members[0].id == candidate.members[1].id ||
        candidate.members[1].id != view.local_id || candidate.members[1].name != local_name) return false;
    candidate.phase = CoopLobbyPhase::Joined; candidate.error.clear(); candidate.revision = view.revision+1;
    view = std::move(candidate); peer.admitted = true; return true;
  }
  bool Flush(Peer& peer) {
    while (peer.sent < peer.outgoing.size()) {
      const int result = send(peer.socket,reinterpret_cast<const char*>(peer.outgoing.data()+peer.sent),
                              int(peer.outgoing.size()-peer.sent),0);
      if (result == SOCKET_ERROR) return WSAGetLastError() == WSAEWOULDBLOCK;
      if (!result) return false;
      peer.sent += size_t(result);
    }
    peer.outgoing.clear(); peer.sent = 0;
    return !peer.close_after_send;
  }
  bool ReadPeer(Peer& peer) {
    uint8_t buffer[1024];
    bool closed = false;
    // Bound work per frame, even when a peer floods the connection.
    for (unsigned iteration=0; iteration<4; ++iteration) {
      const int received = recv(peer.socket,reinterpret_cast<char*>(buffer),sizeof(buffer),0);
      if (received == SOCKET_ERROR) { if (WSAGetLastError()==WSAEWOULDBLOCK) break; closed = true; break; }
      if (!received) { closed = true; break; }
      if (peer.incoming.size()+received > kMaxBuffered) return false;
      peer.incoming.insert(peer.incoming.end(),buffer,buffer+received);
    }
    for (unsigned packets=0; packets<8 && peer.incoming.size()>=8; ++packets) {
      const auto& bytes = peer.incoming;
      if (std::memcmp(bytes.data(),"AOTC",4) || bytes[4]!=11 || bytes[5]<1 || bytes[5]>17) return false;
      const size_t length = (size_t(bytes[6])<<8)|bytes[7];
      if (length > kMaxPayload) return false;
      if (bytes.size()<8+length) break;
      const auto type = Message(bytes[5]);
      Bytes payload(bytes.begin()+8,bytes.begin()+8+length);
      peer.incoming.erase(peer.incoming.begin(),peer.incoming.begin()+8+length);
      if (!(host ? ReceiveHost(peer,type,payload) : ReceiveClient(peer,type,payload))) return false;
      peer.last_receive = now;
    }
    return !closed;
  }
  void Drop(Peer& peer) {
    Close(peer.socket);
    if (host && peer.admitted) {
      std::erase_if(view.members,[&](const auto& member){return member.id==peer.id;}); ++view.revision;
    } else if (!host && view.phase != CoopLobbyPhase::Failed) {
      view.phase = CoopLobbyPhase::Disconnected; view.error = "The host disconnected";
      view.members.clear(); ++view.revision;
    }
    if (peer.admitted) { ClearPreparation(); view.round_trip_ms=0; view.timing_samples=0; }
  }
};

CoopLobby::CoopLobby() : impl_(std::make_unique<Impl>()) {}
CoopLobby::~CoopLobby() = default;
bool CoopLobby::ValidLoadout(const CoopLoadout& equipment) {
  const auto asset=[](const std::string& text) {
    return !text.empty() && text.size()<=128 && text.front()!='.' &&
        text.find("..") == std::string::npos &&
        text.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")==std::string::npos;
  };
  // Retail profile setup stores -1 when no armor is equipped.
  if ((equipment.armor>65535 && equipment.armor!=UINT32_MAX) || equipment.mask>65535 ||
      equipment.weapons.empty() || equipment.weapons.size()>40) return false;
  for (const auto& weapon:equipment.weapons) {
    if (!asset(weapon.archetype) || !asset(weapon.class_name) ||
        std::any_of(weapon.upgrades.begin(),weapon.upgrades.end(),[](auto n){return n>255;})) return false;
  }
  return true;
}
bool CoopLobby::ValidSettings(const CoopLobbySettings& settings) {
  constexpr std::string_view prefix = "Checkpoint?LoadSaveGame?";
  if (uint8_t(settings.visibility)>1 || settings.difficulty>2 || settings.map.size()>192 ||
      !settings.map.starts_with(prefix)) return false;
  auto remaining = std::string_view(settings.map).substr(prefix.size());
  bool difficulty = false, checkpoint = false, global_checkpoint = false;
  while (!remaining.empty()) {
    const auto end = remaining.find('?');
    const auto field = remaining.substr(0,end);
    remaining = end == std::string_view::npos ? std::string_view{} : remaining.substr(end+1);
    if (field.starts_with("Difficulty=")) {
      auto value = field.substr(11);
      if (difficulty || value.size()!=1 || value[0] != char('0'+settings.difficulty)) return false;
      difficulty = true;
    } else if (field=="UseGlobalCheckpoint") {
      if (global_checkpoint || checkpoint) return false;
      global_checkpoint=true;
    } else {
      std::string_view value;
      if (field.starts_with("CheckpointToLoad=")) value = field.substr(17);
      else if (field.starts_with("FromCheckpoint=")) value = field.substr(15);
      else return false;
      if (checkpoint || global_checkpoint || value.empty() || value.size()>16 ||
          value.find_first_not_of("0123456789_") != std::string_view::npos) return false;
      checkpoint = true;
    }
  }
  return difficulty;
}
bool CoopLobby::Host(const CoopLobbySettings& settings, const std::string& name,
                     const std::string& bind_ipv4, uint16_t port, uint64_t now_ms,
                     bool experimental_input_buffer, uint8_t fixed_probe_frames) {
  auto& state = *impl_; state.Reset();
  if (fixed_probe_frames && (!experimental_input_buffer || fixed_probe_frames<3 || fixed_probe_frames>16))
    return state.Fail("Invalid diagnostic input buffer");
  state.experimental_input_buffer=experimental_input_buffer;
  state.fixed_probe_frames=fixed_probe_frames;
  if (!ValidSettings(settings) || !state.Identity(name)) return state.Fail("Invalid campaign settings or identity");
  sockaddr_in address{};
  if (!Endpoint(bind_ipv4,port,address)) return state.Fail("Invalid bind address");
  state.listener = socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  if (state.listener == INVALID_SOCKET || !Nonblocking(state.listener)) return state.Fail("Cannot create lobby socket");
  BOOL exclusive = TRUE;
  if (setsockopt(state.listener,SOL_SOCKET,SO_EXCLUSIVEADDRUSE,reinterpret_cast<char*>(&exclusive),sizeof(exclusive)) ||
      bind(state.listener,reinterpret_cast<sockaddr*>(&address),sizeof(address)) || listen(state.listener,4))
    return state.Fail("Cannot listen on the lobby port");
  int size=sizeof(address);
  if (getsockname(state.listener,reinterpret_cast<sockaddr*>(&address),&size)) return state.Fail("Cannot read lobby port");
  if (!Random(state.view.room) || (settings.visibility==CoopVisibility::Private && !Random(state.invite)))
    return state.Fail("Room generation failed");
  state.host=true; state.now=state.last_ping=now_ms; state.port=ntohs(address.sin_port);
  state.view.settings=settings; state.view.phase=CoopLobbyPhase::Hosting;
  state.view.members.push_back({state.view.local_id,name,0,false}); ++state.view.revision;
  return true;
}
bool CoopLobby::Join(const std::string& ipv4, uint16_t port, CoopVisibility visibility,
                     const std::string& invite, const std::string& name, uint64_t now_ms) {
  auto& state=*impl_; state.Reset();
  if (!state.Identity(name)) return false;
  if (uint8_t(visibility)>1 || !port || (visibility==CoopVisibility::Private && !DecodeInvite(invite,state.invite)) ||
      (visibility==CoopVisibility::Public && !invite.empty())) return state.Fail("Invalid invitation");
  sockaddr_in address{};
  if (!Endpoint(ipv4,port,address) || address.sin_addr.s_addr==INADDR_ANY || address.sin_addr.s_addr==INADDR_BROADCAST)
    return state.Fail("Invalid host address");
  Impl::Peer peer; peer.socket=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  if (peer.socket==INVALID_SOCKET || !PreparePeerSocket(peer.socket)) { Close(peer.socket); return state.Fail("Cannot configure peer socket"); }
  const int result=connect(peer.socket,reinterpret_cast<sockaddr*>(&address),sizeof(address));
  if (result==SOCKET_ERROR && WSAGetLastError()!=WSAEWOULDBLOCK) { Close(peer.socket); return state.Fail("Cannot connect to host"); }
  peer.created=peer.last_receive=now_ms; state.peers.push_back(std::move(peer));
  state.connected=result==0; state.now=state.last_ping=now_ms;
  state.view.settings.visibility=visibility; state.view.phase=CoopLobbyPhase::Connecting; ++state.view.revision;
  Bytes data; Number(data,uint8_t(visibility),1); Number(data,state.view.local_id,8); String(data,name);
  data.insert(data.end(),state.invite.begin(),state.invite.end());
  return state.Queue(state.peers[0],Message::Join,data);
}
void CoopLobby::Poll(uint64_t now_ms) {
  auto& state=*impl_; state.now=std::max(state.now,now_ms);
  if (state.view.preparation && !state.view.load_authorized && state.now-state.preparation_started>30000) {
    state.Fail("Campaign equipment preparation timed out"); return;
  }
  if (state.listener != INVALID_SOCKET && state.peers.size()<4) {
    SOCKET accepted=accept(state.listener,nullptr,nullptr);
    if (accepted!=INVALID_SOCKET) {
      if (!PreparePeerSocket(accepted)) Close(accepted);
      else { Impl::Peer peer; peer.socket=accepted; peer.created=peer.last_receive=state.now; state.peers.push_back(std::move(peer)); }
    }
  }
  if (!state.host && !state.connected && !state.peers.empty()) {
    auto& peer=state.peers[0]; fd_set writable{},failed{}; FD_SET(peer.socket,&writable); FD_SET(peer.socket,&failed);
    timeval timeout{};
    const int selected=select(0,nullptr,&writable,&failed,&timeout);
    if (selected==SOCKET_ERROR || FD_ISSET(peer.socket,&failed) || state.now-peer.created>kHandshakeTimeout) {
      state.Fail("Connection failed or timed out"); return;
    }
    if (!FD_ISSET(peer.socket,&writable)) return;
    int error=0,size=sizeof(error);
    if (getsockopt(peer.socket,SOL_SOCKET,SO_ERROR,reinterpret_cast<char*>(&error),&size) || error) {
      state.Fail("Connection failed"); return;
    }
    state.connected=true;
  }
  const bool ping=state.now-state.last_ping>=1000;
  if (ping) state.last_ping=state.now;
  for (auto& peer:state.peers) {
    const bool expired=peer.admitted ? state.now-peer.last_receive>kPeerTimeout : state.now-peer.created>kHandshakeTimeout;
    if (peer.socket==INVALID_SOCKET || expired || (ping && peer.admitted && !state.SendTiming(peer)) ||
        // Consume a queued rejection before attempting another write to a
        // socket the host may already have closed (for example a lobby kick).
        !state.ReadPeer(peer) || !state.SendCheckpointChunk(peer) || !state.Flush(peer)) state.Drop(peer);
  }
  std::erase_if(state.peers,[](const auto& peer){return peer.socket==INVALID_SOCKET;});
}
bool CoopLobby::SetReady(bool ready) {
  auto& state=*impl_;
  if (state.host && state.view.phase==CoopLobbyPhase::Hosting) {
    if (state.view.members[0].ready!=ready) { state.view.members[0].ready=ready; ++state.view.revision; state.Broadcast(); }
    return true;
  }
  if (state.view.phase!=CoopLobbyPhase::Joined || state.peers.size()!=1) return false;
  return state.Queue(state.peers[0],Message::Ready,Bytes{uint8_t(ready)});
}
bool CoopLobby::SetHostCharacter(uint8_t character) {
  auto& state=*impl_;
  if (!state.host || state.view.phase!=CoopLobbyPhase::Hosting || character>1) return false;
  if (state.view.members[0].character!=character) {
    state.view.members[0].character=character;
    if (state.view.members.size()==2) state.view.members[1].character=1-character;
    ++state.view.revision; state.Broadcast();
  }
  return true;
}
bool CoopLobby::RequestPreparation() {
  auto& state=*impl_;
  if (!state.host || state.view.phase!=CoopLobbyPhase::Hosting || state.view.members.size()!=2 ||
      state.view.preparation || state.next_preparation==UINT64_MAX) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted && !p.close_after_send;});
  if (peer==state.peers.end()) return false;
  const auto preparation=++state.next_preparation;
  const auto delay=state.ChooseInputDelay();
  Bytes request; Number(request,preparation,8);
  Number(request,state.view.settings.difficulty,1); Number(request,delay,1); String(request,state.view.settings.map);
  if (!state.Queue(*peer,Message::Prepare,request)) return false;
  state.ClearPreparation(); state.view.preparation=preparation;
  state.view.input_delay_frames=delay;
  state.submitted=false; state.preparation_started=state.now; ++state.view.revision; return true;
}
bool CoopLobby::RequestTransition(const CoopLobbySettings& settings) {
  auto& state=*impl_;
  if (!state.host || state.view.phase!=CoopLobbyPhase::Hosting || state.view.members.size()!=2 ||
      !state.view.load_authorized || !ValidSettings(settings) ||
      settings.visibility!=state.view.settings.visibility || settings.difficulty!=state.view.settings.difficulty ||
      state.next_preparation==UINT64_MAX) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted && !p.close_after_send;});
  if (peer==state.peers.end()) return false;
  const auto preparation=state.next_preparation+1;
  const auto delay=state.ChooseInputDelay();
  Bytes request; Number(request,preparation,8); Number(request,settings.difficulty,1); Number(request,delay,1); String(request,settings.map);
  if (!state.Queue(*peer,Message::Prepare,request)) return false;
  const auto retired=state.view.preparation, received=state.view.input_received;
  state.ClearPreparation(); state.retired_preparation=retired; state.retired_input_received=received;
  state.view.settings=settings; state.view.preparation=preparation; state.next_preparation=preparation;
  state.view.input_delay_frames=delay;
  state.preparation_started=state.now; ++state.view.revision; return true;
}
bool CoopLobby::Kick(uint64_t member_id) {
  auto& state=*impl_;
  if (!state.host || state.view.phase!=CoopLobbyPhase::Hosting || state.view.preparation ||
      !member_id || member_id==state.view.local_id || state.view.members.size()!=2) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[&](const auto& p){
    return p.admitted && p.id==member_id && !p.close_after_send;
  });
  return peer!=state.peers.end() && state.Reject(*peer,"The host removed you from the lobby.");
}
bool CoopLobby::SubmitLoadout(uint64_t preparation,const CoopLoadout& equipment) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || state.view.prepared ||
      state.view.members.size()!=2 || state.submitted || !ValidLoadout(equipment)) return false;
  if (state.host && state.view.phase==CoopLobbyPhase::Hosting) {
    state.view.loadouts[0]=equipment; state.submitted=true;
    if (!state.PublishEquipment(0)) return state.Fail("Could not publish campaign equipment");
    return true;
  }
  if (state.view.phase!=CoopLobbyPhase::Joined || state.peers.size()!=1) return false;
  // Serialize without publishing client state until the host accepts it.
  state.view.loadouts[1]=equipment;
  const auto bytes=state.Equipment(1); state.view.loadouts[1].reset();
  if (!state.Queue(state.peers[0],Message::Loadout,bytes)) return false;
  state.submitted=true; return true;
}
bool CoopLobby::ValidViewport(const CoopViewport& viewport) {
  return viewport.width>=1 && viewport.width<=16384 && viewport.height>=1 && viewport.height<=16384;
}
bool CoopLobby::RequiresCheckpoint(const CoopLobbySettings& settings) {
  return settings.map.find("?LoadSaveGame")!=std::string::npos &&
         settings.map.find("?CheckpointToLoad=")==std::string::npos;
}
bool CoopLobby::SubmitCheckpoint(uint64_t preparation,std::span<const uint8_t> checkpoint) {
  auto& state=*impl_;
  if (!state.host || !state.view.prepared || !preparation || preparation!=state.view.preparation ||
      state.view.members.size()!=2 || !RequiresCheckpoint(state.view.settings) || state.view.checkpoint_ready ||
      checkpoint.empty() || checkpoint.size()>kMaxCheckpointBytes) return false;
  state.checkpoint.assign(checkpoint.begin(),checkpoint.end());
  state.view.checkpoint_bytes=uint32_t(checkpoint.size()); state.view.checkpoint_ready=true;
  ++state.view.revision; return true;
}
std::span<const uint8_t> CoopLobby::Checkpoint() const {
  return impl_->view.checkpoint_ready ? std::span<const uint8_t>(impl_->checkpoint) : std::span<const uint8_t>{};
}
bool CoopLobby::AcknowledgeCheckpoint(uint64_t preparation) {
  auto& state=*impl_;
  if (!state.view.prepared || !preparation || preparation!=state.view.preparation ||
      state.view.members.size()!=2 || !state.view.checkpoint_ready || state.checkpoint_ack_submitted ||
      !RequiresCheckpoint(state.view.settings) || state.view.load_authorized) return false;
  if (state.host) {
    state.checkpoint_ack_submitted=true; state.view.checkpoint_imported[0]=true;
    if (!state.PublishCheckpointImport(0)) return state.Fail("Could not acknowledge the host checkpoint");
    return true;
  }
  if (state.peers.size()!=1 || state.view.phase!=CoopLobbyPhase::Joined) return false;
  Bytes bytes; Number(bytes,preparation,8); Number(bytes,1,1); Number(bytes,state.checkpoint.size(),4);
  if (!state.Queue(state.peers[0],Message::CheckpointImported,bytes)) return false;
  state.checkpoint_ack_submitted=true; return true;
}
bool CoopLobby::AcknowledgeImport(uint64_t preparation,const CoopViewport& viewport) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || !state.view.prepared ||
      state.view.members.size()!=2 || state.import_submitted || state.view.load_authorized || !ValidViewport(viewport)) return false;
  if (state.host && state.view.phase==CoopLobbyPhase::Hosting) {
    state.import_submitted=true; state.view.imported[0]=true; state.view.viewports[0]=viewport;
    if (!state.PublishImport(0)) return state.Fail("Could not acknowledge campaign import");
    return true;
  }
  if (state.view.phase!=CoopLobbyPhase::Joined || state.peers.size()!=1) return false;
  Bytes bytes; Number(bytes,preparation,8); Number(bytes,1,1);
  Number(bytes,viewport.width,4); Number(bytes,viewport.height,4);
  if (!state.Queue(state.peers[0],Message::Imported,bytes)) return false;
  state.view.viewports[1]=viewport; state.import_submitted=true; return true;
}
void CoopLobby::Stop() {
  auto& state=*impl_; state.CloseAll(); state.view.phase=CoopLobbyPhase::Disconnected;
  state.view.members.clear(); state.view.error.clear(); state.view.loadouts={};
  state.ClearPreparation(); ++state.view.revision;
}
bool CoopLobby::SignalNativeBarrier(uint64_t preparation) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || !state.view.load_authorized ||
      state.view.members.size()!=2 || state.view.barrier_sent>state.view.barrier_received ||
      state.view.barrier_sent==UINT64_MAX) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted;});
  if (peer==state.peers.end()) return false;
  Bytes bytes; Number(bytes,preparation,8); Number(bytes,state.view.barrier_sent+1,8);
  if (!state.Queue(*peer,Message::NativeBarrier,bytes)) return false;
  ++state.view.barrier_sent; return true;
}
uint16_t CoopLobby::Port() const { return impl_->port; }
bool CoopLobby::SignalShoppingDone(uint64_t preparation) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || !state.view.load_authorized ||
      state.view.members.size()!=2 || state.view.shopping_done_sent) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted;});
  if (peer==state.peers.end()) return false;
  Bytes bytes; Number(bytes,preparation,8);
  if (!state.Queue(*peer,Message::ShoppingDone,bytes)) return false;
  state.view.shopping_done_sent=true; return true;
}
bool CoopLobby::TakeShoppingDone() {
  auto& state=*impl_;
  const auto pending=state.shopping_done_pending; state.shopping_done_pending=false; return pending;
}
bool CoopLobby::SendCheckpointCash(uint64_t preparation,uint32_t total) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || !state.view.load_authorized ||
      state.view.members.size()!=2 || state.view.shopping_done_sent || total>INT32_MAX ||
      state.view.cash_sent==UINT64_MAX) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted && !p.close_after_send;});
  if (peer==state.peers.end()) return false;
  Bytes bytes; Number(bytes,preparation,8); Number(bytes,state.view.cash_sent+1,8); Number(bytes,total,4);
  if (!state.Queue(*peer,Message::CheckpointCash,bytes)) return false;
  ++state.view.cash_sent; return true;
}
std::optional<uint32_t> CoopLobby::TakeCheckpointCash() {
  auto& pending=impl_->checkpoint_cash;
  if (pending.empty()) return std::nullopt;
  const auto total=pending.front(); pending.pop_front(); return total;
}
bool CoopLobby::SendNativeInput(uint64_t preparation,std::span<const uint8_t> packet) {
  auto& state=*impl_;
  if (!preparation || preparation!=state.view.preparation || !state.view.load_authorized ||
      state.view.members.size()!=2 || state.view.input_sent==UINT64_MAX ||
      !ValidCoopInputPacket(packet,state.host ? 0 : 1)) return false;
  const auto peer=std::find_if(state.peers.begin(),state.peers.end(),[](const auto& p){return p.admitted;});
  if (peer==state.peers.end()) return false;
  Bytes bytes; Number(bytes,preparation,8); Number(bytes,state.view.input_sent+1,8);
  bytes.insert(bytes.end(),packet.begin(),packet.end());
  if (!state.Queue(*peer,Message::NativeInput,bytes)) return false;
  ++state.view.input_sent; return true;
}
std::optional<std::vector<uint8_t>> CoopLobby::TakeNativeInput() {
  auto& state=*impl_;
  if (state.native_inputs.empty()) return std::nullopt;
  auto packet=std::move(state.native_inputs.front()); state.native_inputs.pop_front();
  state.native_input_bytes-=packet.size(); return packet;
}
std::string CoopLobby::InviteCode() const {
  if (!impl_->host || impl_->view.phase!=CoopLobbyPhase::Hosting || impl_->view.settings.visibility!=CoopVisibility::Private) return {};
  constexpr char hex[]="0123456789abcdef"; std::string code;
  for (auto byte:impl_->invite) { code+=hex[byte>>4]; code+=hex[byte&15]; }
  return code;
}
const CoopLobbySnapshot& CoopLobby::Snapshot() const { return impl_->view; }
}
#endif
