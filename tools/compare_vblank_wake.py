"""Compare verified quiet captures and callback-to-packet return delays."""
import argparse
import bisect
import csv
import json
from pathlib import Path
import statistics


def analyze(path):
    quiet=json.loads((path/'quiet-analysis.json').read_text())
    native=json.loads((path/'probe.json').read_text())
    window=json.loads((path/'quiet-window.json').read_text())
    vblank=json.loads((path/'gpu-vblank-analysis.json').read_text())
    if not quiet['complete'] or native['timed_out'] or native['exit_code_before_cleanup']!=0:
        raise ValueError('Require completed quiet native run')
    if not vblank['watch_matches_packet_memory'] or vblank['read_failures']:
        raise ValueError('Watch does not match the actual packet address')
    low,high=window['steady_clock_start_ms'],window['steady_clock_end_ms']
    callbacks=[r for r in csv.DictReader((path/'gpu-vblank.csv').open())
        if int(r['before_ok']) and int(r['after_ok']) and int(r['before_raw'])!=0 and int(r['after_raw'])==0]
    ends=[float(r['end_clock_ms']) for r in callbacks]
    delays=[];unmatched=0
    for r in csv.DictReader((path/'gpu-waits.csv').open()):
        start,end=float(r['start_clock_ms']),float(r['end_clock_ms'])
        if not low<=start<=end<=high or not int(r['sleep_calls']) or not int(r['matched']):continue
        at=bisect.bisect_right(ends,end)-1
        if at>=0 and start<=float(callbacks[at]['start_clock_ms']):delays.append(end-ends[at])
        else:unmatched+=1
    if not delays:raise ValueError('No callback-to-return samples')
    ordered=sorted(delays)
    return {'path':str(path),'quiet':{k:v for k,v in quiet.items() if k!='slow_frames'},
        'matched_delays':len(delays),'unmatched_sleeping_packets':unmatched,
        'return_delay_mean_ms':statistics.mean(delays),'return_delay_median_ms':statistics.median(delays),
        'return_delay_p99_ms':ordered[int(len(ordered)*.99)],'return_delay_max_ms':max(delays)},native


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('control',type=Path);p.add_argument('experiment',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Require new output')
    control,native0=analyze(a.control);experiment,native1=analyze(a.experiment)
    for key in ('executable_sha256','gpu_plugin','xex_sha256'):
        if native0[key]!=native1[key]:raise ValueError('Binary provenance differs: '+key)
    def normalized(native):
        return [v for v in native['command'] if not v.startswith(('--user_data_root=','--log_file=','--aot_gpu_vblank_wake='))]
    if normalized(native0)!=normalized(native1):raise ValueError('Other runtime options differ')
    for native,value in ((native0,'false'),(native1,'true')):
        if [v for v in native['command'] if v.startswith('--aot_gpu_vblank_wake=')]!=['--aot_gpu_vblank_wake='+value]:
            raise ValueError('Expected explicit off/on comparison')
    report={'complete':True,'control':control,'experiment':experiment,
        'limits':'One sequential pair, not campaign-wide performance proof. Callback writes are concurrent observations. A shorter return delay alone does not establish better game pacing.'}
    a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
