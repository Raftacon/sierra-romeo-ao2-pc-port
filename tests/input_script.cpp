#include "../src/input_script.h"
#include <iostream>

void Check(bool value, const char* reason) {
  if (!value) throw std::runtime_error(reason);
}
int main() {
  const auto path = std::filesystem::temp_directory_path() /
      ("aot-input-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  try {
    auto write = [&](const std::string& text) { std::ofstream(path) << text; };
    write("# test\n10 20 1000 255 0 -32768 32767 0 1\n30 5 2000 0 255 0 0 1 0\n");
    const aot::InputScript script(path.string());
    Check(script.Sample(9).buttons == 0, "Input started early");
    Check(script.Sample(10).buttons == 0x1000 && script.Sample(29).lt == 255,
          "Wrong event interval");
    Check(script.Sample(10).lx == -32768 && script.Sample(10).ly == 32767,
          "Stick boundary conversion");
    Check(script.Sample(30).buttons == 0x2000 && script.Sample(34).rt == 255,
          "Adjacent events overlap");
    Check(script.Sample(35).buttons == 0 && script.Sample(UINT64_MAX).buttons == 0,
          "Input did not release");
    for (const auto* bad : {
        "0 0 0 0 0 0 0 0 0", "-1 20 0 0 0 0 0 0 0",
        "0 -1 0 0 0 0 0 0 0", "0 10 10000 0 0 0 0 0 0",
        "0 10 0 -1 0 0 0 0 0", "0 10 0 0 256 0 0 0 0",
        "0 10 0 0 0 -32769 0 0 0", "0 10 0 0 0 0 32768 0 0",
        "0 10 0 0 0 0 0 0 0 extra", "0 10 0 0 0 0 0 0",
        "0 20 0 0 0 0 0 0 0\n19 1 0 0 0 0 0 0 0"}) {
      write(bad);
      bool rejected = false;
      try { aot::InputScript invalid(path.string()); }
      catch (const std::runtime_error&) { rejected = true; }
      Check(rejected, "Malformed replay accepted");
    }
    write("1000 255 0 0 0 0 0");
    Check(aot::ReadLivePad(path.string()).buttons == 0x1000, "Live input missing");
    std::filesystem::last_write_time(path,
        std::filesystem::file_time_type::clock::now() - std::chrono::seconds(5));
    Check(aot::ReadLivePad(path.string()).buttons == 0, "Stale live input stuck");
    write("corrupt");
    Check(aot::ReadLivePad(path.string()).buttons == 0, "Corrupt live input accepted");
    std::filesystem::remove(path);
    Check(aot::ReadLivePad(path.string()).buttons == 0, "Missing live input accepted");
    std::cout << "Replay intervals, value bounds, and live input expiry passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::error_code ignored;
    std::filesystem::remove(path, ignored);
    std::cerr << error.what() << '\n';
    return 1;
  }
}
