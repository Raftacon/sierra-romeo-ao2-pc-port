"""Repeat fresh main-menu sessions with owned profiles and GPU/presentation traces.

This targets the startup slowdown seen before campaign loading. Menu timing does
not establish campaign performance; tracing is enabled in every measured launch.
"""
import argparse
import csv
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
from gpu_telemetry import GpuTelemetry
from probe import capture, game_windows


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def analyze(out, window):
    log=(out/'runtime.log').read_text()
    origins=re.findall(r'Wait diagnostic steady-clock origin_ms=([0-9.]+)',log)
    if len(origins)!=1:raise ValueError('Require unique trace clock origin')
    phases={int(r['frame']):r for r in csv.DictReader((out/'phases.csv').open())}
    waits={}
    for row in csv.DictReader((out/'waits.csv').open()):waits.setdefault(int(row['frame']),row)
    elapsed=0;frames=[];offsets=[]
    for row in csv.DictReader((out/'frames.csv').open()):
        n=int(row['frame']);v=float(row['interval_ms']);elapsed+=v
        frames.append((n,v,elapsed))
        if n in waits and n in phases:
            offsets.append(float(origins[0])+float(waits[n]['end_ms'])+
                           float(phases[n]['commands_ms'])+float(phases[n]['pacing_ms'])-elapsed)
    if len(offsets)<100:raise ValueError('Insufficient frame-clock calibration')
    shift=statistics.median(offsets);error=max(abs(x-shift) for x in offsets)
    if error>5:raise ValueError('Inconsistent frame-clock calibration')
    values=[v for _,v,t in frames if window['start_ms']+10<=shift+t-v and shift+t<=window['end_ms']-10]
    if sum(values)<19000:raise ValueError('Incomplete menu-frame coverage')
    ordered=sorted(values)
    return dict(frames=len(values),covered_seconds=sum(values)/1000,
                mean_ms=statistics.mean(values),median_ms=statistics.median(values),
                p95_ms=ordered[int(len(values)*.95)],p99_ms=ordered[int(len(values)*.99)],
                over_40_ms=sum(v>40 for v in values),clock_error_ms=error)


def run(root,out,source,cache,monitor):
    profile=out.with_name(out.name+'-profile');shutil.copytree(source,profile)
    script=out.with_name(out.name+'-input.script')
    script.write_text('22000 200 0010 0 0 0 0 0 0\n')
    env={k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env['AOT_INPUT_SCRIPT']=str(script)
    for key,name in [('FRAME_LOG','frames.csv'),('FRAME_PHASE_LOG','phases.csv'),
                     ('WAIT_LOG','waits.csv'),('RENDER_WAIT_LOG','render-waits.csv'),
                     ('GPU_WAIT_LOG','gpu-waits.csv'),('GPU_VBLANK_LOG','gpu-vblank.csv')]:
        env['AOT_'+key]=str(out/name)
    command=[sys.executable,str(root/'tools/probe.py'),'--output',str(out),
             '--user-data',str(profile),'--cache-root',str(cache),'--gpu-plugin','spatial',
             '--seconds','120','--capture-interval','0','--log-level','info','--use-saved-settings',
             '--','--input_backend=xinput','--aot_keyboard_mouse=false',
             '--fullscreen=false','--window_width=1920','--window_height=1080',
             '--vsync=true','--aot_fps=60','--resolution_scale=1','--aot_spatial_upscale=true',
             '--swap_post_effect=fxaa_extreme','--readback_resolve=fast',
             '--d3d12_allow_variable_refresh_rate_and_tearing=false']
    child=subprocess.Popen(command,env=env,stdout=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    pid=None;present=None;report=dict(complete=False,command=command);present_log=None
    def alive():
        if child.poll() is not None:raise RuntimeError('Native helper exited early')
    def shot(name):
        windows=game_windows(pid)
        if len(windows)!=1:raise RuntimeError('Missing owned game window')
        hwnd,_,width,height=windows[0]
        if not capture(hwnd,width,height,out/name):raise RuntimeError('Menu screenshot failed')
    try:
        deadline=time.perf_counter()+70
        while True:
            alive()
            if (out/'running.json').exists():pid=json.loads((out/'running.json').read_text())['pid']
            log=(out/'runtime.log').read_text(errors='replace') if (out/'runtime.log').exists() else ''
            if pid and 'PC menu state: main_menu=true' in log:break
            if time.perf_counter()>deadline:raise RuntimeError('Main menu did not arrive')
            time.sleep(.1)
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),str(pid),'--focus','--seconds','.1'],
                       check=True,timeout=10,stdout=subprocess.DEVNULL)
        session='AotMenu-%d-%d'%(pid,time.time_ns())
        present_command=[str(monitor),'--process_id',str(pid),'--timed','40','--terminate_after_timed',
                         '--terminate_on_proc_exit','--session_name',session,'--no_console_stats',
                         '--no_track_input','--v2_metrics','--qpc_time_ms','--track_hybrid_present',
                         '--output_file',str(out/'presents.csv')]
        present_log=(out/'presentmon.log').open('w')
        present=subprocess.Popen(present_command,stdout=present_log,stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        report['presentmon_command']=present_command
        with GpuTelemetry(out) as telemetry:
            time.sleep(3);alive();shot('menu-before.png');time.sleep(1)
            user=c.WinDLL('user32');user.GetForegroundWindow.restype=w.HWND
            user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
            samples=[];start=time.perf_counter()*1000
            while time.perf_counter()*1000-start<20000:
                alive();telemetry.check()
                if present.poll() is not None:raise RuntimeError('PresentMon exited during measurement')
                owner=w.DWORD();user.GetWindowThreadProcessId(user.GetForegroundWindow(),c.byref(owner))
                samples.append(dict(steady_ms=time.perf_counter()*1000,foreground_pid=owner.value))
                time.sleep(.2)
            window=dict(start_ms=start,end_ms=time.perf_counter()*1000,pid=pid,foreground=samples)
            report['window']=window
            if any(s['foreground_pid']!=pid for s in samples):raise RuntimeError('Game lost foreground')
            gaps=[b['steady_ms']-a['steady_ms'] for a,b in zip(samples,samples[1:])]
            if not gaps or max(gaps)>1000 or window['end_ms']-samples[-1]['steady_ms']>1000:
                raise RuntimeError('Incomplete foreground observation')
            shot('menu-after.png')
            for hwnd,_,_,_ in game_windows(pid):user.PostMessageW(w.HWND(hwnd),0x0010,0,0)
            child.wait(timeout=15)
        # Process-exit detection may be unavailable without elevation. Allow
        # the owned 40-second timer and ETW drain to finish in that case.
        if present.wait(timeout=30)!=0:raise RuntimeError('PresentMon failed')
        native=json.loads((out/'probe.json').read_text())
        if child.returncode or native['timed_out'] or native['exit_code_before_cleanup']!=0:
            raise RuntimeError('Native process did not exit normally')
        states=re.findall(r'PC menu state: main_menu=(true|false)',(out/'runtime.log').read_text())
        if not states or 'true' not in states or 'false' in states[states.index('true'):]:
            raise RuntimeError('Main menu did not remain active')
        report.update(timing=analyze(out,window),executable_sha256=native['executable_sha256'],
                      gpu_plugin=native['gpu_plugin'],complete=True)
    finally:
        if child.poll() is None and pid:
            for hwnd,_,_,_ in game_windows(pid):c.windll.user32.PostMessageW(w.HWND(hwnd),0x0010,0,0)
        child.wait(timeout=130)
        if present is not None and present.poll() is None:present.wait(timeout=45)
        if present is not None:report['presentmon_exit_code']=present.returncode
        if present_log:present_log.close()
        if out.exists():(out/'menu-pacing.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--cache-root',type=Path,required=True)
    parser.add_argument('--runs',type=int,choices=range(1,9),default=3)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    out=args.output.resolve();source=args.profile.resolve()
    if out.exists() or not source.is_dir() or source in out.parents or out in source.parents:
        parser.error('Require new separate output and an existing source profile')
    monitor=root/'.tools/presentmon/PresentMon-2.5.1-x64.exe'
    if hashlib.sha256(monitor.read_bytes()).hexdigest()!='9bec3083069f58f911e6a512f4806db51a27bd096103087bc1d05ef54c80a191':
        raise ValueError('Require pinned PresentMon 2.5.1')
    original=hashes(source);out.mkdir(parents=True)
    report=dict(complete=False,source_before=original,runs=[],limits=__doc__)
    try:
        for i in range(args.runs):
            result=run(root,out/('run-%02d'%i),source,args.cache_root.resolve(),monitor)
            report['runs'].append(result)
            print(json.dumps(dict(run=i,timing=result['timing'])),flush=True)
            if result['timing']['mean_ms']>40:break
        report['complete']=True
    finally:
        report['source_unchanged']=original==hashes(source)
        (out/'menu-series.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['source_unchanged']:raise RuntimeError('Source profile changed')


if __name__=='__main__':main()
