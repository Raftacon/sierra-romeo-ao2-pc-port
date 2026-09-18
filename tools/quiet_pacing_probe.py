"""Collect frame phases after verified travel, with no captures in a quiet tail.

The tail begins after the last setup capture and ends before the final capture.
Frame/wait tracing and command-file polling remain active; this is attribution,
not an uninstrumented benchmark or proof of smooth motion.
"""
import argparse
from contextlib import ExitStack
import ctypes as c
from ctypes import wintypes as w
import json
import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from process_resources import ProcessResources
from gpu_telemetry import GpuTelemetry


def observe_quiet_tail(out,alive,presentmon=False):
    deadline=time.perf_counter()+330
    while not (out/'after-travel-090.png').is_file():
        alive()
        if time.perf_counter()>deadline:raise RuntimeError('Final setup capture did not arrive')
        time.sleep(.2)
    # Allow the final setup capture to finish. There is no checkpoint query at
    # +90s in this single-destination route and no more captures before +210s.
    time.sleep(5);alive()
    pid=json.loads((out/'running.json').read_text())['pid']
    if not presentmon:return sample_quiet_window(out,pid,alive)
    monitor=Path(__file__).resolve().parents[1]/'.tools/presentmon/PresentMon-2.5.1-x64.exe'
    digest=hashlib.sha256(monitor.read_bytes()).hexdigest()
    if digest!='9bec3083069f58f911e6a512f4806db51a27bd096103087bc1d05ef54c80a191':
        raise ValueError('Require pinned PresentMon 2.5.1')
    session='AotQuietPresentation-%d-%d'%(pid,time.time_ns())
    command=[str(monitor),'--process_id',str(pid),'--timed','95',
        '--terminate_after_timed','--terminate_on_proc_exit','--session_name',session,
        '--no_console_stats','--no_track_input','--v2_metrics','--qpc_time_ms',
        '--track_hybrid_present','--output_file',str(out/'presents.csv')]
    report={'command':command,'sha256':digest,'complete':False}
    with (out/'presentmon.log').open('x') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(2);alive()
            if process.poll() is not None:raise RuntimeError('PresentMon exited before measurement')
            result=sample_quiet_window(out,pid,alive)
            process.wait(timeout=25)
            if process.returncode or not (out/'presents.csv').is_file():
                raise RuntimeError('PresentMon capture failed')
            report['complete']=True
            return result
        finally:
            if process.poll() is None:
                # Stop only the uniquely named session owned by this helper.
                stop=subprocess.run([str(monitor),'--session_name',session,'--terminate_existing_session'],
                    stdout=log,stderr=subprocess.STDOUT,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                report['cleanup_exit_code']=stop.returncode
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
            report['exit_code']=process.returncode
            (out/'presentmon-scenario.json').write_text(json.dumps(report,indent=2)+'\n')


def sample_quiet_window(out,pid,alive,window_name='quiet-window.json'):
    subprocess.run([sys.executable,str(Path(__file__).with_name('pc_input_probe.py')),
        str(pid),'--focus','--seconds','.1'],check=True,timeout=10,stdout=subprocess.DEVNULL)
    runtime=out/'runtime.log';offset=runtime.stat().st_size
    with (out/'game.commands').open('a') as f:f.write('getall WorldInfo Pauser\n')
    deadline=time.perf_counter()+8
    while True:
        alive()
        with runtime.open('rb') as f:f.seek(offset);tail=f.read().decode(errors='replace')
        pause=re.findall(r'\.Pauser = ([^\r\n]+)',tail)
        if pause:break
        if time.perf_counter()>deadline:raise RuntimeError('Pause state not returned')
        time.sleep(.1)
    if any(value.strip()!='None' for value in pause):raise RuntimeError('Quiet scene is paused')
    user=c.WinDLL('user32',use_last_error=True)
    user.GetForegroundWindow.restype=w.HWND
    user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
    focus=[];resources=[]
    start=time.perf_counter()*1000
    with ProcessResources(pid) as process:
        while time.perf_counter()*1000-start<80000:
            alive()
            if (out/'observation-end.png').exists():raise RuntimeError('Quiet window overlaps final capture')
            owner=w.DWORD()
            user.GetWindowThreadProcessId(user.GetForegroundWindow(),c.byref(owner))
            seconds=(time.perf_counter()*1000-start)/1000
            focus.append({'seconds':seconds,'foreground_pid':owner.value})
            if not resources or seconds-resources[-1]['seconds']>=1:
                resources.append({'seconds':seconds,**process.sample()})
            time.sleep(.2)
    end=time.perf_counter()*1000
    if (out/'observation-end.png').exists():raise RuntimeError('Quiet window overlaps final capture')
    report={'steady_clock_start_ms':start,'steady_clock_end_ms':end,
        'pause':pause,'game_pid':pid,'foreground_samples':focus,'process_resources':resources,
        'limits':'QPC interval recorded after setup captures; tracing and file polling remain active.'}
    (out/window_name).write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--cache-root',type=Path,required=True)
    parser.add_argument('--checkpoint',default='01_02')
    parser.add_argument('--fullscreen',action='store_true',help='Match the normal fullscreen presentation')
    parser.add_argument('--guest-vsync',action=argparse.BooleanOptionalAction,default=True,
                        help='Diagnostic guest refresh timer; host tearing remains disabled in this helper')
    parser.add_argument('--log-level',choices=('info','debug'),default='info')
    parser.add_argument('--gpu-timing',action='store_true',help='Also trace GPU packet sleeps and VBlank callbacks')
    parser.add_argument('--presentmon',action='store_true',help='Capture process-filtered PresentMon 2.x CPU/GPU/presentation metrics around the quiet interval')
    parser.add_argument('--no-pc-overlays',action='store_true',help='Attribution only: omit PC dialogs/cursor policy to measure persistent UI repaint cost')
    parser.add_argument('--nvidia-telemetry',action='store_true',help='Record device-wide GPU clocks, load, temperature and power once per second')
    parser.add_argument('--timer-resolution-ms',type=int,choices=[1],help='Opt-in process timer-resolution attribution experiment')
    parser.add_argument('--vblank-wake',action='store_true',help='Enable the experimental callback-driven packet wake')
    parser.add_argument('--immediate-guest-present',action=argparse.BooleanOptionalAction,default=None,
                        help='Compare retail zero-interval presentation while retaining the host cap and display policy')
    parser.add_argument('--source-snapshots',action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument('--automatic-projection',action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument('--projection',action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument('--projection-fma',action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument('--zero-stencil',action=argparse.BooleanOptionalAction,default=None,
                        help='Track proven-zero stencil transfers; false observes only, true omits their bit writes')
    parser.add_argument('--hud-text-contrast',action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument('--batch-occlusion',action=argparse.BooleanOptionalAction,default=None,
                        help='Compare fence-safe batching with synchronous real visibility results')
    parser.add_argument('--post-effect',choices=('fxaa','fxaa_extreme','none'),default='fxaa')
    parser.add_argument('--readback',choices=('fast','full','none'),default='fast',
                        help='Controlled resolve-readback comparison; none may break rendering correctness')
    parser.add_argument('--completed-readback',action=argparse.BooleanOptionalAction,default=None,
                        help='Compare the fence-safe completed-snapshot experiment; includes sparse counters')
    parser.add_argument('--render-target-path',choices=('rtv','rov'),help='Explicit existing renderer path for a controlled comparison')
    parser.add_argument('--gpu-vblank-watch',type=lambda s:int(s,0),help='Aligned physical word for the optional callback trace')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];out=args.output.resolve()
    if out.exists() or not args.profile.is_dir():parser.error('Require new output and existing source profile')
    if args.gpu_vblank_watch is not None and (not args.gpu_timing or not 0<=args.gpu_vblank_watch<=0x1FFFFFFC or args.gpu_vblank_watch%4):
        parser.error('Watch requires GPU tracing and an aligned physical address below 512 MiB')
    env={k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_FRAME_PHASE_LOG=str(out/'phases.csv'),AOT_WAIT_LOG=str(out/'waits.csv'),
               AOT_RENDER_WAIT_LOG=str(out/'render-waits.csv'))
    if args.no_pc_overlays:env['AOT_PERF_NO_PC_OVERLAYS']='1'
    if args.gpu_timing:
        env.update(AOT_GPU_WAIT_LOG=str(out/'gpu-waits.csv'),AOT_GPU_VBLANK_LOG=str(out/'gpu-vblank.csv'))
    if args.timer_resolution_ms:env['AOT_TIMER_RESOLUTION_MS']=str(args.timer_resolution_ms)
    if args.gpu_vblank_watch is not None:env['AOT_GPU_VBLANK_WATCH']=hex(args.gpu_vblank_watch)
    command=[sys.executable,str(root/'tools/checkpoint_probe.py'),'--output',str(out),
        '--profile',str(args.profile.resolve()),'--cache-root',str(args.cache_root.resolve()),
        '--checkpoint',args.checkpoint,'--verify-checkpoint','--controller-only',
        '--gpu-plugin','spatial','--window','1920','1080','--capture-interval','0',
        '--observe-seconds','210','--log-level',args.log_level,'--post-effect',args.post_effect,
        '--readback',args.readback]
    if args.fullscreen:command+=['--fullscreen']
    command+=['--guest-vsync' if args.guest_vsync else '--no-guest-vsync']
    command+=['--extra','--aot_gpu_vblank_wake='+str(args.vblank_wake).lower()]
    command+=['--d3d12_allow_variable_refresh_rate_and_tearing=false']
    command+=['--aot_spatial_upscale=true']
    if args.completed_readback is not None:
        command+=['--aot_completed_resolve_readback='+str(args.completed_readback).lower(),
                  '--aot_trace_completed_readback=true']
    if args.render_target_path:command+=['--render_target_path_d3d12='+args.render_target_path]
    if args.zero_stencil is not None:
        command+=['--aot_zero_stencil_transfers='+str(args.zero_stencil).lower(),
                  '--aot_trace_zero_stencil=true']
    for name,value in (('aot_projection_source_snapshots',args.source_snapshots),
                       ('aot_immediate_guest_present',args.immediate_guest_present),
                       ('aot_automatic_projection_precision',args.automatic_projection),
                       ('aot_projection_precision',args.projection),
                       ('aot_projection_fma',args.projection_fma),
                       ('aot_batch_occlusion_queries',args.batch_occlusion),
                       ('aot_hud_text_contrast',args.hud_text_contrast)):
        if value is not None:command+=['--'+name+'='+str(value).lower()]
    origin=time.perf_counter()*1000
    child=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    telemetry=None
    def alive():
        if child.poll() is not None:raise RuntimeError('Quiet probe ended early')
        if telemetry is not None:telemetry.check()
    with ExitStack() as monitors:
        try:
            if args.nvidia_telemetry:
                deadline=time.perf_counter()+30
                while not out.is_dir():
                    alive()
                    if time.perf_counter()>deadline:raise RuntimeError('Probe output directory did not arrive')
                    time.sleep(.1)
                telemetry=monitors.enter_context(GpuTelemetry(out))
            window=observe_quiet_tail(out,alive,args.presentmon)
        finally:
            child.wait(timeout=380)
        if telemetry is not None:telemetry.check()
    if child.returncode:raise RuntimeError('Checkpoint probe failed')
    native=json.loads((out/'probe.json').read_text());travel=json.loads((out/'travel.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup']!=0 or native['captures']:
        raise RuntimeError('Require normal close and no periodic captures')
    if not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']:
        raise RuntimeError('Source files changed')
    setup=next(e['seconds'] for e in travel['events'] if e.get('capture')=='after-travel-090.png')
    end=next(e['seconds'] for e in travel['events'] if e.get('capture')=='observation-end.png')
    if end-setup<110:raise RuntimeError('Short quiet tail')
    report={'complete':True,'steady_clock_origin_ms':origin,'command':command,
        'no_pc_overlays':args.no_pc_overlays,
        'nvidia_telemetry':args.nvidia_telemetry,
        'quiet_tail_checkpoint_clock_seconds':[setup+5,end-5],'window':window,
        'source_profile_unchanged':True,'retail_checkpoints_unchanged':True,
        'limits':__doc__}
    (out/'quiet-pacing.json').write_text(json.dumps(report,indent=2)+'\n')
    print(out)


if __name__=='__main__':main()
