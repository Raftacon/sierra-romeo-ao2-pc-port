"""Correlate guest VSync callbacks, observed writeback changes and GPU packet waits."""
import argparse
import bisect
import csv
import json
from pathlib import Path
import re
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    packet = json.loads((args.probe / 'gpu-wait-analysis.json').read_text())
    origins = re.findall(r'Wait diagnostic steady-clock origin_ms=([\d.]+)',
                         (args.probe / 'runtime.log').read_text(errors='replace'))
    if len(origins) != 1: raise ValueError('Require one native steady-clock origin')
    origin = float(origins[0])
    low, high = packet['game_clock_bounds_ms']
    callbacks = []
    with (args.probe / 'gpu-vblank.csv').open() as f:
        for raw in csv.DictReader(f):
            row = {k: float(v) if k.endswith('_ms') else int(v) for k, v in raw.items()}
            row['start_ms'] = row.pop('start_clock_ms') - origin
            row['end_ms'] = row.pop('end_clock_ms') - origin
            callbacks.append(row)
    selected = [r for r in callbacks if low <= r['start_ms'] and r['end_ms'] <= high]
    if not selected: raise ValueError('No callbacks in the measured game-clock window')
    durations = sorted(r['interval_ms'] for r in selected)
    watched = {r['watch_address'] for r in selected}
    watched_callbacks = [r for r in selected if r['watch_address'] != 0xFFFFFFFF]
    packet_addresses = {int(r['poll_address'], 16) & ~3 for r in packet['conditions'] if int(r['wait_info'], 16) & 0x10}
    same_watch = len(watched) == 1 and watched == packet_addresses
    starts = [r['start_ms'] for r in callbacks]
    result = {'window_game_frame_seconds': packet['window_game_frame_seconds'],
              'callbacks': len(selected), 'mean_interval_ms': statistics.mean(durations),
              'min_interval_ms': min(durations), 'p99_interval_ms': durations[int(.99 * (len(durations) - 1))],
              'max_interval_ms': max(durations), 'max_callback_ms': max(r['callback_ms'] for r in selected),
              'watch_matches_packet_memory': same_watch,
              'watched_callbacks': len(watched_callbacks),
              'read_failures': sum(not r['before_ok'] or not r['after_ok'] for r in watched_callbacks),
              'nonzero_to_zero': sum(r['before_ok'] and r['after_ok'] and r['before_raw'] != 0 and r['after_raw'] == 0 for r in selected),
              'nonzero_after': sum(r['after_ok'] and r['after_raw'] != 0 for r in selected),
              'slow_frames': [],
              'limitation': 'Before/after reads are observations amid concurrent guest activity, not exclusive writer attribution. Overlap and nearby callbacks do not prove causation; all tracing can perturb scheduling.'}
    for frame in packet['slow_frames']:
        bounds = frame['game_wait_bounds_ms']
        overlaps = [r for r in callbacks if bounds and bounds[0] <= r['start_ms'] <= bounds[1]]
        waits = []
        for wait in frame['gpu_packets']:
            previous_index = bisect.bisect_right(starts, wait['start_ms']) - 1
            previous = callbacks[previous_index] if previous_index >= 0 else None
            during = [r for r in callbacks if wait['start_ms'] <= r['start_ms'] <= wait['end_ms']]
            releases = [r for r in during if same_watch and r['before_ok'] and r['after_ok'] and r['before_raw'] != 0 and r['after_raw'] == 0]
            waits.append(dict(packet=wait['packet'], packet_ms=wait['packet_ms'],
                              start_after_previous_vblank_ms=wait['start_ms'] - previous['start_ms'] if previous else None,
                              callbacks_during_wait=during,
                              end_after_last_observed_clear_ms=wait['end_ms'] - releases[-1]['end_ms'] if releases else None))
        result['slow_frames'].append(dict(frame=frame['frame'], interval_ms=frame['interval_ms'],
                                         callbacks_during_game_wait=overlaps, gpu_packets=waits))
    (args.probe / 'gpu-vblank-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'slow_frames'}, indent=2))


if __name__ == '__main__':
    main()
