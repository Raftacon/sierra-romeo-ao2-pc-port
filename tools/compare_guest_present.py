"""Compare completed same-build presentation-interval experiments.

Both sessions must preserve source files, retain PC VSync/60 FPS, and have
matching launch settings apart from the explicit immediate-interval flag.
Single sequential pairs do not prove repeatability or original-console fidelity.
"""
import argparse
import json
from pathlib import Path
import re


def load(folder, enabled):
    read = lambda name: json.loads((folder / name).read_text())
    native, session = read('probe.json'), read('session-pacing.json')
    timing, telemetry = read('visit-00-analysis.json'), read('visit-00-gpu-telemetry-analysis.json')
    if (not session['complete'] or len(session['visits']) != 1 or native['timed_out']
            or native['exit_code_before_cleanup'] != 0 or not timing['complete']
            or not timing['foreground_verified'] or not telemetry['complete']
            or not session['source_profile_unchanged'] or not session['retail_checkpoints_unchanged']):
        raise ValueError('Require completed, focused, single-visit probes with unchanged sources')
    visit = session['visits'][0]
    if (visit['menus_before'] or visit['menus_after'] or visit['rotation_before'] != visit['rotation_after']
            or not visit['pause_after'] or any(x.strip() != 'None' for x in visit['pause_after'])):
        raise ValueError('Scene state changed during measurement')
    command = native['command']
    flag = '--aot_immediate_guest_present=' + str(enabled).lower()
    if command.count(flag) != 1 or '--vsync=true' not in command or '--aot_fps=60' not in command:
        raise ValueError('Unexpected interval experiment, VSync or cap')
    runtime = (folder / 'runtime.log').read_text()
    if 'PC initial presentation: vsync=true, allow_tearing=false' not in runtime:
        raise ValueError('Runtime did not confirm the PC VSync / no-tearing policy')
    observed = re.findall(r'PC present interval: retail=(\d+), effective=(\d+), target_fps=(\d+), immediate_experiment=(true|false)', runtime)
    if not observed or any(int(target) != 60 or mode != str(enabled).lower()
                           or int(effective) != (0 if enabled else min(int(retail), 1))
                           for retail, effective, target, mode in observed):
        raise ValueError('Runtime did not confirm the requested interval policy')
    canonical = [item.replace(str(folder.resolve()) + '-profile', '<PROFILE>')
                     .replace(str(folder.resolve()), '<RUN>')
                 for item in command if not item.startswith('--aot_immediate_guest_present=')]
    return dict(path=str(folder.resolve()), native=native, session=session, canonical=canonical,
                visit=visit, timing=timing, telemetry=telemetry, observed=observed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('control', type=Path)
    parser.add_argument('enabled', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Require a new report path')
    a, b = load(args.control, False), load(args.enabled, True)
    if (a['canonical'] != b['canonical']
            or a['native']['executable_sha256'] != b['native']['executable_sha256']
            or a['native']['gpu_plugin'] != b['native']['gpu_plugin']
            or a['session']['source_profile_before'] != b['session']['source_profile_before']
            or a['session']['checkpoint_metadata'] != b['session']['checkpoint_metadata']
            or a['visit']['rotation_before'] != b['visit']['rotation_before']):
        raise ValueError('Binary, settings, source profile or view mismatch')
    keys = ('frames', 'covered_seconds', 'mean_ms', 'p95_ms', 'p99_ms', 'maximum_ms', 'over_25_ms', 'process_resources')
    records = {name: dict(path=row['path'], timing={key: row['timing'][key] for key in keys},
                         observed_intervals=row['observed']) for name, row in [('control', a), ('enabled', b)]}
    result = dict(complete=True, runs=records, executable_sha256=a['native']['executable_sha256'],
                  gpu_plugin=a['native']['gpu_plugin'], normalized_command=a['canonical'],
                  mean_difference_ms=b['timing']['mean_ms'] - a['timing']['mean_ms'],
                  p99_difference_ms=b['timing']['p99_ms'] - a['timing']['p99_ms'], limits=__doc__)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'normalized_command'}, indent=2))


if __name__ == '__main__':
    main()
