"""Verify matched native controls before comparing the projection FMA experiment."""
import argparse
import json
from pathlib import Path
import re

from compare_quiet_guest_vsync import inspect_timer


def inspect_fma(path):
    shared, run = inspect_timer(path)
    if run['guest_vsync'] != 'true':
        raise ValueError('Require the normal guest refresh timer')
    command = shared['command']
    values = [arg.partition('=')[2] for arg in command if arg.startswith('--aot_projection_fma=')]
    if len(values) != 1 or values[0] not in ('true', 'false'):
        raise ValueError('Require an explicit, unambiguous FMA choice')
    log = (path / 'runtime.log').read_text(errors='replace')
    if re.findall(r'AOT projection FMA: enabled=(true|false)', log) != values:
        raise ValueError('Runtime FMA selection differs from requested setting')
    for flag in ('--aot_projection_precision=true', '--aot_automatic_projection_precision=true',
                 '--aot_projection_source_snapshots=true'):
        if command.count(flag) != 1:
            raise ValueError('Missing normal projection correction: ' + flag)
    shared['command'] = [arg for arg in command if not arg.startswith('--aot_projection_fma=')]
    shared['guest_vsync'] = run['guest_vsync']
    run['projection_fma'] = values[0]
    return shared, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probes', type=Path, nargs=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Require a new output file')
    a, b = [inspect_fma(path.resolve()) for path in args.probes]
    if a[0] != b[0]:
        raise ValueError('Unmatched controls: ' + ', '.join(k for k in a[0] if a[0][k] != b[0][k]))
    if {a[1]['projection_fma'], b[1]['projection_fma']} != {'true', 'false'}:
        raise ValueError('Require one enabled and one disabled FMA run')
    report = dict(complete=True, shared=a[0], runs=[a[1], b[1]],
                  limits='Sequential static-scene measurement after warmup; not motion or campaign parity. '
                         'FMA uses a separate shader cache. Cache inventories and GPU telemetry are retained.')
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({r['projection_fma']: r['summary'] for r in report['runs']}, indent=2))


if __name__ == '__main__':
    main()
