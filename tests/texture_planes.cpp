// Exact SDK methods are compiled below. Only memory watching and GPU transfer
// are doubles; this test covers CPU acknowledgment, not GPU pixel correctness.
#include <atomic>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <utility>
#include <vector>

void require(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}
#define assert_not_zero(x) require((x) != 0, "zero texture extent")
#define REXLOG_WARN(...) ((void)0)
namespace rex {
template <typename T> T align(T value, T alignment) {
  return (value + alignment - 1) & ~(alignment - 1);
}
}
struct SharedMemory {
  std::vector<uint32_t> watched_pages;
  template <typename... T> void* WatchMemoryRange(uint32_t address, T...) {
    watched_pages.push_back(address >> 12);
    return reinterpret_cast<void*>(uintptr_t(watched_pages.size()));
  }
};
struct TextureCache {
  struct Region {
    std::recursive_mutex mutex;
    auto Acquire() { return std::unique_lock(mutex); }
  } global_critical_region_;
  struct TextureKey { uint32_t base_page = 1, mip_page = 4; bool scaled_resolve = false; };
  struct Texture {
    static constexpr uint32_t kOutdatedBitBase = 1, kOutdatedBitMips = 2;
    TextureCache& cache;
    TextureKey key_;
    bool base_outdated_ = true, mips_outdated_ = true;
    std::atomic<uint32_t> outdated_mask_{3};
    void* base_watch_handle_ = nullptr;
    void* mips_watch_handle_ = nullptr;
    explicit Texture(TextureCache& owner) : cache(owner) {}
    TextureCache& texture_cache() { return cache; }
    TextureKey key() const { return key_; }
    uint32_t GetGuestBaseSize() const { return 4096; }
    uint32_t GetGuestMipsSize() const { return 4096; }
    uint32_t outdated_mask() const { return outdated_mask_.load(); }
    bool base_outdated(const std::unique_lock<std::recursive_mutex>&) const { return base_outdated_; }
    bool mips_outdated(const std::unique_lock<std::recursive_mutex>&) const { return mips_outdated_; }
#ifdef AOT_TEXTURE_ORIGINAL
    void MakeUpToDateAndWatch(const std::unique_lock<std::recursive_mutex>&);
#else
    void MakeUpToDateAndWatch(const std::unique_lock<std::recursive_mutex>&, bool, bool);
#endif
    void WatchCallback(const std::unique_lock<std::recursive_mutex>&, bool);
    void LogAction(const char*) {}
  };
  struct PendingTextureLoad { Texture* texture = nullptr; bool load_base = false, load_mips = false; };
  struct PendingSharedMemoryRange { uint32_t start, length; };
  SharedMemory memory;
  bool upload_success = true, resident_success = true;
  std::vector<std::pair<bool, bool>> uploads;
  SharedMemory& shared_memory() { return memory; }
  static void WatchCallback() {}
  bool EnsureScaledResolveMemoryCommitted(uint32_t, uint32_t, uint32_t) { return resident_success; }
  bool LoadTextureDataFromResidentMemoryImpl(Texture&, bool base, bool mips) {
    uploads.emplace_back(base, mips);
    return upload_success;
  }
  bool PrepareTextureLoad(Texture&, PendingTextureLoad&, PendingSharedMemoryRange*, size_t&);
  bool CommitPreparedTextureLoad(const PendingTextureLoad&);
};

#ifdef AOT_TEXTURE_ORIGINAL
#include "texture_plane_original_methods.inc"
#else
#include "texture_plane_methods.inc"
#endif

int main() {
  try {
    for (bool initially_mips : {false, true}) {
      TextureCache cache;
      TextureCache::Texture texture(cache);
      TextureCache::PendingTextureLoad pending;
      TextureCache::PendingSharedMemoryRange ranges[2];
      size_t count = 0;
      require(cache.PrepareTextureLoad(texture, pending, ranges, count) && count == 2, "initial upload must select both planes");
      require(cache.CommitPreparedTextureLoad(pending) && texture.outdated_mask() == 0, "initial upload failed");
      cache.memory.watched_pages.clear();
      texture.WatchCallback(cache.global_critical_region_.Acquire(), initially_mips);
      require(cache.PrepareTextureLoad(texture, pending, ranges, count) && count == 1, "partial selection failed");
      // Deterministically invalidate the OTHER plane between prepare and commit.
      texture.WatchCallback(cache.global_critical_region_.Acquire(), !initially_mips);
      require(cache.CommitPreparedTextureLoad(pending), "partial upload failed");
      require(cache.uploads.back() == std::pair{!initially_mips, initially_mips}, "unexpected upload planes");
      const auto remaining = initially_mips ? 1u : 2u;
#ifdef AOT_TEXTURE_ORIGINAL
      require(texture.outdated_mask() == 0 && cache.memory.watched_pages.size() == 2,
              "original negative control no longer reproduces lost invalidation");
#else
      require(texture.outdated_mask() == remaining, "unuploaded plane incorrectly acknowledged");
      require(cache.memory.watched_pages.size() == 1, "unuploaded plane incorrectly watched");
      require(cache.PrepareTextureLoad(texture, pending, ranges, count) && count == 1,
              "next upload must select retained dirty plane");
      require(cache.CommitPreparedTextureLoad(pending) && texture.outdated_mask() == 0,
              "retained plane did not recover on next upload");
      require(!cache.PrepareTextureLoad(texture, pending, ranges, count), "clean texture reloaded");
#endif
    }
    TextureCache cache;
    TextureCache::Texture texture(cache);
    TextureCache::PendingTextureLoad pending{&texture, true, true};
    cache.upload_success = false;
    require(!cache.CommitPreparedTextureLoad(pending), "failed upload accepted");
    require(texture.outdated_mask() == 3 && cache.memory.watched_pages.empty(), "failed upload acknowledged");
    cache.upload_success = true;
    texture.key_.scaled_resolve = true;
    cache.resident_success = false;
    require(!cache.CommitPreparedTextureLoad(pending) && texture.outdated_mask() == 3,
            "failed residency acknowledged");
    require(cache.CommitPreparedTextureLoad({}), "empty pending upload should be harmless");
#ifdef AOT_TEXTURE_ORIGINAL
    std::cout << "Original SDK loses both late-plane invalidation interleavings\n";
#else
    std::cout << "Both late-plane invalidations retained; retry and failure paths pass\n";
#endif
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
