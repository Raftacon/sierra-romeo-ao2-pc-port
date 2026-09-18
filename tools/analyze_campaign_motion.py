"""Summarize a campaign-motion trace without treating travel as campaign parity."""
import argparse
import collections
import csv
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--start', type=float, default=100)
    parser.add_argument('--end', type=float, default=190)
    args = parser.parse_args()
    p = args.probe.resolve()
    subprocess.run([sys.executable, str(Path(__file__).with_name('analyze_frame_phases.py')),
                    str(p), '--start', str(args.start), '--end', str(args.end)],
                   check=True, stdout=subprocess.DEVNULL)
    phase = json.loads((p / 'phase-analysis.json').read_text())
    scenario = json.loads((p / 'motion-scenario.json').read_text())
    native = json.loads((p / 'probe.json').read_text())
    travel = json.loads((p / 'travel.json').read_text())
    elapsed, values = 0, []
    for row in csv.DictReader((p / 'frame-times.csv').open()):
        ms = float(row['interval_ms'])
        elapsed += ms / 1000
        if args.start <= elapsed < args.end:
            values.append(ms)
    if not values or elapsed < args.end:
        raise ValueError('Native trace does not cover the requested window')
    slow = phase['slow_frames']
    chains = collections.Counter(tuple(w.get(k) for k in
        ('api', 'max_caller', 'parent1', 'parent2', 'parent3', 'parent4'))
        for frame in slow for w in frame['runtime_waits'])
    origins = re.findall(r'Wait diagnostic steady-clock origin_ms=([\d.]+)',
                         (p / 'runtime.log').read_text(errors='replace'))
    if 'steady_clock_origin_ms' in scenario and len(origins) == 1:
        offset = float(origins[0]) - scenario['steady_clock_origin_ms']
        bounds = {int(r['frame']): (float(r['end_ms']) + offset) / 1000
                  for r in csv.DictReader((p / 'waits.csv').open())}
        for frame in slow:
            # This timestamps the end of the between-hook wait interval,
            # before host command processing and pacing, not display present.
            frame['wait_interval_end_wall_seconds'] = bounds.get(frame['frame'])
    ordered = sorted(values)
    result = {'window_game_frame_seconds': [args.start, args.end],
              'frames': len(values), 'covered_seconds': sum(values) / 1000,
              'mean_ms': statistics.mean(values), 'p95_ms': ordered[int(len(values) * .95)],
              'p99_ms': ordered[int(len(values) * .99)], 'max_ms': max(values),
              'over_25_ms': len(slow),
              'command_phase_over_1_ms': sum(f['commands_ms'] > 1 for f in slow),
              'command_phase_max_ms': max((f['commands_ms'] for f in slow), default=0),
              'wait_chains': [{'chain': k, 'frames': v} for k, v in chains.most_common()],
              'source_profile_unchanged': scenario['source_profile_unchanged'],
              'retail_checkpoint_unchanged': travel['retail_checkpoints_unchanged'],
              'exit_code': native['exit_code_before_cleanup'], 'timed_out': native['timed_out'],
              'executable_sha256': native['executable_sha256'], 'gpu_plugin': native['gpu_plugin'],
              'turn_around': scenario.get('turn_around', False), 'slow_frames': slow,
              'scope': 'Inspect scene images. Debug travel, input helpers, screenshots and wait/command logging limit performance and campaign-parity conclusions.'}
    (p / 'motion-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'slow_frames'}, indent=2))


if __name__ == '__main__':
    main()
