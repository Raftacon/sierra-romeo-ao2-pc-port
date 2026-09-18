"""Attribute a completed menu probe's synchronized diagnostic window.

Host presents are not distinct game frames. ETW GPU metrics and device-wide
telemetry are observations, not proof of the cause of a guest-frame stall.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import statistics
from analyze_quiet_presentation import summary
from gpu_telemetry import FIELDS


def analyze(p):
    scenario=json.loads((p/'menu-pacing.json').read_text())
    native=json.loads((p/'probe.json').read_text())
    if not scenario['complete'] or scenario.get('presentmon_exit_code')!=0 or native['timed_out'] or native['exit_code_before_cleanup']!=0:
        raise ValueError('Require a completed menu probe and normal native/monitor exits')
    window=scenario['window'];start,end=window['start_ms'],window['end_ms']
    if end-start<20000 or any(s['foreground_pid']!=native['pid'] for s in window['foreground']):
        raise ValueError('Require foreground menu coverage')
    with (p/'presents.csv').open(newline='',encoding='utf-8-sig') as f:raw=list(csv.DictReader(f))
    if not raw or any(int(r['ProcessID'])!=native['pid'] for r in raw):raise ValueError('Unexpected presentation process')
    presents=[r for r in raw if start+100<=float(r['CPUStartQPCTime'])<=end-100]
    if len(presents)<100 or float(presents[-1]['CPUStartQPCTime'])-float(presents[0]['CPUStartQPCTime'])<19000:
        raise ValueError('Incomplete presentation coverage')
    groups={}
    for address in sorted({r['SwapChainAddress'] for r in presents}):
        rows=[r for r in presents if r['SwapChainAddress']==address]
        metrics={}
        for name in ('FrameTime','CPUBusy','CPUWait','GPULatency','GPUTime','GPUBusy','GPUWait','DisplayLatency','DisplayedTime'):
            metrics[name]=summary([float(r[name]) for r in rows if r[name]!='NA'])
        groups[address]=dict(records=len(rows),metrics_ms=metrics,
            categories={k:dict(Counter(r[k] for r in rows)) for k in ('PresentMode','SyncInterval','AllowsTearing')})
    packets=[r for r in csv.DictReader((p/'gpu-waits.csv').open())
             if start<=float(r['start_clock_ms']) and float(r['end_clock_ms'])<=end]
    callbacks=[r for r in csv.DictReader((p/'gpu-vblank.csv').open())
               if start<=float(r['start_clock_ms'])<=end]
    if not callbacks or float(callbacks[-1]['start_clock_ms'])-float(callbacks[0]['start_clock_ms'])<19000:
        raise ValueError('Incomplete VBlank trace')
    telemetry=json.loads((p/'gpu-telemetry.json').read_text())
    if not telemetry['complete'] or telemetry['fields']!=FIELDS:raise ValueError('Incomplete GPU telemetry')
    a,b=telemetry['start'],telemetry['end']
    offsets=[x['wall_ms']-x['steady_ms'] for x in (a,b)]
    if abs(offsets[0]-offsets[1])>100 or a['utc_offset_seconds']!=b['utc_offset_seconds']:
        raise ValueError('Ambiguous telemetry clock')
    zone=timezone(timedelta(seconds=a['utc_offset_seconds']));offset=statistics.mean(offsets)
    device_rows=[]
    for raw in csv.reader((p/'gpu-telemetry.csv').open()):
        if len(raw)!=len(FIELDS):raise ValueError('Malformed telemetry')
        row=dict(zip(FIELDS,(v.strip() for v in raw)))
        t=datetime.strptime(row['timestamp'],'%Y/%m/%d %H:%M:%S.%f').replace(tzinfo=zone).timestamp()*1000-offset
        if start<=t<=end:device_rows.append((t,row))
    devices={}
    for uuid in {r['uuid'] for _,r in device_rows}:
        rows=[(t,r) for t,r in device_rows if r['uuid']==uuid]
        if len(rows)<18 or rows[0][0]-start>1500 or end-rows[-1][0]>1500:
            raise ValueError('Incomplete device samples')
        devices[uuid]=dict(samples=len(rows),
            graphics_clock_mhz=summary([float(r['clocks.current.graphics']) for _,r in rows]),
            utilization_percent=summary([float(r['utilization.gpu']) for _,r in rows]),
            limiting_reasons=dict(Counter(r['clocks_event_reasons.active'] for _,r in rows)))
    if not devices:raise ValueError('No GPU telemetry')
    return dict(complete=True,game=scenario['timing'],host_swap_chains=groups,
                packet_waits=dict(count=len(packets),elapsed_ms=summary([float(r['packet_ms']) for r in packets]),
                                  unmatched=sum(r['matched']!='1' for r in packets)),
                vblank_interval_ms=summary([float(r['interval_ms']) for r in callbacks]),
                devices=devices,limits=__doc__)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('probe',type=Path)
    p=parser.parse_args().probe;result=analyze(p)
    (p/'menu-attribution.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
