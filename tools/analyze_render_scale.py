"""Compare rendering scales or 1x output filters with device-wide NVIDIA samples.

GPU telemetry is not process-attributed; still images require visual review.
"""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probes', type=Path, nargs='+')
    parser.add_argument('--comparison', choices=('scale', 'upscaler'), default='scale')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.probes) != (3 if args.comparison == 'scale' else 2):
        parser.error('Scale comparison needs three runs; upscaler comparison needs two')
    results, identities = [], []
    for path in args.probes:
        native = json.loads((path / 'probe.json').read_text())
        run = json.loads((path / 'partner-camera.json').read_text())
        if native['timed_out'] or native['exit_code_before_cleanup'] != 0 or not all(
                run[name] for name in ('source_profile_unchanged', 'checkpoint_unchanged', 'state_sequence_verified', 'gpu_telemetry')):
            raise ValueError('Incomplete native test or preservation check: ' + str(path))
        command = native['command']
        if args.comparison == 'upscaler':
            expected = '--aot_spatial_upscale=' + str(run.get('upscaler') == 'fsr').lower()
            if [arg for arg in command if arg.startswith('--aot_spatial_upscale=')] != [expected]:
                raise ValueError('Upscaler command disagrees with recorded mode')
        for name, value in [('resolution_scale', run['scale']), ('window_width', run['window'][0]), ('window_height', run['window'][1])]:
            if [arg for arg in command if arg.startswith('--' + name + '=')] != ['--' + name + '=' + str(value)]:
                raise ValueError('Missing, conflicting or duplicate rendering arguments')
        identities.append((native['executable_sha256'], native['gpu_plugin']['sha256'],
                           run['checkpoint']['sha256'], run['window'], run['source_profile_sha256']))
        if [phase['camera_enabled'] for phase in run['phases']] != [True, False, True]:
            raise ValueError('Expected verified On/Off/On camera sequence')
        with (path / 'gpu-telemetry.csv').open() as stream:
            telemetry = [{key.strip(): value.strip() for key, value in row.items()} for row in csv.DictReader(stream)]
        if len({row['index'] for row in telemetry}) != 1:
            raise ValueError('Require exactly one GPU in these telemetry records')
        gpu_names = {row['name'] for row in telemetry}
        if len(gpu_names) != 1:
            raise ValueError('GPU identity changed during capture')
        phases = []
        for phase in run['phases']:
            if phase['focus_lost'] or not phase['rotation_unchanged']:
                raise ValueError('View or focus changed during a timing window')
            if run['scale'] > 1 and not phase.get('start_marker_after_settle'):
                raise ValueError('Low-FPS comparison requires a fresh frame-log flush after settling')
            start, end = map(datetime.fromisoformat, (phase['local_start'], phase['local_end']))
            rows = [row for row in telemetry if start <= datetime.strptime(row['timestamp'], '%Y/%m/%d %H:%M:%S.%f') <= end]
            if len(rows) < 10:
                raise ValueError('Insufficient phase GPU telemetry')
            def values(field):
                return [float(row[field].split()[0]) for row in rows]
            phases.append({**phase, 'mean_game_fps': 1000 / phase['mean_ms'], 'gpu_samples': len(rows),
                           'gpu_utilization_mean_percent': statistics.mean(values('utilization.gpu [%]')),
                           'gpu_utilization_max_percent': max(values('utilization.gpu [%]')),
                           'gpu_memory_max_mib': max(values('memory.used [MiB]')),
                           'gpu_power_mean_w': statistics.mean(values('power.draw [W]')),
                           'gpu_clock_min_mhz': min(values('clocks.current.graphics [MHz]')),
                           'gpu_clock_max_mhz': max(values('clocks.current.graphics [MHz]'))})
        results.append({'probe': str(path.resolve()), 'scale': run['scale'], 'window': run['window'],
                        'upscaler': run.get('upscaler', 'bilinear'),
                        'gpu_name': next(iter(gpu_names)), 'phases': phases})
    if args.comparison == 'scale' and sorted(run['scale'] for run in results) != [1, 2, 3]:
        raise ValueError('Require one run of each rendering scale')
    if args.comparison == 'upscaler' and (any(run['scale'] != 1 for run in results) or
            sorted(run['upscaler'] for run in results) != ['bilinear', 'fsr']):
        raise ValueError('Require bilinear and FSR runs at 1x')
    if any(identity != identities[0] for identity in identities[1:]):
        raise ValueError('Executable, GPU plugin, checkpoint, window or seed profile differs')
    if len({run['gpu_name'] for run in results}) != 1 or len({p['rotation_before'] for r in results for p in r['phases']}) != 1:
        raise ValueError('GPU or controller rotation differs between runs')
    report = {'scope': __doc__.strip(), 'comparison': args.comparison,
              'runs': sorted(results, key=lambda run: (run['scale'], run['upscaler']))}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    # Unscaled pixel crops for visual review, not a numerical quality score.
    # The clothing animates between runs; the vent is a stationary reference.
    crops = [('Wall vent', (210, 145, 530, 385)), ('Clothing', (1140, 510, 1460, 750))]
    sheet = Image.new('RGB', (320 * len(results), 560), '#202020')
    draw = ImageDraw.Draw(sheet)
    for column, run in enumerate(report['runs']):
        source = Path(run['probe']) / 'initial.png'
        with Image.open(source) as picture:
            if picture.size != (1936, 1119):
                raise ValueError('Clarity crop coordinates require the observed 1080p window dimensions')
            for row, (label, box) in enumerate(crops):
                x, y = column * 320, row * 280
                draw.text((x + 8, y + 8), f"{run['scale']}x {run['upscaler']} - {label}", fill='white')
                sheet.paste(picture.crop(box), (x, y + 32))
    sheet.save(args.output.with_suffix('.clarity.png'))
    for run in report['runs']:
        print(f"{run['scale']}x {run['upscaler']}: " + ', '.join(f"{p['name']} {p['mean_game_fps']:.1f} FPS / {p['gpu_utilization_mean_percent']:.1f}% GPU" for p in run['phases']))


if __name__ == '__main__':
    main()
