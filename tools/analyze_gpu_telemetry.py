"""Join read-only NVIDIA samples to a verified quiet observation's QPC bounds."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import statistics

from gpu_telemetry import FIELDS


def analyze(probe, visit=None):
    if visit is not None and not 0 <= visit < 12:
        raise ValueError('Visit must be in [0, 11]')
    read = lambda name: json.loads((probe / name).read_text())
    capture = read('gpu-telemetry.json')
    prefix = 'quiet' if visit is None else 'visit-%02d' % visit
    quiet = read('quiet-pacing.json' if visit is None else 'session-pacing.json')
    window = read(prefix + '-window.json')
    pacing = read(prefix + '-analysis.json')
    if (not capture['complete'] or capture['fields'] != FIELDS or not quiet['complete']
            or not pacing['complete'] or not pacing['foreground_verified']):
        raise ValueError('Require completed telemetry and verified quiet analysis')
    start, end = capture['start'], capture['end']
    if (start['utc_offset_seconds'] != end['utc_offset_seconds']
            or max(start['uncertainty_ms'], end['uncertainty_ms']) > 5):
        raise ValueError('Ambiguous wall-clock calibration')
    offsets = [item['wall_ms'] - item['steady_ms'] for item in (start, end)]
    drift = abs(offsets[1] - offsets[0])
    if drift > 100:
        raise ValueError('Wall clock moved by more than 100 ms')
    zone = timezone(timedelta(seconds=start['utc_offset_seconds']))
    offset = statistics.mean(offsets)
    first, last = window['steady_clock_start_ms'], window['steady_clock_end_ms']
    if last - first < 75000 or not start['steady_ms'] <= first < last <= end['steady_ms']:
        raise ValueError('Telemetry does not surround a full quiet window')
    samples = {}
    with (probe / 'gpu-telemetry.csv').open(newline='') as stream:
        for raw in csv.reader(stream):
            if len(raw) != len(FIELDS):
                raise ValueError('Malformed NVIDIA sample: ' + repr(raw))
            row = dict(zip(FIELDS, (value.strip() for value in raw)))
            stamp = datetime.strptime(row['timestamp'], '%Y/%m/%d %H:%M:%S.%f').replace(tzinfo=zone)
            row['steady_ms'] = stamp.timestamp() * 1000 - offset
            samples.setdefault(row['uuid'], []).append(row)
    if not samples:
        raise ValueError('Empty telemetry')
    summaries = {}
    for gpu, rows in samples.items():
        selected = [row for row in rows if first <= row['steady_ms'] <= last]
        if (len(selected) < 2 or selected[0]['steady_ms'] - first > 1500
                or last - selected[-1]['steady_ms'] > 1500):
            raise ValueError('Incomplete GPU sample coverage: ' + gpu)
        gaps = [b['steady_ms'] - a['steady_ms'] for a, b in zip(selected, selected[1:])]
        if min(gaps) <= 0 or max(gaps) > 2500:
            raise ValueError('GPU telemetry has duplicate, reversed or missing samples')
        metrics = {}
        for field in FIELDS[4:12]:
            values = []
            for row in selected:
                try:
                    number = float(row[field])
                except ValueError:
                    if row[field] not in ('N/A', '[N/A]', '[Not Supported]'):
                        raise ValueError('Invalid numeric telemetry: ' + row[field])
                    continue
                if not math.isfinite(number):
                    raise ValueError('Non-finite telemetry')
                values.append(number)
            metrics[field] = dict(available=len(values), missing=len(selected) - len(values),
                                  minimum=min(values) if values else None,
                                  maximum=max(values) if values else None,
                                  mean=statistics.mean(values) if values else None)
        states = {}
        for field in ('pstate', *FIELDS[12:]):
            states[field] = {value: sum(row[field] == value for row in selected)
                             for value in sorted({row[field] for row in selected})}
        summaries[gpu] = dict(samples=len(selected), maximum_gap_ms=max(gaps),
                              metrics=metrics, states=states, rows=selected)
    return dict(complete=True, clock_drift_ms=drift, gpus=summaries,
                limits='One-second, device-wide samples include other processes. '
                       'Clock stability does not establish renderer causality or frame-level GPU costs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--visit', type=int, choices=range(12), help='Analyze a completed session window')
    args = parser.parse_args()
    result = analyze(args.probe, args.visit)
    prefix = '' if args.visit is None else 'visit-%02d-' % args.visit
    (args.probe / (prefix + 'gpu-telemetry-analysis.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({**result, 'gpus': {gpu: {k: v for k, v in data.items() if k != 'rows'}
                                      for gpu, data in result['gpus'].items()}}, indent=2))


if __name__ == '__main__':
    main()
