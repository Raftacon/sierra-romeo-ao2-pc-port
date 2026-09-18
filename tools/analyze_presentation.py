"""Validate native presentation records and summarize swap-chain policy changes."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    p = args.probe.resolve()
    native = json.loads((p / 'probe.json').read_text())
    scenario = json.loads((p / 'presentation-scenario.json').read_text())
    rows = list(csv.DictReader((p / 'presents.csv').open()))
    if not rows or any(int(r['ProcessID']) != native['pid'] or r['Runtime'] != 'DXGI' for r in rows):
        raise ValueError('Require DXGI records for only the selected native child')
    if native['exit_code_before_cleanup'] != 0 or native['timed_out'] or not scenario['source_profile_unchanged']:
        raise ValueError('Native completion or source-profile preservation failed')
    if scenario['presentmon_exit_code'] != 0:
        raise ValueError('PresentMon did not complete successfully')
    segments = []
    for row in rows:
        key = (row['SwapChainAddress'], int(row['PresentFlags']), int(row['SyncInterval']))
        if not segments or segments[-1]['key'] != key:
            segments.append({'key': key, 'first_seconds': float(row['TimeInSeconds']),
                             'last_seconds': 0, 'rows': 0,
                             'allows_tearing': collections.Counter(), 'present_modes': collections.Counter()})
        segment = segments[-1]
        segment['last_seconds'] = float(row['TimeInSeconds'])
        segment['rows'] += 1
        segment['allows_tearing'][row['AllowsTearing']] += 1
        segment['present_modes'][row['PresentMode']] += 1
    for segment in segments:
        address, flags, interval = segment.pop('key')
        segment.update(swap_chain=address, present_flags=flags, sync_interval=interval,
                       tearing_requested=bool(flags & 0x200))
    if scenario.get('settings_sequence'):
        if [s['tearing_requested'] for s in segments] != [False, True, False]:
            raise ValueError('Expected live On -> Off -> On swap-chain policy')
        if any(s['allows_tearing'].get('1', 0) for s in (segments[0], segments[-1])):
            raise ValueError('Tearing was allowed while VSync was On')
        log = (p / 'runtime.log').read_text(errors='replace')
        if 'PC Exit confirmed; requesting window close' not in log:
            raise ValueError('Native Exit confirmation did not complete')
    report = {'rows': len(rows), 'segments': segments,
              'executable_sha256': native['executable_sha256'], 'gpu_plugin': native['gpu_plugin'],
              'native_exit_code': native['exit_code_before_cleanup'],
              'native_elapsed_seconds': native['elapsed_seconds'],
              'source_profile_unchanged': scenario['source_profile_unchanged'],
              'captures': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*.png')},
              'scope': 'DXGI presentation policy for the captured native child; does not prove game simulation FPS or campaign rendering parity.'}
    (p / 'presentation-analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'captures'}, indent=2))


if __name__ == '__main__':
    main()
