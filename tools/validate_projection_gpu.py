"""Run bounded synthetic GPU checks; preserve input/output hashes and negative controls.

Uses two known terrain layouts and an instance layout, with bytecode from the
translation matrix. This is not a native-scene or rasterization parity test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

SHADERS = ('42B8F7CF97B6945E', '4859844392E104EC', '6F20AB2C7E98C539')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out, exe, matrix = args.output.resolve(), args.exe.resolve(), args.matrix.resolve()
    if out.exists():
        parser.error('Require new output directory')
    out.mkdir(parents=True)
    inputs = [exe]
    programs = {}
    for guest in SHADERS:
        modes = ('original', 'original', 'precise') if guest == SHADERS[2] else ('guest-original', 'original', 'precise')
        programs[guest] = [matrix / (guest + '-000000000000001F-' + mode + '.dxbc') for mode in modes]
        inputs.extend(programs[guest])
    before = {str(path): sha(path) for path in inputs}
    report = {'complete': False, 'scope': 'synthetic finite vertices; no native scene or rasterization',
              'inputs': before, 'runs': [], 'negative_controls': []}

    def run(label, programs, expected_error=None):
        command = [str(exe), *map(str, programs), str(out / label)]
        started = time.monotonic()
        proc = subprocess.run(command, capture_output=True, text=True, timeout=45,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        (out / (label + '.log')).write_text(proc.stdout + proc.stderr)
        item = {'label': label, 'command': command, 'seconds': time.monotonic() - started,
                'returncode': proc.returncode}
        if expected_error:
            report['negative_controls'].append(item)
            if proc.returncode != 1 or expected_error not in proc.stderr:
                raise RuntimeError('Negative control did not reject the expected fault: ' + label)
            if (out / label / 'summary.json').exists():
                raise RuntimeError('Rejected run emitted a success report')
        else:
            report['runs'].append(item)
            if proc.returncode:
                raise RuntimeError('GPU check failed: ' + label + ': ' + proc.stderr)
            summary = json.loads((out / label / 'summary.json').read_text())
            if not summary['complete'] or summary['draws'] != 216 or not summary['changed_position_lanes']:
                raise RuntimeError('Incomplete GPU report: ' + label)
            item['summary'] = summary

    try:
        for guest in SHADERS:
            original, snapshot, precise = programs[guest]
            if len({sha(path) for path in programs[guest]}) != (2 if guest == SHADERS[2] else 3):
                raise RuntimeError('Unexpected number of distinct shader binaries')
            run(guest, programs[guest])
            run(guest + '-reject-uncorrected', [original, snapshot, original], 'CPU position mismatch')
            run(guest + '-reject-changed-snapshot', [original, precise, precise], 'Snapshot-only changed output')
        after = {str(path): sha(path) for path in inputs}
        report['inputs_unchanged'] = before == after
        if not report['inputs_unchanged']:
            raise RuntimeError('Diagnostic inputs changed during execution')
        report['artifacts'] = {str(path.relative_to(out)): sha(path)
                               for path in sorted(out.rglob('*')) if path.is_file()}
        report['complete'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'complete': True, 'shaders': len(report['runs']),
                      'negative_controls': len(report['negative_controls']), 'report': str(out / 'report.json')}))


if __name__ == '__main__':
    main()
