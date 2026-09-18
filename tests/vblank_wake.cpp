#include "src/vblank_wake_event.h"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

int main() {
  using namespace std::chrono;
  aot::VblankWakeEvent wake;
  bool ok=true;
  // Callback arrives between the condition snapshot and entering the wait.
  auto observed=wake.Snapshot();wake.Signal();
  auto start=steady_clock::now();
  ok &= wake.Wait(observed,2000) && steady_clock::now()-start<500ms;
  // No callback: retain a bounded retry instead of depending on notifications.
  start=steady_clock::now();
  ok &= wake.Wait(wake.Snapshot(),5) && steady_clock::now()-start<500ms;
  // Unrelated callbacks must not count as completed guest work. Exercise
  // multiple waiters and both notification-before-wait and in-wait races.
  for (unsigned run=0;run<32;++run) {
    std::atomic<unsigned> ready=0,done=0;
    std::atomic<bool> condition=false,timed_out=false;
    std::thread consumers[4];
    for (auto& consumer:consumers) consumer=std::thread([&] {
      ++ready;
      const auto deadline=steady_clock::now()+2s;
      for (;;) {
        const auto sequence=wake.Snapshot();
        if (condition.load()) {++done;break;}
        if (steady_clock::now()>deadline || !wake.Wait(sequence,20)) {timed_out=true;break;}
      }
    });
    while (ready.load()!=4) std::this_thread::yield();
    wake.Signal();std::this_thread::sleep_for(2ms);
    ok &= done.load()==0;
    condition=true;wake.Signal();
    for (auto& consumer:consumers) consumer.join();
    ok &= done.load()==4 && !timed_out.load();
  }
  if (!ok) {std::cerr<<"VBlank notification/condition contract failed\n";return 1;}
  std::cout<<"Preserved condition checks across callback races and timeout fallback\n";
}
