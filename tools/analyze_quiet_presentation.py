"""Summarize process-filtered PresentMon 2.x records inside a verified quiet window.

Host presents are not unique game frames. GPU metrics are ETW estimates and may
be less accurate with hardware scheduling; this does not attribute a guest stall.
"""
import argparse
import collections
import csv
import json
import math
from pathlib import Path
import statistics


def summary(values):
    if not values:return None
    ordered=sorted(values)
    return {'samples':len(values),'mean':statistics.mean(values),'median':statistics.median(values),
            'p95':ordered[int(.95*(len(values)-1))],'p99':ordered[int(.99*(len(values)-1))],
            'min':ordered[0],'max':ordered[-1]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe',type=Path)
    p=parser.parse_args().probe
    native=json.loads((p/'probe.json').read_text())
    scenario=json.loads((p/'presentmon-scenario.json').read_text())
    quiet=json.loads((p/'quiet-analysis.json').read_text())
    window=json.loads((p/'quiet-window.json').read_text())
    if (not scenario['complete'] or scenario['exit_code']!=0 or not quiet['complete']
        or not quiet['foreground_verified'] or not quiet['source_profile_unchanged']
        or native['timed_out'] or native['exit_code_before_cleanup']!=0):
        raise ValueError('Require completed PresentMon capture and verified quiet native run')
    with (p/'presents.csv').open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream);fields=reader.fieldnames;rows=list(reader)
    if not rows or 'CPUStartQPCTime' not in fields:raise ValueError('Require QPC-millisecond records')
    if any(int(r['ProcessID'])!=native['pid'] for r in rows):raise ValueError('Unexpected process in capture')
    low,high=window['steady_clock_start_ms']+100,window['steady_clock_end_ms']-100
    selected=[r for r in rows if low<=float(r['CPUStartQPCTime'])<=high]
    if len(selected)<100:raise ValueError('Too few host records inside quiet window')
    span=max(float(r['CPUStartQPCTime']) for r in selected)-min(float(r['CPUStartQPCTime']) for r in selected)
    if span<75000:raise ValueError('Incomplete quiet-window presentation coverage')
    groups=collections.defaultdict(list)
    for r in selected:groups[r['SwapChainAddress']].append(r)
    result={'complete':True,'pid':native['pid'],'total_records':len(rows),'quiet_records':len(selected),
            'quiet_record_span_seconds':span/1000,'csv_fields':fields,'swap_chains':[],
            'guest_frames':quiet['frames'],'guest_mean_ms':quiet['mean_ms'],
            'limits':__doc__,'metric_reference':'https://github.com/GameTechDev/PresentMon/blob/v2.5.1/README-ConsoleApplication.md'}
    for address,group in groups.items():
        times=[float(r['CPUStartQPCTime']) for r in group]
        if any(b<a for a,b in zip(times,times[1:])):raise ValueError('Host timestamps moved backward')
        item={'address':address,'records':len(group),'host_start_interval_ms':summary([b-a for a,b in zip(times,times[1:])]),
              'categories':{},'metrics':{}}
        for field in ('PresentRuntime','Runtime','PresentMode','SyncInterval','PresentFlags','AllowsTearing','IsHybridPresent','HybridPresent'):
            if field in fields:item['categories'][field]=dict(collections.Counter(r[field] for r in group))
        for field in ('FrameTime','CPUBusy','CPUWait','GPULatency','GPUTime','GPUBusy','GPUWait','DisplayLatency','DisplayedTime',
                      'MsCPUBusy','MsCPUWait','MsGPULatency','MsGPUTime','MsGPUBusy','MsGPUWait','MsInPresentAPI'):
            if field not in fields:continue
            values=[]
            for r in group:
                try:value=float(r[field])
                except ValueError:continue
                if math.isfinite(value):values.append(value)
            item['metrics'][field]={'valid':len(values),'missing':len(group)-len(values),'ms':summary(values)}
        result['swap_chains'].append(item)
    (p/'quiet-presentation-analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
