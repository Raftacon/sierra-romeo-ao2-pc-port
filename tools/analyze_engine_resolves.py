"""Summarize bounded engine observations from a normally closed native probe.

Native texture descriptors are retained verbatim. They are not interchangeable
with final GPU fetch constants. Read failures include the bounded stack walk;
these observations are not proof that the game accessed invalid resource data.
"""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path

SITES = {1: 'surface_copy', 2: 'device_resolve', 3: 'light_loop_entry',
         4: 'light_loop_return', 5: 'begin_scene_color', 7: 'finish_scene_color',
         8: 'light_entry', 9: 'light_return', 10: 'experimental_dirty_override'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--row-limit', type=int, choices=(4096, 8192), default=8192)
    a = parser.parse_args(); root = a.probe
    native = json.loads((root / 'probe.json').read_text())
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with (root / 'engine-resolves.csv').open() as f: raw = list(csv.DictReader(f))
    if not 0 < len(raw) < a.row_limit: raise ValueError('Empty or potentially truncated engine trace')
    rows = []; previous = -math.inf
    for i, r in enumerate(raw):
        clock = float(r['clock_ms'])
        if not math.isfinite(clock) or clock < previous: raise ValueError('Invalid clock sequence')
        previous = clock
        row = {k: int(v) for k, v in r.items() if k != 'clock_ms'}
        if row['call'] != i or row['site'] not in SITES: raise ValueError('Invalid call sequence/site')
        if any(v < 0 or v > 0xFFFFFFFF for v in row.values()): raise ValueError('Out-of-range observation')
        rows.append(dict(row, clock_ms=clock))
    elapsed = rows[-1]['clock_ms'] - rows[0]['clock_ms']
    if elapsed > 2000: raise ValueError('Trace exceeded two-second window')
    groups = Counter((r['site'], r['caller'], r['r3'], r['r4'],
                      tuple(r['surface' + str(i)] for i in range(3)),
                      tuple(r['color_fetch' + str(i)] for i in range(6)),
                      tuple(r['depth_fetch' + str(i)] for i in range(6))) for r in rows)
    failures = Counter((r['site'], r['caller'], r['failed_reads']) for r in rows if r['failed_reads'])
    inputs = Counter((r['r4'], r['r5']) for r in rows if r['site'] == 3)
    report = {'complete': True, 'rows': len(rows), 'elapsed_ms': elapsed,
        'sites': {SITES[k]: v for k, v in Counter(r['site'] for r in rows).items()},
        'groups': [{'site': SITES[k[0]], 'caller': hex(k[1]), 'r3': hex(k[2]), 'r4': hex(k[3]),
                    'surface': [hex(x) for x in k[4]], 'color_descriptor': [hex(x) for x in k[5]],
                    'depth_descriptor': [hex(x) for x in k[6]], 'count': n} for k, n in groups.items()],
        'read_failures': [{'site': SITES[k[0]], 'caller': hex(k[1]), 'reads_per_call': k[2],
                           'calls': n} for k, n in failures.items()],
        'light_loop_inputs': [{'phase': k[0], 'incoming_changed': k[1], 'calls': n} for k, n in inputs.items()],
        'light_returns': dict(Counter(r['r3'] for r in rows if r['site'] == 9)),
        'executable_sha256': native['executable_sha256'], 'gpu_plugin': native['gpu_plugin'],
        'command': native['command'], 'limits': __doc__}
    (root / 'engine-resolve-analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('groups', 'command', 'limits')}, indent=2))


if __name__ == '__main__': main()
