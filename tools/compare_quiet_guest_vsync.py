"""Compare guest refresh pacing while retaining no-tearing host presentation."""
import argparse
import json
from pathlib import Path
import re

from compare_quiet_readback import inspect


def inspect_timer(path):
    shared, run = inspect(path)
    if run['mode'] != 'fast' or run['completed_snapshots'] != 'false':
        raise ValueError('Require explicit normal fast readback for this comparison')
    command = shared['command']
    modes = [argument.partition('=')[2] for argument in command if argument.startswith('--vsync=')]
    if len(modes) != 1 or modes[0] not in ('true', 'false'):
        raise ValueError('Ambiguous guest VSync selection')
    for flag in ('--d3d12_allow_variable_refresh_rate_and_tearing=false', '--aot_fps=60'):
        if command.count(flag) != 1:
            raise ValueError('Missing explicit presentation control: ' + flag)
    log = (path / 'runtime.log').read_text(errors='replace')
    presentation = re.findall(r'PC initial presentation: vsync=(true|false), allow_tearing=(true|false),', log)
    if presentation != [(modes[0], 'false')]:
        raise ValueError('Runtime presentation does not match requested controls')
    shared['command'] = [argument for argument in command if not argument.startswith('--vsync=')]
    analysis = json.loads((path / 'quiet-analysis.json').read_text())
    run.update(guest_vsync=modes[0], mean_phase_ms=analysis['mean_phase_ms'],
               mean_runtime_wait_ms_by_api=analysis['mean_runtime_wait_ms_by_api'])
    return shared, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probes', type=Path, nargs=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Require a new output file')
    a, b = [inspect_timer(path.resolve()) for path in args.probes]
    if a[0] != b[0]:
        raise ValueError('Unmatched controls: ' + ', '.join(key for key in a[0] if a[0][key] != b[0][key]))
    if {a[1]['guest_vsync'], b[1]['guest_vsync']} != {'true', 'false'}:
        raise ValueError('Require one enabled and one disabled guest timer')
    result = dict(complete=True, shared=a[0], runs=[a[1], b[1]],
                  limits='Existing vsync flag changes guest callback cadence and packet polling policy. '
                         'Host tearing stays disabled and the game retains its 60 FPS cap. '
                         'Sequential static-scene evidence does not establish simulation, motion or campaign parity.')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({run['guest_vsync']:run['summary'] for run in result['runs']}, indent=2))


if __name__ == '__main__':
    main()
