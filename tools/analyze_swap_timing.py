"""Attribute CPU wall time within IssueSwap in a completed quiet observation.

These spans include CPU work and waits for other threads/GPU work. They are not
GPU execution costs, and the trace does not include the preceding scene draws.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics

MARKS = ('prepared_ms', 'callback_ms', 'commands_ms', 'submitted_ms',
         'spatial_ms', 'published_ms', 'finished_ms')
PHASES = ('image_preparation', 'refresh_acquire', 'output_commands',
          'guest_submission', 'spatial_draw', 'refresh_publish', 'final_submission', 'scope_exit')
SUB_MARKS = ('allocator_ms', 'end_frame_ms', 'pipelines_ms', 'barriers_ms',
             'reset_ms', 'deferred_ms', 'closed_ms', 'queue_ms', 'signal_ms', 'retired_ms')
SUB_PHASES = ('allocator_setup', 'frame_cache_cleanup', 'pipeline_completion',
              'barrier_recording', 'command_reset', 'deferred_commands', 'command_close',
              'queue_execute', 'queue_signal_and_allocator_rotation', 'frame_retirement', 'return')


def summary(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('Empty timing selection')
    return dict(samples=len(ordered), mean_ms=statistics.mean(ordered),
                p95_ms=ordered[int(.95 * (len(ordered) - 1))],
                p99_ms=ordered[int(.99 * (len(ordered) - 1))], maximum_ms=ordered[-1])


def parse_rows(path):
    rows, previous_end = [], -1
    with path.open(newline='') as stream:
        for raw in csv.DictReader(stream):
            row = {key: float(value) for key, value in raw.items()}
            if any(not math.isfinite(value) for value in row.values()):
                raise ValueError('Nonfinite swap timestamp')
            start, end = row['start_clock_ms'], row['end_clock_ms']
            if (start < previous_end or end < start or row['presented'] not in (0, 1)
                    or row['gpu_frame'] < 0 or not row['gpu_frame'].is_integer()):
                raise ValueError('Overlapping/reversed swap or invalid completion flag')
            previous_end = end
            if row['presented']:
                stamps = [start, *(row[key] for key in MARKS), end]
                if any(b < a for a, b in zip(stamps, stamps[1:])):
                    raise ValueError('Incomplete or reversed successful swap stages')
                row['phases'] = dict(zip(PHASES, (b - a for a, b in zip(stamps, stamps[1:]))))
                row['total_ms'] = end - start
                if any(key in row for key in SUB_MARKS):
                    sub_stamps = [row['commands_ms'], *(row[key] for key in SUB_MARKS), row['submitted_ms']]
                    if any(b < a for a, b in zip(sub_stamps, sub_stamps[1:])):
                        raise ValueError('Incomplete or reversed submission stages')
                    row['submission_phases'] = dict(zip(SUB_PHASES, (b - a for a, b in zip(sub_stamps, sub_stamps[1:]))))
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--visit', type=int, default=0, choices=range(12))
    args = parser.parse_args()
    p = args.probe
    read = lambda name: json.loads((p / name).read_text())
    native, session = read('probe.json'), read('session-pacing.json')
    prefix = 'visit-%02d' % args.visit
    timing, window = read(prefix + '-analysis.json'), read(prefix + '-window.json')
    if (not session['complete'] or native['timed_out'] or native['exit_code_before_cleanup'] != 0
            or not timing['complete'] or not timing['foreground_verified']
            or not session['source_profile_unchanged'] or not session['retail_checkpoints_unchanged']):
        raise ValueError('Require a completed, focused, source-preserving quiet run')
    rows = parse_rows(p / 'swap-timing.csv')
    start, end = window['steady_clock_start_ms'], window['steady_clock_end_ms']
    selected = [row for row in rows if start <= row['start_clock_ms'] and row['end_clock_ms'] <= end]
    if (not selected or end - start < 75000 or selected[0]['start_clock_ms'] - start > 1000
            or end - selected[-1]['end_clock_ms'] > 1000):
        raise ValueError('Incomplete swap coverage of quiet window')
    if any(not row['presented'] for row in selected):
        raise ValueError('Unsuccessful swap inside quiet window')
    frames = [row['gpu_frame'] for row in selected]
    if any(b != a + 1 for a, b in zip(frames, frames[1:])):
        raise ValueError('Missing or repeated GPU frame in quiet swap trace')
    phase_summary = {name: summary([row['phases'][name] for row in selected]) for name in PHASES}
    sub_summary = ({name: summary([row['submission_phases'][name] for row in selected]) for name in SUB_PHASES}
                   if any('submission_phases' in row for row in selected) else None)
    result = dict(complete=True, gpu_frames=len(selected), game_frames=timing['frames'],
                  game_mean_ms=timing['mean_ms'], swap_total=summary([row['total_ms'] for row in selected]),
                  phases=phase_summary, submission_phases=sub_summary, maximum_recorded_frame_gap_ms=max(
                      b['start_clock_ms'] - a['start_clock_ms'] for a, b in zip(selected, selected[1:])),
                  slowest_swaps=sorted(selected, key=lambda row: row['total_ms'], reverse=True)[:12],
                  executable_sha256=native['executable_sha256'], gpu_plugin=native['gpu_plugin'], limits=__doc__)
    (p / (prefix + '-swap-analysis.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'slowest_swaps'}, indent=2))


if __name__ == '__main__':
    main()
