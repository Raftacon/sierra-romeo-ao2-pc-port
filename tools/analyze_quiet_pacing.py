"""Join a quiet observation's QPC bounds to frame and wait traces."""
import argparse
import csv
import json
from pathlib import Path
import re
import statistics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe',type=Path)
    parser.add_argument('--visit',type=int,help='Analyze a visit from session_pacing_probe.py')
    args=parser.parse_args();p=args.probe
    native=json.loads((p/'probe.json').read_text())
    if args.visit is not None and not 0<=args.visit<12:parser.error('Visit must be in [0, 11]')
    prefix='quiet' if args.visit is None else 'visit-%02d'%args.visit
    scenario=json.loads((p/('quiet-pacing.json' if args.visit is None else 'session-pacing.json')).read_text())
    window=json.loads((p/(prefix+'-window.json')).read_text())
    focus=window.get('foreground_samples')
    foreground=None
    if focus is not None:
        if len(focus)<2 or not window.get('game_pid'):
            raise ValueError('Missing foreground observations or game PID')
        duration=(window['steady_clock_end_ms']-window['steady_clock_start_ms'])/1000
        gaps=[b['seconds']-a['seconds'] for a,b in zip(focus,focus[1:])]
        if focus[0]['seconds']>.5 or duration-focus[-1]['seconds']>.5 or any(g<=0 or g>1 for g in gaps):
            raise ValueError('Incomplete foreground observation coverage')
        foreground=all(sample['foreground_pid']==window['game_pid'] for sample in focus)
        if not foreground:raise ValueError('Game lost foreground during quiet observation')
        if not window.get('pause') or any(value.strip()!='None' for value in window['pause']):
            raise ValueError('Require verified unpaused scene before quiet observation')
    resources=window.get('process_resources')
    resource_summary=None
    if resources:
        first,last=resources[0],resources[-1]
        seconds=last['seconds']-first['seconds']
        if seconds<=0:raise ValueError('Short process-resource observation')
        resource_summary={'samples':len(resources),'seconds':seconds,
            'mean_cpu_cores':(last['cpu_seconds']-first['cpu_seconds'])/seconds,
            'private_bytes_first':first['private_bytes'],'private_bytes_last':last['private_bytes'],
            'private_bytes_min':min(r['private_bytes'] for r in resources),
            'private_bytes_max':max(r['private_bytes'] for r in resources),
            'working_set_bytes_first':first['working_set_bytes'],'working_set_bytes_last':last['working_set_bytes'],
            'page_faults_delta':last['page_faults']-first['page_faults']}
    if not scenario['complete'] or native['timed_out'] or native['exit_code_before_cleanup']!=0:
        raise ValueError('Require normally completed quiet probe')
    if native['captures']:raise ValueError('Periodic captures were active')
    origins=re.findall(r'Wait diagnostic steady-clock origin_ms=([\d.]+)',(p/'runtime.log').read_text(errors='replace'))
    if len(origins)!=1:raise ValueError('Require unique native QPC origin')
    origin=float(origins[0])
    phases={int(r['frame']):{k:float(v) for k,v in r.items()} for r in csv.DictReader((p/'phases.csv').open())}
    waits={}
    for r in csv.DictReader((p/'waits.csv').open()):waits.setdefault(int(r['frame']),[]).append(r)
    frames=[];elapsed=0;offsets=[]
    for r in csv.DictReader((p/'frame-times.csv').open()):
        frame=int(r['frame']);interval=float(r['interval_ms']);elapsed+=interval
        frames.append((frame,interval,elapsed))
        if frame in waits and frame in phases:
            phase=phases[frame]
            offsets.append(origin+float(waits[frame][0]['end_ms'])+phase['commands_ms']+phase['pacing_ms']-elapsed)
    if len(offsets)<100:raise ValueError('Insufficient clock calibration')
    clock=statistics.median(offsets);deviation=max(abs(o-clock) for o in offsets)
    # FinishWaitInterval timestamps just after frame-hook entry. The phase sum
    # reconstructs the paced frame sample with this small, measured discrepancy.
    if deviation>5:raise ValueError('Clock reconstruction discrepancy exceeds 5 ms: '+str(deviation))
    start,end=window['steady_clock_start_ms'],window['steady_clock_end_ms']
    selected=[r for r in frames if start+10<=clock+r[2]-r[1] and clock+r[2]<=end-10]
    if not selected or sum(r[1] for r in selected)<75000:raise ValueError('Short quiet frame coverage')
    slow=[];values=[]
    for frame,interval,elapsed in selected:
        if frame not in phases or frame-1 not in phases:raise ValueError('Missing phase row')
        values.append(interval);phase=phases[frame]
        if interval<25:continue
        components={k:phase[k] for k in ('between_hooks_ms','commands_ms','pacing_ms')}
        components.update(previous_bookkeeping_ms=phases[frame-1]['bookkeeping_ms'],
                          previous_trace_write_ms=phase['previous_trace_write_ms'])
        slow.append({'frame':frame,'interval_ms':interval,'quiet_seconds':(clock+elapsed-start)/1000,
            **components,'unaccounted_ms':interval-sum(components.values()),
            'runtime_wait_ms':sum(float(w['total_ms']) for w in waits.get(frame,[])),
            'runtime_waits':waits.get(frame,[])})
    ordered=sorted(values)
    mean_phases={key:statistics.mean(phases[frame][key] for frame,_,_ in selected)
                 for key in ('between_hooks_ms','commands_ms','pacing_ms','previous_trace_write_ms')}
    mean_phases['previous_bookkeeping_ms']=statistics.mean(
        phases[frame-1]['bookkeeping_ms'] for frame,_,_ in selected)
    cpu=[phases[frame]['between_hooks_cpu_ms'] for frame,_,_ in selected]
    mean_phases['between_hooks_cpu_ms']=statistics.mean(cpu) if all(value>=0 for value in cpu) else None
    api_totals={}
    for frame,_,_ in selected:
        for wait in waits.get(frame,[]):
            api_totals[wait['api']]=api_totals.get(wait['api'],0)+float(wait['total_ms'])
    result={'complete':True,'frames':len(values),'first_frame':selected[0][0],'last_frame':selected[-1][0],
        'covered_seconds':sum(values)/1000,'mean_ms':statistics.mean(values),
        'p95_ms':ordered[int(len(values)*.95)],'p99_ms':ordered[int(len(values)*.99)],'maximum_ms':max(values),
        'over_25_ms':len(slow),'clock_calibration_max_deviation_ms':deviation,
        'foreground_verified':foreground,
        'source_profile_unchanged':scenario.get('source_profile_unchanged'),
        'scenario_recovery':scenario.get('recovery'),
        'process_resources':resource_summary,
        'mean_phase_ms':mean_phases,
        'mean_runtime_wait_ms_by_api':{api:total/len(selected) for api,total in api_totals.items()},
        'clock_calibration_samples':len(offsets),'slow_frames':slow,
        'executable_sha256':native['executable_sha256'],'gpu_plugin':native['gpu_plugin'],
        'limits':'Static verified checkpoint. No captures or scripted movement in measured window; tracing and command polling remain active. Not a full-game benchmark.'}
    (p/(prefix+'-analysis.json')).write_text(json.dumps(result,indent=2)+'\n')
    # Existing rendering-fence analysis accepts the same slow-frame keys.
    (p/('phase-analysis.json' if args.visit is None else prefix+'-phase-analysis.json')).write_text(json.dumps({'slow_frames':slow,
        'window_game_frame_seconds':[(selected[0][2]-selected[0][1])/1000+1e-6,
                                     selected[-1][2]/1000+1e-6]},indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='slow_frames'},indent=2))


if __name__=='__main__':main()
