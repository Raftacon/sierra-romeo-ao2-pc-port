#include "tutorial_text.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <rex/runtime.h>
#include <rex/logging.h>
#include <rex/filesystem/vfs.h>
#include <rex/filesystem/devices/host_path_device.h>
#ifdef _WIN32
#include <windows.h>
#endif

namespace aot {
using rex::X_STATUS;
void MountTutorialText(rex::Runtime* runtime, const std::filesystem::path& assets,
                       const std::filesystem::path& user_root) {
  try {
    const auto source_path = assets / "AO2Game/Localization/Coalesced.int";
    const auto size = std::filesystem::file_size(source_path);
    if (size > 4 * 1024 * 1024) throw std::runtime_error("Unexpected localization size");
    std::vector<uint8_t> source(static_cast<size_t>(size));
    std::ifstream input(source_path, std::ios::binary);
    if (!input.read(reinterpret_cast<char*>(source.data()), source.size()))
      throw std::runtime_error("Could not read localization");
    const auto patched = PrepareTutorialText(source);
    if (!patched) throw std::runtime_error("Unsupported localization structure");
    if (!patched->changed_lines) return;
    const auto directory = user_root / "pc-text";
    std::filesystem::create_directories(directory);
    // OpenFile resolves the parent directory, then calls GetChild(filename).
    // A file-only VFS alias is bypassed. Mirror the directory and preserve all
    // other language files so directory redirection does not hide them.
    for (const auto& entry : std::filesystem::directory_iterator(source_path.parent_path())) {
      if (!entry.is_regular_file()) throw std::runtime_error("Unsupported localization directory entry");
      if (entry.path().filename() != source_path.filename())
        std::filesystem::copy_file(entry.path(), directory / entry.path().filename(),
                                   std::filesystem::copy_options::overwrite_existing);
    }
    const auto file = directory / "Coalesced.int";
    const auto temporary = directory / "Coalesced.int.tmp";
    {
      std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
      output.write(reinterpret_cast<const char*>(patched->bytes.data()), patched->bytes.size());
      output.close();
      if (!output) throw std::runtime_error("Could not write PC localization");
    }
#ifdef _WIN32
    if (!MoveFileExW(temporary.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
      throw std::runtime_error("Could not install PC localization");
#else
    std::filesystem::rename(temporary, file);
#endif
    auto* fs = runtime->file_system();
    std::string game_device;
    if (!fs->FindSymbolicLink("game:", game_device)) throw std::runtime_error("No game mount");
    constexpr auto mount = "\\Device\\AotPcText";
    auto device = std::make_unique<rex::filesystem::HostPathDevice>(mount, directory, true);
    if (!device->Initialize()) throw std::runtime_error("Could not initialize PC text mount");
    fs->RegisterDevice(std::move(device));
    const auto link = game_device + "\\AO2Game\\Localization";
    fs->RegisterSymbolicLink(link, mount);
    rex::filesystem::File* opened = nullptr;
    rex::filesystem::FileAction action;
    const auto status = fs->OpenFile(nullptr, "game:\\AO2Game\\Localization\\Coalesced.int",
        rex::filesystem::FileDisposition::kOpen, rex::filesystem::FileAccess::kFileReadData,
        false, true, &opened, &action);
    std::unique_ptr<rex::filesystem::File> verified(opened);
    std::vector<uint8_t> readback(patched->bytes.size());
    size_t read = 0;
    const bool correct = status == X_STATUS_SUCCESS && verified &&
        verified->entry()->device()->mount_path() == mount &&
        verified->ReadSync(readback, 0, &read) == X_STATUS_SUCCESS &&
        read == readback.size() && readback == patched->bytes;
    verified.reset();
    if (!correct) {
      fs->UnregisterSymbolicLink(link);
      fs->UnregisterDevice(mount);
      throw std::runtime_error("PC localization redirection did not resolve");
    }
    REXLOG_INFO("PC tutorial wording: {} lines, verified VFS file read from {}",
                patched->changed_lines, mount);
  } catch (const std::exception& error) {
    REXLOG_WARN("PC tutorial wording unavailable: {}", error.what());
  }
}
}  // namespace aot
