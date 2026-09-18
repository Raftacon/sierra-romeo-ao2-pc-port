"""Attribute slow frame intervals to measured host phases and the gap between hooks."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--start', type=float, default=75)
    parser.add_argument('--end', type=float, default=375)
    parser.add_argument('--threshold-ms', type=float, default=25)
    args = parser.parse_args()
    if not 0 <= args.start < args.end or args.threshold_ms <= 0:
        parser.error('Require 0 <= start < end and a positive threshold')
    with (args.probe / 'phases.csv').open() as f:
        phases = {int(row['frame']): {k: float(v) for k, v in row.items()} for row in csv.DictReader(f)}
    waits = {}
    if (args.probe / 'waits.csv').exists():
        with (args.probe / 'waits.csv').open() as f:
            for row in csv.DictReader(f):
                item = {'api': row['api'], 'calls': int(row['calls']),
                        'total_ms': float(row['total_ms']), 'max_ms': float(row['max_ms'])}
                for key in ('max_caller', 'max_arg0', 'max_arg1', 'max_result', 'parent1', 'parent2', 'parent3', 'parent4'):
                    if key in row:
                        item[key] = f"0x{int(row[key]):08X}"
                waits.setdefault(int(row['frame']), []).append(item)
    elapsed, covered, measured, missing = 0.0, 0.0, 0, 0
    slow = []
    with (args.probe / 'frame-times.csv').open() as f:
        for row in csv.DictReader(f):
            frame, interval = int(row['frame']), float(row['interval_ms'])
            elapsed += interval / 1000
            if not args.start <= elapsed < args.end:
                continue
            covered += interval / 1000
            measured += 1
            if frame not in phases or frame - 1 not in phases:
                missing += 1
                continue
            if interval < args.threshold_ms:
                continue
            current, previous = phases[frame], phases[frame - 1]
            components = {k: current[k] for k in ('between_hooks_ms', 'commands_ms', 'pacing_ms')}
            components.update(previous_bookkeeping_ms=previous['bookkeeping_ms'],
                              previous_trace_write_ms=current['previous_trace_write_ms'])
            slow.append({'frame': frame, 'game_elapsed_seconds': elapsed, 'interval_ms': interval,
                         **components, 'between_hooks_cpu_ms': current['between_hooks_cpu_ms'],
                         'unaccounted_ms': interval - sum(components.values()),
                         'runtime_wait_call_ms': sum(r['total_ms'] for r in waits.get(frame, [])),
                         'runtime_waits': waits.get(frame, [])})
    result = {'window_game_frame_seconds': [args.start, args.end], 'covered_seconds': covered,
              'frames': measured, 'missing_phase_rows': missing, 'threshold_ms': args.threshold_ms,
              'slow_frames': slow,
              'limitation': 'Between-hook time includes original game/runtime work and waits. CPU accounting is coarse; this does not identify a specific wait, function or GPU fault.'}
    (args.probe / 'phase-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
