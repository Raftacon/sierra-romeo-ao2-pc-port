"""Validate command-list submission results in the pinned spatial plugin."""
from pathlib import Path
import sys

# Runs after prepare_submission_fence, which checks the pinned header, and
# prepare_spatial_upscale, which checks the pinned command processor source.
source, output = map(Path, sys.argv[1:])
path = output / 'command_processor.cpp'
text = path.read_text()
start = text.index('    // Submit the deferred command list.\n')
end_anchor = '    queue_operations_done_since_submission_signal_ = false;\n'
end = text.index(end_anchor, start) + len(end_anchor)
baseline = text[start:end]
block = baseline


def replace(before, after):
    global block
    if block.count(before) != 1:
        raise ValueError('Expected one submission anchor: ' + before[:90])
    block = block.replace(before, after)


replace('    command_allocator->Reset();', '''    // A failed Close permanently invalidates this command list. If Signal
    // fails, commands may already be executing without a completion marker.
    // Neither failure permits replaying the submission or reclaiming its memory.
    const auto check_submission_result = [&](HRESULT result, const char* operation) {
      if (SUCCEEDED(result)) return;
      REXGPU_ERROR("Direct3D 12 {} failed for submission {} (HRESULT 0x{:08X})",
                   operation, submission_current_, uint32_t(result));
      const HRESULT removed_reason = provider.GetDevice()->GetDeviceRemovedReason();
      if (FAILED(removed_reason)) {
        LogDeviceRemovalDiagnostics(provider.GetDevice(), removed_reason);
      }
      rex::FlushLogging();
      rex::FatalError("Direct3D 12 command submission failed; see runtime log for details");
    };
    check_submission_result(command_allocator->Reset(), "command allocator Reset");''')
replace('    command_list_->Reset(command_allocator, nullptr);',
        '    check_submission_result(command_list_->Reset(command_allocator, nullptr), "command list Reset");')
replace('    command_list_->Close();',
        '    check_submission_result(command_list_->Close(), "command list Close");')
replace('    direct_queue->Signal(submission_fence_, submission_current_++);',
        '''    check_submission_result(direct_queue->Signal(submission_fence_, submission_current_),
                            "submission fence Signal");
    ++submission_current_;''')

for target, value in ((path, text[:start] + block + text[end:]),
                      (output / 'command_submission_previous.inc', baseline),
                      (output / 'command_submission_fixed.inc', block)):
    if not target.exists() or target.read_text() != value:
        target.write_text(value)
