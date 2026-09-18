#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winhttp.h>
#include "coop_directory_client.h"
#include "coop_connection.h"
#include "vendor/nlohmann/json.hpp"
#include <array>
#include <chrono>
#include <stdexcept>

namespace aot {
namespace {
using Json=nlohmann::json;
struct Handle {HINTERNET value=nullptr;~Handle(){if(value)WinHttpCloseHandle(value);}operator HINTERNET()const{return value;}};
bool Token(std::string_view s,size_t length,std::string_view allowed){return s.size()==length && s.find_first_not_of(allowed)==s.npos;}
bool Lease(const CoopDirectoryLease& lease){return Token(lease.id,32,"0123456789abcdef")&&Token(lease.token,43,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-");}
Json Parse(const std::string& text){
  return Json::parse(text,[](int depth,Json::parse_event_t,Json&){if(depth>8)throw std::runtime_error("JSON nesting");return true;});
}
std::string Settings(const CoopDiscoveredRoom& room,bool joinable){
  if(room.visibility!=CoopVisibility::Public || room.difficulty>2 || !room.port || room.name.empty() || room.name.size()>32 ||
     room.name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _.-")!=room.name.npos)
    throw std::runtime_error("Invalid public room");
  Json settings={{"name",room.name},{"map",CoopDiscoveryMap(room.map)},{"difficulty",room.difficulty},
              {"port",room.port},{"protocol",11},{"players",1},{"joinable",joinable}};
  if(room.relay){if(!room.relay->Valid())throw std::runtime_error("relay");settings["relay"]={{"room",room.relay->room},{"join_token",room.relay->join_token}};}
  return settings.dump();
}
}
CoopDirectoryClient::CoopDirectoryClient(std::wstring endpoint):endpoint_(std::move(endpoint)){}
bool CoopDirectoryClient::Request(const wchar_t* method,const std::string& path,const std::string& body,const std::string& token,std::string& response){
  error_.clear();response.clear();
  try{
    URL_COMPONENTS parts{};parts.dwStructSize=sizeof(parts);parts.dwHostNameLength=parts.dwUrlPathLength=parts.dwExtraInfoLength=parts.dwUserNameLength=parts.dwPasswordLength=DWORD(-1);
    if(endpoint_.size()>2048||!WinHttpCrackUrl(endpoint_.c_str(),DWORD(endpoint_.size()),0,&parts))throw std::runtime_error("Invalid directory address.");
    const std::wstring host(parts.lpszHostName,parts.dwHostNameLength);
    const std::wstring base_path(parts.lpszUrlPath?parts.lpszUrlPath:L"",parts.dwUrlPathLength);
    const bool secure=parts.nScheme==INTERNET_SCHEME_HTTPS;
    if(parts.dwUserNameLength||parts.dwPasswordLength||parts.dwExtraInfoLength||(!base_path.empty()&&base_path!=L"/")||
       (!secure && (parts.nScheme!=INTERNET_SCHEME_HTTP || (host!=L"127.0.0.1"&&host!=L"localhost"))))
      throw std::runtime_error("Directory requires HTTPS, or loopback HTTP for local testing.");
    Handle session{WinHttpOpen(L"ArmyOfTwoPC-Directory/1",WINHTTP_ACCESS_TYPE_NO_PROXY,WINHTTP_NO_PROXY_NAME,WINHTTP_NO_PROXY_BYPASS,0)};
    if(!session.value||!WinHttpSetTimeouts(session,2000,2000,2000,2000))throw std::runtime_error("Directory connection could not initialize.");
    Handle connection{WinHttpConnect(session,host.c_str(),parts.nPort,0)};
    const std::wstring target(path.begin(),path.end());
    Handle request{connection.value?WinHttpOpenRequest(connection,method,target.c_str(),nullptr,WINHTTP_NO_REFERER,WINHTTP_DEFAULT_ACCEPT_TYPES,secure?WINHTTP_FLAG_SECURE:0):nullptr};
    DWORD disabled=WINHTTP_DISABLE_REDIRECTS|WINHTTP_DISABLE_COOKIES;
    DWORD auto_logon=WINHTTP_AUTOLOGON_SECURITY_LEVEL_HIGH;
    if(!request.value||!WinHttpSetOption(request,WINHTTP_OPTION_DISABLE_FEATURE,&disabled,sizeof(disabled))||
       !WinHttpSetOption(request,WINHTTP_OPTION_AUTOLOGON_POLICY,&auto_logon,sizeof(auto_logon)))throw std::runtime_error("Directory request could not initialize.");
    std::wstring headers=L"Content-Type: application/json\r\n";
    if(!token.empty())headers+=L"Authorization: Bearer "+std::wstring(token.begin(),token.end())+L"\r\n";
    if(!WinHttpSendRequest(request,headers.c_str(),DWORD(headers.size()),body.empty()?WINHTTP_NO_REQUEST_DATA:const_cast<char*>(body.data()),DWORD(body.size()),DWORD(body.size()),0)||
       !WinHttpReceiveResponse(request,nullptr))throw std::runtime_error("Directory unavailable. Try again or join by address.");
    DWORD status=0,size=sizeof(status);
    if(!WinHttpQueryHeaders(request,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,WINHTTP_HEADER_NAME_BY_INDEX,&status,&size,WINHTTP_NO_HEADER_INDEX))throw std::runtime_error("Directory returned an invalid response.");
    if(status<200||status>=300)throw std::runtime_error("Directory request failed (HTTP "+std::to_string(status)+").");
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    std::array<char,4096> buffer{};
    for(;;){
      DWORD received=0;
      if(std::chrono::steady_clock::now()>deadline||!WinHttpReadData(request,buffer.data(),DWORD(buffer.size()),&received))throw std::runtime_error("Directory response timed out.");
      if(!received)break;
      if(response.size()+received>131072)throw std::runtime_error("Directory response exceeds the room limit.");
      response.append(buffer.data(),received);
    }
    return true;
  }catch(const std::exception& e){error_=e.what();response.clear();return false;}
}
bool CoopDirectoryClient::Create(const CoopDiscoveredRoom& room,CoopDirectoryLease& lease){
  try{
    std::string response;if(!Request(L"POST","/v1/rooms",Settings(room,true),{},response))return false;
    const auto result=Parse(response);CoopDirectoryLease candidate{result.at("id").get<std::string>(),result.at("lease").get<std::string>()};
    if(!Lease(candidate))throw std::runtime_error("lease");lease=std::move(candidate);return true;
  }catch(...){error_="Directory returned invalid room registration data.";return false;}
}
bool CoopDirectoryClient::Update(const CoopDiscoveredRoom& room,const CoopDirectoryLease& lease,bool joinable){
  try{if(!Lease(lease))throw std::runtime_error("lease");std::string response;return Request(L"PUT","/v1/rooms/"+lease.id,Settings(room,joinable),lease.token,response);}
  catch(...){error_="Invalid directory room or lease.";return false;}
}
bool CoopDirectoryClient::Remove(const CoopDirectoryLease& lease){
  if(!Lease(lease)){error_="Invalid directory lease.";return false;}std::string response;return Request(L"DELETE","/v1/rooms/"+lease.id,{},lease.token,response);
}
bool CoopDirectoryClient::Search(std::vector<CoopDiscoveredRoom>& rooms){
  rooms.clear();std::string response;if(!Request(L"GET","/v1/rooms?protocol=11&relay=1",{},{},response))return false;
  try{
    const auto result=Parse(response);const auto& entries=result.at("rooms");
    if(!entries.is_array()||entries.size()>256)throw std::runtime_error("rooms");
    std::vector<CoopDiscoveredRoom> candidates;
    for(const auto& item:entries){
      if(!item.at("port").is_number_unsigned()||!item.at("difficulty").is_number_unsigned()||
         item.at("port").get<uint64_t>()>65535||item.at("difficulty").get<uint64_t>()>2||
         item.at("protocol")!=11||item.at("players")!=1||item.at("joinable")!=true)throw std::runtime_error("settings");
      CoopDiscoveredRoom room{item.at("address").get<std::string>(),item.at("name").get<std::string>(),item.at("map").get<std::string>(),
                             item.at("port").get<uint16_t>(),item.at("difficulty").get<uint8_t>(),CoopVisibility::Public};
      CoopConnection endpoint;endpoint.join=true;endpoint.address=room.address;endpoint.port=std::to_string(room.port);endpoint.name=room.name;
      if(!endpoint.Error(false).empty()||room.map.size()>64||room.map.empty()||room.map.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")!=room.map.npos)throw std::runtime_error("endpoint");
      if(item.contains("relay")){
        const auto& relay=item.at("relay");
        room.relay=CoopRelayRoute{relay.at("endpoint").get<std::string>(),relay.at("room").get<std::string>(),relay.at("join_token").get<std::string>()};
        if(!room.relay->Valid())throw std::runtime_error("relay");
      }
      candidates.push_back(std::move(room));
    }
    rooms=std::move(candidates);return true;
  }catch(...){error_="Directory returned an invalid game list.";return false;}
}
}
