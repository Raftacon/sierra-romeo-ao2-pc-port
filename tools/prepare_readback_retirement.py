"""Keep evicted readback resources alive until their queue submission completes."""
import hashlib
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
sdk_path = source / 'src/graphics/d3d12/command_processor.cpp'
sdk = sdk_path.read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(sdk).hexdigest() != 'aeb26127de7b9dd0b797d6430187540e9b7ef970f2171e80a1819165318bdc58':
    raise ValueError('Unexpected pinned readback implementation')
path = output / 'command_processor.cpp'
text = path.read_text()


def replace(code, before, after):
    if code.count(before) != 1:
        raise ValueError('Expected one readback-retirement anchor: ' + before[:90])
    return code.replace(before, after)


def write(path, content):
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


start = text.index('void D3D12CommandProcessor::EvictOldReadbackBuffers(')
end = text.index('ID3D12Resource* D3D12CommandProcessor::RequestReadbackBuffer(', start)
previous = text[start:end]
fixed = replace(previous, '        readback.buffers[i]->Release();', '''        // Transfer the cache's owning reference. Capacity eviction can select
        // a buffer written by a still-pending submission; Unmap does not end
        // that GPU use. Reuse the existing fence-governed deletion queue.
        resources_for_deletion_.emplace_back(GetCurrentSubmission(), readback.buffers[i]);''')
fixed = replace(fixed, '    for (uint32_t i = 0; i < 2; ++i) {', '''    static const bool trace_retirement = std::getenv("AOT_TRACE_READBACK_RETIREMENT") != nullptr;
    static uint32_t trace_count = 0;
    if (trace_retirement && trace_count < 65536) {
      ++trace_count;
      const uint64_t completed_now = submission_fence_->GetCompletedValue();
      REXGPU_INFO("Readback retirement: frame={}, key={}, count={}, last_frame={}, written0={}, written1={}, completed={}, retire={}, bytes0={}, bytes1={}, limit={}",
          frame_current_, it->first, buffer_map.size(), readback.last_used_frame,
          readback.submission_written[0], readback.submission_written[1], completed_now,
          GetCurrentSubmission(), readback.sizes[0], readback.sizes[1], trace_count == 65536);
    }
    for (uint32_t i = 0; i < 2; ++i) {''')
text = text[:start] + fixed + text[end:]
# Resolve slots did not record their writer (memexport slots already do).
# This is metadata only: no change to CPU read selection, waiting, or copies.
text = replace(text, '''  ReadbackResolveMode readback_mode = GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve));
  bool use_delayed_sync =''', '''  rb.submission_written[write_index] = submission_current_;
  ReadbackResolveMode readback_mode = GetReadbackResolveMode(REXCVAR_GET(d3d12_readback_resolve));
  bool use_delayed_sync =''')
text = replace(text, '#include <algorithm>', '#include <algorithm>\n#include <cstdlib>')
# Tests compile the full eviction method and the real reclamation block.
a = text.index('  while (!resources_for_deletion_.empty()) {')
b = text.index('\n  }', a) + len('\n  }')
write(output / 'readback_retirement_drain.inc', text[a:b])
write(output / 'readback_retirement_fixed.inc', fixed)
write(output / 'readback_retirement_previous.inc', previous)
header = (source / 'include/rex/graphics/d3d12/command_processor.h').read_text()
a = header.index('  struct ReadbackBuffer {')
b = header.index('  static inline uint32_t AlignReadbackBufferSize', a)
write(output / 'readback_retirement_members.inc', header[a:b])
write(path, text)
