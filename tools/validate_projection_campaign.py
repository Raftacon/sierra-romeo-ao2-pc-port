"""Check a completed precision-enabled checkpoint transition and its live draw modes."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    path = args.probe
    output = path / 'projection-campaign-validation.json'
    if output.exists(): parser.error('Validation report already exists')
    probe = json.loads((path / 'probe.json').read_text())
    travel = json.loads((path / 'travel.json').read_text())
    if probe.get('timed_out') or probe.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    if not travel.get('retail_checkpoints_unchanged') or not travel.get('source_profile_unchanged'):
        raise ValueError('Retail checkpoint or source profile changed')
    expected = set(travel['checkpoint_metadata'])
    verified = {e['checkpoint_reference_verified'] for e in travel['events'] if 'checkpoint_reference_verified' in e}
    if len(expected) != 2 or verified != expected: raise ValueError('Require two verified destinations')
    commands = probe['command']
    for flag in ['--gpu_plugin=spatial', '--resolution_scale=1']:
        if flag not in commands: raise ValueError('Unexpected runtime configuration')
    if '--aot_projection_precision=false' in commands:
        raise ValueError('Precision was explicitly disabled')
    log = (path / 'runtime.log').read_text(errors='replace')
    if re.search(r'\[(?:error|critical)\]|DEVICE_(?:HUNG|REMOVED)', log):
        raise ValueError('Runtime reports an error or device failure')
    if 'requested=true, supported=true, enabled=true' not in log:
        raise ValueError('Precision was not enabled on supported hardware')
    applied = re.findall(r'projection precision applied: guest=([0-9A-F]+), modification=([0-9A-F]+)', log)
    expected_variants = {('480333F4AFCF0E1E','0000000000000000'),
                         ('480333F4AFCF0E1E','000000000000007F'),('D6E05D80EF7DEBF8','0000000000000000')}
    if set(applied) != expected_variants: raise ValueError('Unexpected translated variants')
    draws_path = path / 'draws.csv'
    rows = list(csv.DictReader(draws_path.open()))
    if not rows or len({r['gpu_frame'] for r in rows}) != 1:
        raise ValueError('Require one completed diagnostic frame')
    if [int(r['draw']) for r in rows] != list(range(len(rows))): raise ValueError('Noncontiguous draw records')
    completed = f"AOT draw capture complete: gpu_frame={rows[0]['gpu_frame']}, draws={len(rows)}"
    if completed not in log: raise ValueError('Missing draw capture completion')
    constants = path / 'draws.csv.constants.bin'
    if constants.stat().st_size != len(rows)*8192: raise ValueError('Incomplete float constants')
    modes = []
    for guest in ['480333f4afcf0e1e', 'd6e05d80ef7debf8']:
        selected = [r for r in rows if r['vs_hash'].lower() == guest]
        if not selected: raise ValueError('Target shader was compiled but not drawn')
        # PA_CL_VTE_CNTL bits 8,9,10 map to system flag bits 1,2,3
        # in the pinned D3D12CommandProcessor::UpdateSystemConstantValues.
        flags = Counter((int(r['vte_control']) >> 7) & 14 for r in selected)
        if set(flags) != {8}: raise ValueError('Observed alternate position mode: ' + str(flags))
        for row in selected:
            if not all(math.isfinite(float(row[k])) for k in ('ndc_scale_x','ndc_scale_y','ndc_scale_z',
                                                             'ndc_offset_x','ndc_offset_y','ndc_offset_z')):
                raise ValueError('Nonfinite viewport transform')
        viewports = Counter((r['viewport_w'],r['viewport_h']) for r in selected)
        modes.append({'guest': guest, 'draws': len(selected), 'position_flags': dict(flags),
                      'viewports': [{'width': int(w), 'height': int(h), 'draws': count}
                                    for (w,h),count in viewports.items()]})
    result = {'probe': str(path.resolve()), 'executable_sha256': probe['executable_sha256'],
              'gpu_plugin_sha256': probe['gpu_plugin']['sha256'], 'exit_code': 0,
              'elapsed_seconds': probe['elapsed_seconds'], 'checkpoint_metadata': travel['checkpoint_metadata'],
              'applied': applied, 'draw_count': len(rows), 'modes': modes,
              'draws_sha256': hashlib.sha256(draws_path.read_bytes()).hexdigest(),
              'limitation': 'Live CPU draw state verifies active mode and checkpoint transitions, not pixel parity. '
                            'One captured frame in the first destination; screenshots require separate visual inspection.'}
    output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'checkpoints': sorted(verified), 'draw_count': len(rows), 'modes': modes}, indent=2))


if __name__ == '__main__':
    main()
