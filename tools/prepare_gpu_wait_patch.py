"""Instrument pinned waits, with an opt-in callback wake in the spatial build.

The callback only requests a recheck; original conditions, barriers and shutdown
checks remain. With the experimental flag off, the original sleep is retained.
"""
import hashlib
from pathlib import Path
import sys

if len(sys.argv) not in (3,4) or (len(sys.argv)==4 and sys.argv[3]!='--trace-only'):
    raise ValueError('Usage: prepare_gpu_wait_patch.py SDK OUTPUT_CPP [--trace-only]')
trace_only=len(sys.argv)==4
source = Path(sys.argv[1]) / 'src/graphics/command_processor.cpp'
output = Path(sys.argv[2])
text = source.read_bytes().replace(b'\r\n', b'\n')
EXPECTED_HASH = '53a982be7550aee8aa3194eb0741d69b55af41ab2db65e611cdacec42a67a125'
if hashlib.sha256(text).hexdigest() != EXPECTED_HASH:
    raise ValueError('Unexpected SDK packet processor revision')
text = text.decode()

def replace(before, after):
    global text
    if text.count(before) != 1:
        raise ValueError('Packet wait patch anchor must be unique')
    text = text.replace(before, after)

replace('#include <algorithm>', '#include "src/gpu_wait_diagnostics.h"\n'+
        ('#include "src/gpu_vblank_wake.h"\n' if trace_only else '#include "src/gpu_packet_sleep.h"\n')+'#include <algorithm>')
if trace_only:
    replace('#include <algorithm>', '#include "src/occlusion_trace.h"\n#include <algorithm>')
    replace('''  bool result = false;
  switch (opcode) {''', '''#include "src/occlusion_packet_barrier.inc"
  bool result = false;
  switch (opcode) {''')
    replace('#include <algorithm>', '#include "src/gpu_packet_diagnostics.h"\n#include <algorithm>')
    replace('''  // & 1 == predicate - when set, we do bin check to see if we should execute
''', '''  const auto aot_copy_mode = register_file_->Get<reg::RB_MODECONTROL>().edram_mode;
  if (((opcode == PM4_DRAW_INDX || opcode == PM4_DRAW_INDX_2) &&
       aot_copy_mode == xenos::EdramMode::kCopy) ||
      ((packet & 1) && !(bin_select_ & bin_mask_) &&
       (opcode == PM4_INDIRECT_BUFFER || opcode == PM4_INDIRECT_BUFFER_PFD))) {
    aot::ObserveGpuCopyPacket(packet, bin_select_, bin_mask_, uint32_t(aot_copy_mode),
                             (*register_file_)[XE_GPU_REG_RB_COPY_DEST_BASE],
                             (*register_file_)[XE_GPU_REG_PA_SC_VIZ_QUERY]);
  }
  // & 1 == predicate - when set, we do bin check to see if we should execute
''')
    replace('''  bool matched = false;
  do {
    uint32_t value = 0;''', '''  bool matched = false;
  do {
    const auto vblank_sequence = aot::SnapshotGpuVblankWake();
    uint32_t value = 0;''')
replace('''  bool is_memory = (wait_info & 0x10) != 0;
''', '''  aot::GpuWaitDiagnostics wait_trace(wait_info, poll_reg_addr, ref, mask, wait);
  bool is_memory = (wait_info & 0x10) != 0;
''')
replace('''    if (!matched) {
      // Wait.''', '''    wait_trace.Poll(value, matched);
    if (!matched) {
      // Wait.''')
replace('''          rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));''', '''          const auto sleep_start = wait_trace.BeforeSleep();
          if (!is_memory || !aot::TryGpuVblankWake(vblank_sequence, wait / 0x100)) {
            rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));
          }
          wait_trace.AfterSleep(sleep_start, wait / 0x100);''' if trace_only else '''          const auto sleep_start = wait_trace.BeforeSleep();
          if (!aot::TryPreciseGpuPacketSleep(wait / 0x100)) {
            rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));
          }
          wait_trace.AfterSleep(sleep_start, wait / 0x100);''')
output.parent.mkdir(parents=True, exist_ok=True)
if not output.exists() or output.read_text() != text:
    output.write_text(text)

source = Path(sys.argv[1]) / 'src/graphics/graphics_system.cpp'
text = source.read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(text).hexdigest() != 'd834ab276411d69e6ec80e350fe6c7f64d6e4892f1eb0446ebff5e825d221042':
    raise ValueError('Unexpected SDK graphics system revision')
text = text.decode()
replace('#include <algorithm>', '#include "src/gpu_vblank_diagnostics.h"\n'+
        ('#include "src/gpu_vblank_wake.h"\n' if trace_only else '#include "src/gpu_vblank_sleep.h"\n')+'#include <algorithm>')
if not trace_only:
    replace('''          rex::thread::Sleep(std::chrono::milliseconds(1));
''', '''          if (!REXCVAR_GET(vsync) || !aot::TryPreciseVblankSleep(
                  last_frame_time, interval_ticks, guest_tick_frequency)) {
            rex::thread::Sleep(std::chrono::milliseconds(1));
          }
''')
replace('''void GraphicsSystem::MarkVblank() {
''', '''void GraphicsSystem::MarkVblank() {
  aot::GpuVblankDiagnostics vblank_trace(memory_->physical_membase());
''')
replace('''  DispatchInterruptCallback(0, 2);
''', '''  vblank_trace.BeforeCallback();
  DispatchInterruptCallback(0, 2);
  vblank_trace.AfterCallback();
''' + ('  aot::NotifyGpuVblankWake();\n' if trace_only else ''))
output = Path(sys.argv[2]).parent / 'graphics_system.cpp'
if not output.exists() or output.read_text() != text:
    output.write_text(text)
