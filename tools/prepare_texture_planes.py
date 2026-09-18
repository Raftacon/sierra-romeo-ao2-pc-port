"""Keep unuploaded texture planes dirty in the pinned SDK's batched upload path."""
from pathlib import Path
import hashlib
import sys

source, output = map(Path, sys.argv[1:])

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)

def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Expected one texture patch anchor: ' + before[:80])
    return text.replace(before, after)

original = (source / 'src/graphics/pipeline/texture/cache.cpp').read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(original).hexdigest() != 'd365a4d8d7f2361244ddd6db02984e1a003cf323b40cc90c63c36af7a1d7d26e':
    raise ValueError('Unexpected pinned texture-cache source')
text = original.decode()
text = replace(text, '  texture.MakeUpToDateAndWatch(global_critical_region_.Acquire());', '''  auto global_lock = global_critical_region_.Acquire();
  const bool late_base = texture.base_outdated(global_lock) && !pending_load.load_base;
  const bool late_mips = texture.mips_outdated(global_lock) && !pending_load.load_mips;
  if (late_base || late_mips) {
    static thread_local uint64_t late_count = 0;
    if (++late_count <= 16) {
      REXLOG_WARN("AOT texture-plane invalidation retained: base={}, mips={}, count={}",
                  late_base, late_mips, late_count);
    }
  }
  texture.MakeUpToDateAndWatch(global_lock, pending_load.load_base, pending_load.load_mips);''')
signature = '    const std::unique_lock<std::recursive_mutex>& global_lock) {\n  SharedMemory& shared_memory = texture_cache().shared_memory();'
text = replace(text, signature, '    const std::unique_lock<std::recursive_mutex>& global_lock, bool load_base, bool load_mips) {\n  SharedMemory& shared_memory = texture_cache().shared_memory();')
start = text.index('void TextureCache::Texture::MakeUpToDateAndWatch(')
end = text.index('void TextureCache::Texture::MarkAsUsed()', start)
section = text[start:end].replace('if (base_outdated_)', 'if (load_base && base_outdated_)').replace('if (mips_outdated_)', 'if (load_mips && mips_outdated_)')
text = text[:start] + section + text[end:]
write(output / 'texture_cache.cpp', text)
header_bytes = (source / 'include/rex/graphics/pipeline/texture/cache.h').read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(header_bytes).hexdigest() != 'b1d7a9019f1896498372d15827bd8d09a09eb150b54e554ae98678e465394a89':
    raise ValueError('Unexpected pinned texture-cache header')
header = header_bytes.decode()
header = replace(header,
    '    void MakeUpToDateAndWatch(const std::unique_lock<std::recursive_mutex>& global_lock);',
    '    void MakeUpToDateAndWatch(const std::unique_lock<std::recursive_mutex>& global_lock, bool load_base, bool load_mips);')
write(output / 'include/rex/graphics/pipeline/texture/cache.h', header)

# Compile the exact production bookkeeping methods against test doubles for
# shared memory and GPU upload. The original methods are retained for the
# negative control, not reimplemented in the regression test.
def methods(code):
    sections = []
    for begin, end in [
        ('bool TextureCache::PrepareTextureLoad(', 'void TextureCache::RequestTextures('),
        ('void TextureCache::Texture::MakeUpToDateAndWatch(', 'void TextureCache::Texture::MarkAsUsed('),
        ('void TextureCache::Texture::WatchCallback(', 'void TextureCache::WatchCallback('),
    ]:
        a = code.index(begin)
        sections.append(code[a:code.index(end, a)])
    return '\n'.join(sections)
write(output / 'texture_plane_methods.inc', methods(text))
write(output / 'texture_plane_original_methods.inc', methods(original.decode()))
