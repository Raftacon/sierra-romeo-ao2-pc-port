"""Propagate fence failures without advancing completion or reclaiming resources."""
import hashlib
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
path = output / 'command_processor.cpp'
text = path.read_text()
header_path = output / 'include/rex/graphics/d3d12/command_processor.h'
header = header_path.read_text()
sdk_header = (source / 'include/rex/graphics/d3d12/command_processor.h').read_bytes().replace(b'\r\n', b'\n')
if hashlib.sha256(sdk_header).hexdigest() != '646460362f1b15e3a1309b669d799b582fe7bf57e050fabc2c5be65ec516bcb2':
    raise ValueError('Unexpected pinned command processor header')


def replace(code, before, after):
    if code.count(before) != 1:
        raise ValueError('Expected one submission-fence anchor: ' + before[:90])
    return code.replace(before, after)


def write(path, code):
    if not path.exists() or path.read_text() != code:
        path.write_text(code)


start = text.index('void D3D12CommandProcessor::CheckSubmissionFence(')
end = text.index('void D3D12CommandProcessor::LogDeviceRemovalDiagnostics(', start)
baseline = text[start:end]
method = replace(baseline, 'void D3D12CommandProcessor::CheckSubmissionFence(uint64_t await_submission) {',
                 'bool D3D12CommandProcessor::CheckSubmissionFence(uint64_t await_submission) {\n  if (device_removed_) return false;')
method = replace(method, '''    if (submission_open_) {
      EndSubmission(false);
    }''', '''    if (submission_open_ && !EndSubmission(false)) {
      return false;
    }''')
method = replace(method, '''        WaitForSingleObject(fence_completion_event_, INFINITE);
        queue_operations_done_since_submission_signal_ = false;''', '''        if (WaitForSingleObject(fence_completion_event_, INFINITE) != WAIT_OBJECT_0) {
          REXGPU_ERROR("Failed to wait for the out-of-submission queue fence event");
          return false;
        }
        const uint64_t queue_completed = queue_operations_since_submission_fence_->GetCompletedValue();
        if (queue_completed == UINT64_MAX || queue_completed < fence_value) {
          REXGPU_ERROR("Out-of-submission queue fence did not report valid completion");
          return false;
        }
        queue_operations_done_since_submission_signal_ = false;''')
method = replace(method, '''            "Direct3D 12 fence");
      }''', '''            "Direct3D 12 fence");
        return false;
      }''')
method = replace(method, '''    // A submission won't be ended if it hasn't been started, or if ending
    // has failed - clamp the index.''', '''    // No submission is ended if none was open. Await the last submitted index.''')
a = method.index('  uint64_t submission_completed_before = submission_completed_;')
b = method.index('  // Reclaim command allocators.', a)
method = method[:a] + '''  const uint64_t submission_completed_before = submission_completed_;
  uint64_t completed = submission_fence_->GetCompletedValue();
  if (completed == UINT64_MAX) {
    REXGPU_ERROR("Submission fence reports device removal; completion is unchanged");
    return false;
  }
  if (completed < await_submission) {
    if (FAILED(submission_fence_->SetEventOnCompletion(await_submission, fence_completion_event_))) {
      REXGPU_ERROR("Failed to register the submission fence event");
      return false;
    }
    PROFILE_CMD_BUFFER_STALL();
    if (WaitForSingleObject(fence_completion_event_, INFINITE) != WAIT_OBJECT_0) {
      REXGPU_ERROR("Failed to wait for the submission fence event");
      return false;
    }
    completed = submission_fence_->GetCompletedValue();
  }
  if (completed == UINT64_MAX || completed < await_submission) {
    REXGPU_ERROR("Submission fence did not report valid completion after the wait");
    return false;
  }
  if (completed <= submission_completed_before) {
    return true;
  }
  submission_completed_ = completed;

''' + method[b:]
method = replace(method, '  texture_cache_->CompletedSubmissionUpdated(submission_completed_);\n}',
                 '  texture_cache_->CompletedSubmissionUpdated(submission_completed_);\n  return true;\n}')
text = text[:start] + method + text[end:]
text = replace(text, '  CheckSubmissionFence(0);\n  bool previous_slot_ready',
               '  if (!CheckSubmissionFence(0)) return false;\n  bool previous_slot_ready')
text = replace(text, '''  CheckSubmissionFence(is_opening_frame ? closed_frame_submissions_[frame_current_ % kQueueFrames]
                                        : 0);
  // TODO(Triang3l): If failed to await (completed submission < awaited frame
  // submission), do something like dropping the draw command that wanted to
  // open the frame.''', '''  if (!CheckSubmissionFence(is_opening_frame ? closed_frame_submissions_[frame_current_ % kQueueFrames]
                                             : 0)) {
    return false;
  }''')
text = replace(text, '''  CheckSubmissionFence(query_submission);
  if (submission_completed_ < query_submission) {''', '''  if (!CheckSubmissionFence(query_submission)) {''')
header = replace(header, '  void CheckSubmissionFence(uint64_t await_submission);',
                 '  bool CheckSubmissionFence(uint64_t await_submission);')
old_await = '''  bool AwaitAllQueueOperationsCompletion() {
    CheckSubmissionFence(submission_current_);
    return submission_completed_ + 1 >= submission_current_;
  }'''
new_await = '''  bool AwaitAllQueueOperationsCompletion() {
    return CheckSubmissionFence(submission_current_);
  }'''
header = replace(header, old_await, new_await)
write(path, text)
write(header_path, header)
write(output / 'submission_fence_fixed.inc', method)
write(output / 'submission_fence_previous.inc', baseline)
write(output / 'submission_await_fixed.inc', new_await)
write(output / 'submission_await_previous.inc', old_await)
# Keep the existing branch and real-queue tests on the final production block.
a = method.index('    if (queue_operations_done_since_submission_signal_) {')
b = method.index('    // No submission is ended', a)
write(output / 'queue_wait_fixed.inc', method[a:b])
