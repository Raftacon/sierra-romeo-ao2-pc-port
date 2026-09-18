"""Summarize completed-snapshot counters from a normally finished native probe."""
import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    native = json.loads((args.probe / 'probe.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup'] != 0:
        raise ValueError('Require normal native completion')
    if '--aot_completed_resolve_readback=true' not in native['command']:
        raise ValueError('Require explicit completed-snapshot selection')
    fields = ('frame', 'copies', 'bytes', 'held', 'initial_waits', 'progress_waits', 'max_source_age')
    pattern = 'Completed resolve readback: ' + ', '.join(k + r'=(\d+)' for k in fields)
    log = (args.probe / 'runtime.log').read_text(errors='replace')
    rows = [dict(zip(fields, map(int, values))) for values in re.findall(pattern, log)]
    if not rows or any(b['frame'] <= a['frame'] for a, b in zip(rows, rows[1:])):
        raise ValueError('Missing or unordered readback counters')
    tail = rows[-6:]
    timing_matches = re.findall(r'Completed resolve timings: frame=(\d+), fence_polls=(\d+), '
                               r'fence_ms=([\d.eE+-]+), copy_ms=([\d.eE+-]+), wait_ms=([\d.eE+-]+)', log)
    timings = [dict(frame=int(v[0]), fence_polls=int(v[1]), fence_ms=float(v[2]),
                    copy_ms=float(v[3]), wait_ms=float(v[4])) for v in timing_matches]
    timing_summary = None
    if timings:
        if [r['frame'] for r in timings] != [r['frame'] for r in rows]:
            raise ValueError('Timing and counter intervals do not match')
        timed_tail = timings[-len(tail):]
        frames = tail[-1]['frame'] - (rows[-7]['frame'] if len(rows) > 6 else 0)
        totals = {k: sum(r[k] for r in timed_tail) for k in ('fence_polls', 'fence_ms', 'copy_ms', 'wait_ms')}
        timing_summary = dict(gpu_frames=frames, totals=totals,
                              per_gpu_frame={k: v / frames for k, v in totals.items()})
    result = dict(complete=True, executable_sha256=native['executable_sha256'],
                  gpu_plugin=native['gpu_plugin'], intervals=len(rows), rows=rows,
                  final_intervals=tail,
                  final_totals={key: sum(r[key] for r in tail) for key in fields[1:-1]},
                  final_max_source_age=max(r['max_source_age'] for r in tail),
                  timings=timings, final_timing_summary=timing_summary,
                  limits='Aggregate GPU-frame intervals, not exact game-frame timing. '
                         'The last incomplete interval is not logged. '
                         'Counters do not prove pixel parity or cover every memory consumer.')
    (args.probe / 'completed-readback-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('rows', 'final_intervals', 'timings')}, indent=2))


if __name__ == '__main__':
    main()
