"""Check matched quiet probes before comparing resolve-readback modes."""
import argparse
import json
from pathlib import Path


def inspect(path):
    def read(name):
        return json.loads((path / name).read_text())

    native = read('probe.json')
    travel = read('travel.json')
    quiet = read('quiet-pacing.json')
    analysis = read('quiet-analysis.json')
    if (not quiet['complete'] or not analysis['complete'] or native['timed_out']
            or native['exit_code_before_cleanup'] != 0 or native['captures']):
        raise ValueError(f'Incomplete or interrupted probe: {path}')
    if not all((analysis['foreground_verified'], travel['source_profile_unchanged'],
                travel['retail_checkpoints_unchanged'], quiet['source_profile_unchanged'],
                quiet['retail_checkpoints_unchanged'])):
        raise ValueError(f'Foreground or source-preservation check failed: {path}')
    if not any(e.get('checkpoint_reference_verified') == travel['checkpoint']
               for e in travel['events']):
        raise ValueError(f'Unverified destination: {path}')
    modes = [s.partition('=')[2] for s in native['command']
             if s.startswith('--readback_resolve=')]
    if len(modes) != 1 or modes[0] not in ('none', 'fast', 'full'):
        raise ValueError(f'Ambiguous readback mode: {path}')
    completed = [s.partition('=')[2] for s in native['command']
                 if s.startswith('--aot_completed_resolve_readback=')]
    if len(completed) > 1 or (completed and completed[0] not in ('true', 'false')):
        raise ValueError(f'Ambiguous completed-snapshot mode: {path}')
    normalized = [s for s in native['command'] if not s.startswith(
        ('--user_data_root=', '--log_file=', '--readback_resolve=',
         '--aot_completed_resolve_readback='))]
    environment = {k: v.replace(str(path), 'PROBE')
                   for k, v in native['input_environment'].items()}
    shared = dict(command=normalized, environment=environment,
                  executable_sha256=native['executable_sha256'],
                  gpu_plugin=native['gpu_plugin'], xex_sha256=native['xex_sha256'],
                  source_profile_sha256=travel['source_profile_sha256'],
                  retail_checkpoint_sha256=travel['retail_checkpoint_sha256'],
                  checkpoint=travel['checkpoint'])
    shared['nvidia_telemetry'] = quiet.get('nvidia_telemetry', False)
    if shared['nvidia_telemetry']:
        telemetry = read('gpu-telemetry.json')
        gpu_analysis = read('gpu-telemetry-analysis.json')
        if not telemetry['complete'] or not gpu_analysis['complete']:
            raise ValueError('Incomplete GPU telemetry: ' + str(path))
        shared['telemetry_controls'] = dict(command=telemetry['command'],
            binary_sha256=telemetry['binary_sha256'], gpus=sorted(gpu_analysis['gpus']))
    summary = {k: analysis[k] for k in ('frames', 'covered_seconds', 'mean_ms',
               'p95_ms', 'p99_ms', 'maximum_ms', 'over_25_ms', 'process_resources')}
    return shared, dict(path=str(path), mode=modes[0],
                        completed_snapshots=completed[0] if completed else 'default', summary=summary,
                        gpu_telemetry=({gpu: {k: v for k, v in data.items() if k != 'rows'}
                                       for gpu, data in gpu_analysis['gpus'].items()}
                                      if shared['nvidia_telemetry'] else None),
                        shader_cache_before=native['shader_cache_before'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probes', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.probes) < 2 or args.output.exists():
        parser.error('Require at least two probes and a new output file')
    first = None
    runs = []
    for path in args.probes:
        shared, run = inspect(path.resolve())
        if first is None:
            first = shared
        elif shared != first:
            raise ValueError('Unmatched controls: ' + ', '.join(
                key for key in first if shared[key] != first[key]))
        runs.append(run)
    report = dict(complete=True, shared=first, runs=runs,
                  limits='Sequential static-scene attribution, not rendering parity. '
                         'Shader cache inventories are retained, not assumed equal; '
                         'quiet windows start after scene warmup. No GPU clock control.')
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({r['path']: r['summary'] for r in runs}, indent=2))


if __name__ == '__main__':
    main()
