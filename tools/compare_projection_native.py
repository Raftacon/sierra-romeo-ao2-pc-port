"""Compare closed native projection probes in their final quiet gameplay interval.

Uses actual game-hook intervals, not the lossless video's nominal frame rate.
This is a capped scene timing comparison, not isolated GPU timestamp cost.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


def inspect(path, enabled):
    probe = json.loads((path / 'probe.json').read_text())
    travel = json.loads((path / 'travel.json').read_text())
    scenario = json.loads((path / 'wall-scenario.json').read_text())
    if probe.get('timed_out') or probe.get('exit_code_before_cleanup') != 0 or scenario['checkpoint_helper_exit'] != 0:
        raise ValueError('Require normally closed native run')
    if not any(e.get('checkpoint_reference_verified') == '01_04' for e in travel['events']):
        raise ValueError('Missing exact alley checkpoint verification')
    if not travel.get('checkpoint_unchanged') or not travel.get('source_profile_unchanged'):
        raise ValueError('Retail checkpoint or source test profile changed')
    command = probe['command']
    if any('renderdoc' in x.lower() for x in command) or '--aot_renderdoc_capture=true' in command:
        raise ValueError('Native timing must not include GPU capture')
    for flag in ['--gpu_plugin=spatial', '--aot_fps=60', '--vsync=true', '--resolution_scale=1',
                 '--window_width=1920', '--window_height=1080', '--swap_post_effect=fxaa',
                 '--readback_resolve=fast', '--input_backend=xinput', '--aot_keyboard_mouse=false',
                 '--aot_projection_precision=' + str(enabled).lower()]:
        if flag not in command: raise ValueError('Unexpected comparison settings: ' + flag)
    log = (path / 'runtime.log').read_text(errors='replace')
    errors = re.findall(r'^.*(?:\[(?:error|critical)\]|DEVICE_(?:HUNG|REMOVED)).*$', log, re.MULTILINE)
    if errors: raise ValueError('Native diagnostic errors: ' + '\n'.join(errors[:5]))
    applied = re.findall(r'projection precision applied: guest=([0-9A-F]+), modification=([0-9A-F]+)', log)
    expected = {('480333F4AFCF0E1E', '0000000000000000'), ('480333F4AFCF0E1E', '000000000000007F'),
                ('D6E05D80EF7DEBF8', '0000000000000000')}
    if (set(applied) != expected if enabled else bool(applied)):
        raise ValueError('Unexpected runtime shader correction coverage')
    if enabled and 'requested=true, supported=true, enabled=true' not in log:
        raise ValueError('Missing hardware support confirmation')
    rows, elapsed, previous = [], 0.0, 0
    for row in csv.DictReader((path / 'frame-times.csv').open()):
        frame, interval = int(row['frame']), float(row['interval_ms'])
        if frame <= previous or not math.isfinite(interval) or interval <= 0:
            raise ValueError('Invalid frame log')
        elapsed += interval / 1000
        rows.append((elapsed, interval))
        previous = frame
    # End-relative alignment avoids assuming that the first hook occurred at
    # launch. In these runs it begins well after native process startup.
    values = [ms for t, ms in rows if elapsed - 22 <= t < elapsed - 7]
    if len(values) < 200 or not 14.9 < sum(values)/1000 < 15.1:
        raise ValueError('Incomplete quiet interval')
    # Ensure this interval is safely past the last capture/sweep in the scenario.
    stationary = next(e['wall_seconds'] for e in scenario['events'] if e.get('capture') == 'wall-stationary.png')
    if probe['elapsed_seconds'] - 22 <= stationary:
        raise ValueError('Quiet interval overlaps a scenario capture')
    ordered = sorted(values)
    return {'path': str(path.resolve()), 'enabled': enabled, 'native_elapsed_seconds': probe['elapsed_seconds'],
            'executable_sha256': probe['executable_sha256'], 'gpu_plugin_sha256': probe['gpu_plugin']['sha256'],
            'checkpoint_verified': '01_04', 'applied': applied, 'game_log_duration_seconds': elapsed,
            'source_profile_sha256': travel['source_profile_sha256'],
            'window_game_seconds': [elapsed-22, elapsed-7], 'window_seconds_before_last_frame': [22, 7],
            'frames': len(values), 'covered_seconds': sum(values)/1000,
            'fps': 1000/statistics.mean(values), 'mean_ms': statistics.mean(values),
            'p50_ms': statistics.median(values), 'p95_ms': ordered[int(len(ordered)*.95)],
            'p99_ms': ordered[int(len(ordered)*.99)], 'max_ms': max(values),
            'over_20ms': sum(x > 20 for x in values),
            'frame_log_sha256': hashlib.sha256((path / 'frame-times.csv').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--corrected', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('Use a new output file')
    a, b = inspect(args.baseline, False), inspect(args.corrected, True)
    if any(a[k] != b[k] for k in ['executable_sha256', 'gpu_plugin_sha256', 'source_profile_sha256']):
        raise ValueError('Comparison requires the same executable, plugin and source profile')
    result = {'scope': __doc__.strip(), 'baseline': a, 'corrected': b,
              'limitation': 'One run per mode, VSync and 60 FPS limit enabled; stationary checkpoint interval. '
                            'Does not measure isolated shader GPU cost, uncapped throughput, or campaign-wide parity.'}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
