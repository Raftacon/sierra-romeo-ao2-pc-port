"""Join slow game frames to rendering-fence intervals by diagnostic timestamps."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    analysis = json.loads((args.probe / 'phase-analysis.json').read_text())
    with (args.probe / 'waits.csv').open() as f:
        game = {int(r['frame']): (float(r['start_ms']), float(r['end_ms']))
                for r in csv.DictReader(f)}
    fences = {}
    with (args.probe / 'render-waits.csv').open() as f:
        for row in csv.DictReader(f):
            key = (int(row.get('host_thread', 0)), int(row['fence']))
            fence = fences.setdefault(key, {
                'host_thread': key[0], 'fence': key[1],
                'start_ms': float(row['start_ms']), 'end_ms': float(row['end_ms']),
                'interval_ms': float(row['interval_ms']), 'calls': []})
            if row['api'] != 'interval':
                call = {k: row[k] for k in ['api']}
                call.update(calls=int(row['calls']), total_ms=float(row['total_ms']), max_ms=float(row['max_ms']))
                for field in ['max_caller', 'max_arg0', 'max_arg1', 'max_result', 'parent1', 'parent2', 'parent3', 'parent4']:
                    call[field] = f"0x{int(row[field]):08X}"
                fence['calls'].append(call)
    intervals = sorted(fences.values(), key=lambda r: r['end_ms'])
    result = {'render_intervals': len(intervals),
              'render_start_ms': intervals[0]['start_ms'] if intervals else None,
              'render_end_ms': intervals[-1]['end_ms'] if intervals else None,
              'slow_frames': [],
              'limitation': 'These rendering intervals end during the game interval; overlap does not identify a unique cause. Render intervals also include idle time and untraced work.'}
    for frame in analysis['slow_frames']:
        bounds = game.get(frame['frame'])
        matches = [r for r in intervals if bounds and bounds[0] <= r['end_ms'] <= bounds[1]]
        result['slow_frames'].append({'frame': frame['frame'], 'interval_ms': frame['interval_ms'],
            'game_wait_bounds_ms': bounds, 'ending_render_intervals': matches})
    (args.probe / 'render-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
