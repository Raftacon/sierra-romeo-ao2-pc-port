"""Summarize actual GPU packet sleeps and their overlap with traced game frames."""
import argparse
import collections
import csv
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    phase = json.loads((args.probe / 'phase-analysis.json').read_text())
    origins = re.findall(r'Wait diagnostic steady-clock origin_ms=([\d.]+)',
                         (args.probe / 'runtime.log').read_text(errors='replace'))
    if len(origins) != 1:
        raise ValueError('Require exactly one native steady-clock origin')
    origin = float(origins[0])
    with (args.probe / 'waits.csv').open() as stream:
        frames = {int(row['frame']): (float(row['start_ms']), float(row['end_ms']))
                  for row in csv.DictReader(stream)}
    elapsed, selected = 0.0, []
    with (args.probe / 'frame-times.csv').open() as stream:
        for row in csv.DictReader(stream):
            elapsed += float(row['interval_ms']) / 1000
            if phase['window_game_frame_seconds'][0] <= elapsed < phase['window_game_frame_seconds'][1]:
                if int(row['frame']) in frames: selected.append(frames[int(row['frame'])])
    if not selected: raise ValueError('No traced game waits in the requested window')
    low, high = min(v[0] for v in selected), max(v[1] for v in selected)
    packets = []
    with (args.probe / 'gpu-waits.csv').open() as stream:
        for raw in csv.DictReader(stream):
            row = {k: float(v) if k.endswith('_ms') else int(v) for k, v in raw.items()}
            if row['polls'] == 1 and row['matched'] and row['packet_ms'] < .1:
                continue  # Also supports the initial, unfiltered prototype trace.
            row['start_ms'] = row.pop('start_clock_ms') - origin
            row['end_ms'] = row.pop('end_clock_ms') - origin
            if low <= row['start_ms'] and row['end_ms'] <= high: packets.append(row)
    if not packets: raise ValueError('No GPU packets inside the selected game-clock window')
    groups = collections.defaultdict(list)
    for row in packets: groups[(row['wait_info'], row['poll_address'], row['wait_operand'])].append(row)
    def summarize(rows):
        calls = sum(r['sleep_calls'] for r in rows)
        actual = sum(r['total_sleep_ms'] for r in rows)
        return dict(packets=len(rows), sleep_calls=calls,
                    requested_sleep_ms=sum(r['requested_sleep_ms'] for r in rows),
                    actual_sleep_ms=actual, mean_sleep_ms=actual / calls if calls else None,
                    max_sleep_ms=max((r['max_sleep_ms'] for r in rows), default=0),
                    max_packet_ms=max((r['packet_ms'] for r in rows), default=0))
    copies = []
    if (args.probe / 'gpu-copies.csv').exists():
        with (args.probe / 'gpu-copies.csv').open() as stream:
            for raw in csv.DictReader(stream):
                row = {k: float(v) if k.endswith('_ms') else int(v) for k, v in raw.items()}
                row['start_ms'] = row.pop('start_clock_ms') - origin
                row['end_ms'] = row.pop('end_clock_ms') - origin
                if low <= row['start_ms'] and row['end_ms'] <= high: copies.append(row)
    result = {'window_game_frame_seconds': phase['window_game_frame_seconds'],
              'game_clock_bounds_ms': [low, high], 'summary': summarize(packets),
              'conditions': [dict(wait_info=hex(key[0]), poll_address=hex(key[1]),
                                  wait_operand=hex(key[2]), **summarize(rows))
                             for key, rows in groups.items()],
              'copy_summary': dict(gpu_frames=len(copies), copies=sum(r['copies'] for r in copies),
                  bytes=sum(r['bytes'] for r in copies), total_copy_ms=sum(r['total_copy_ms'] for r in copies),
                  max_copy_ms=max((r['max_copy_ms'] for r in copies), default=0),
                  max_frame_copy_ms=max((r['total_copy_ms'] for r in copies), default=0)) if copies else None,
              'slow_frames': [],
              'limitation': 'Overlap is not causation. Whole-packet sleep totals and GPU-frame copy totals can include time outside an overlapping game interval. Immediate matched packets under 0.1 ms are excluded. Copy tracing covers only the legacy resolve memcpy.'}
    for frame in phase['slow_frames']:
        bounds = frames.get(frame['frame'])
        overlap = [r for r in packets if bounds and r['start_ms'] < bounds[1] and r['end_ms'] > bounds[0]]
        result['slow_frames'].append(dict(frame=frame['frame'], interval_ms=frame['interval_ms'],
                                         game_wait_bounds_ms=bounds, gpu_packets=overlap,
                                         gpu_copy_frames=[r for r in copies if bounds and r['start_ms'] < bounds[1] and r['end_ms'] > bounds[0]]))
    (args.probe / 'gpu-wait-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'slow_frames'}, indent=2))
    print(f"Slow frames: {len(result['slow_frames'])}; details in gpu-wait-analysis.json")


if __name__ == '__main__':
    main()
