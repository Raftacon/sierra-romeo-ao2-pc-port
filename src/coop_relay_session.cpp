#include "coop_relay_session.h"
#include "vendor/nlohmann/json.hpp"
#include <algorithm>
#include <stdexcept>

namespace aot {
namespace {
using Json=nlohmann::json;
class RelayResponseError : public std::runtime_error {
 public: using std::runtime_error::runtime_error;
};
Json Parse(const std::string& line){
  auto response=Json::parse(line,[](int depth,Json::parse_event_t,Json&){if(depth>4)throw std::runtime_error("Invalid relay response.");return true;});
  // Translate only exact protocol errors; never display arbitrary server text
  // (which may contain credentials or untrusted markup) in a game dialog.
  if(response.is_object()&&response.size()==1&&response.contains("error")&&response["error"].is_string()){
    const auto& error=response["error"].get_ref<const std::string&>();
    if(error=="room unavailable")throw RelayResponseError("This online game is no longer available. Refresh the game list or ask the host for a new invitation.");
    if(error=="relay busy"||error=="room limit reached")throw RelayResponseError("The online service is full. Please try again shortly.");
  }
  return response;
}
void Send(CoopTlsStream& stream,const Json& value){const auto text=value.dump()+"\n";stream.Write({reinterpret_cast<const uint8_t*>(text.data()),text.size()});}
}
std::string CoopRelaySession::Line(){
  for(;;){
    const auto newline=std::find(pending_.begin(),pending_.end(),'\n');
    if(newline!=pending_.end()){
      if(newline-pending_.begin()>1023)throw std::runtime_error("Relay response exceeds the handshake limit.");
      std::string result(pending_.begin(),newline);pending_.erase(pending_.begin(),newline+1);return result;
    }
    if(pending_.size()>=1024)throw std::runtime_error("Relay response is missing its delimiter.");
    std::vector<uint8_t> bytes;if(!stream_.Read(bytes))throw std::runtime_error("Relay closed during pairing.");
    pending_.insert(pending_.end(),bytes.begin(),bytes.end());
  }
}
CoopRelayRoute CoopRelaySession::RegisterHost(const std::string& endpoint){
  try{
    if(state_!=State::Initial||!endpoint.starts_with("tls://")||!CoopRelayRoute{endpoint,std::string(32,'0'),std::string(43,'a')}.Valid())throw std::runtime_error("Invalid relay host request.");
    Send(stream_,{{"version",1},{"role","host"}});
    const auto response=Parse(Line());
    if(!response.is_object()||response.size()!=3||!response.at("version").is_number_integer()||response.at("version")!=1)throw std::runtime_error("Invalid relay registration response.");
    CoopRelayRoute route{endpoint,response.at("room").get<std::string>(),response.at("join_token").get<std::string>()};
    if(!route.Valid())throw std::runtime_error("Invalid relay room credentials.");
    state_=State::Registered;return route;
  }catch(const RelayResponseError&){state_=State::Failed;throw;}
  catch(...){state_=State::Failed;throw std::runtime_error("Could not create an online room. Please try again.");}
}
void CoopRelaySession::PairResponse(){
  const auto response=Parse(Line());
  if(!response.is_object()||response.size()!=2||!response.at("version").is_number_integer()||response.at("version")!=1||
     !response.at("paired").is_boolean()||!response.at("paired").get<bool>())throw std::runtime_error("Relay refused pairing.");
  state_=State::Paired;
}
void CoopRelaySession::WaitPaired(){
  try{if(state_!=State::Registered)throw std::runtime_error("state");PairResponse();}
  catch(const RelayResponseError&){state_=State::Failed;throw;}
  catch(...){state_=State::Failed;throw std::runtime_error("The online room closed before your partner joined. Please try again.");}
}
void CoopRelaySession::Join(const CoopRelayRoute& route){
  try{
    if(state_!=State::Initial||!route.Valid()||!route.endpoint.starts_with("tls://"))throw std::runtime_error("state");
    Send(stream_,{{"version",1},{"role","peer"},{"room",route.room},{"join_token",route.join_token}});PairResponse();
  }catch(const RelayResponseError&){state_=State::Failed;throw;}
  catch(...){state_=State::Failed;throw std::runtime_error("Could not join this online game. Refresh the game list or ask the host for a new invitation.");}
}
void CoopRelaySession::Write(std::span<const uint8_t> bytes){
  if(state_!=State::Paired)throw std::runtime_error("Relay is not paired.");
  try{stream_.Write(bytes);}catch(...){state_=State::Failed;throw;}
}
bool CoopRelaySession::Read(std::vector<uint8_t>& bytes){
  if(state_!=State::Paired)throw std::runtime_error("Relay is not paired.");
  if(!pending_.empty()){bytes=std::move(pending_);pending_.clear();return true;}
  try{return stream_.Read(bytes);}catch(...){state_=State::Failed;throw;}
}
}
