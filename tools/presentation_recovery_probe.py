"""Measure one verified scene before and after minimizing its owned game window.

Uses a copied profile, preserves renderer corrections, and does not claim the
intentionally minimized interval is a gameplay benchmark. Quiet windows use the
same trace analyzer as session_pacing_probe.py (--visit 0 and --visit 1).
"""
import argparse
from contextlib import ExitStack
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from gpu_telemetry import GpuTelemetry
from inspect_checkpoint import checkpoint_metadata
from probe import capture, game_windows
from quiet_pacing_probe import sample_quiet_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--cache-root', type=Path, required=True)
    a = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = a.output.resolve()
    if out.exists(): parser.error('Use a new output directory')
    env = {k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_FRAME_PHASE_LOG=str(out/'phases.csv'), AOT_WAIT_LOG=str(out/'waits.csv'),
               AOT_RENDER_WAIT_LOG=str(out/'render-waits.csv'))
    command = [sys.executable, str(root/'tools/checkpoint_probe.py'), '--output', str(out),
        '--profile', str(a.profile.resolve()), '--cache-root', str(a.cache_root.resolve()),
        '--checkpoint', '01_02', '--verify-checkpoint', '--controller-only',
        '--gpu-plugin', 'spatial', '--window', '1920', '1080', '--observe-seconds', '420',
        '--capture-interval', '0', '--post-effect', 'fxaa_extreme', '--log-level', 'info',
        '--finish-trigger', str(out/'recovery.complete'), '--extra', '--aot_spatial_upscale=true',
        '--d3d12_allow_variable_refresh_rate_and_tearing=false']
    child = subprocess.Popen(command, cwd=root, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    started = time.monotonic()
    report = dict(complete=False, command=command, visits=[], transitions=[],
                  scope='Stationary windowed scene and one minimize/restore cycle; not a combat or leak proof.')
    pid = None
    user = c.WinDLL('user32', use_last_error=True)
    user.ShowWindow.argtypes = [w.HWND, c.c_int]
    user.IsIconic.argtypes = user.IsWindowVisible.argtypes = [w.HWND]
    user.GetForegroundWindow.restype = w.HWND
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    dwm = c.WinDLL('dwmapi')
    dwm.DwmGetWindowAttribute.argtypes = [w.HWND,w.DWORD,c.c_void_p,w.DWORD]
    dwm.DwmGetWindowAttribute.restype = c.c_long
    telemetry = None

    def alive():
        if child.poll() is not None: raise RuntimeError('Owned native probe ended early')
        if telemetry is not None: telemetry.check()

    def wait(seconds):
        end = time.monotonic()+seconds
        while time.monotonic()<end: alive(); time.sleep(.2)

    def query(command, pattern):
        log = out/'runtime.log'; offset = log.stat().st_size
        with (out/'game.commands').open('a') as stream: stream.write(command+'\n')
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            alive()
            with log.open('rb') as stream:
                stream.seek(offset); tail = stream.read().decode(errors='replace')
            values = re.findall(pattern,tail)
            if values: return values
            time.sleep(.1)
        raise RuntimeError('No reply to '+command)

    def window():
        found = game_windows(pid)
        if len(found)!=1: raise RuntimeError('Expected one owned game window')
        return found[0]

    def state(hwnd):
        owner = w.DWORD(); user.GetWindowThreadProcessId(user.GetForegroundWindow(),c.byref(owner))
        cloaked = w.DWORD()
        result = dwm.DwmGetWindowAttribute(hwnd,14,c.byref(cloaked),c.sizeof(cloaked))
        if result<0: raise RuntimeError('Could not read DWM cloaking state')
        return dict(steady_clock_ms=time.perf_counter()*1000, minimized=bool(user.IsIconic(hwnd)),
            visible=bool(user.IsWindowVisible(hwnd)), cloaked=cloaked.value, foreground_pid=owner.value)

    def active_visit(index,label):
        expected = checkpoint_metadata(root/'assets/AO2Game/Checkpoints/01_02')['actor_path']
        refs = query('getall AO2CheckpointManager CurrCheckpoint',r"CurrCheckpoint = AO2Checkpoint'([^']+)'")
        if set(refs)!={expected}: raise RuntimeError('Checkpoint changed')
        rotation = query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
        win = window()
        if not capture(win[0],win[2],win[3],out/f'visit-{index:02d}-before.png'):
            raise RuntimeError('Capture failed')
        wait(3)
        states = []; last_sample = 0
        def observed_alive():
            nonlocal last_sample
            alive()
            if time.monotonic()-last_sample >= .5:
                last_sample = time.monotonic(); states.append(state(win[0]))
        measured = sample_quiet_window(out,pid,observed_alive,f'visit-{index:02d}-window.json')
        # Sampling is deadline based, not an exact integer count: a .5s check
        # inside a .2s loop normally fires every .6s. Verify actual coverage.
        start_ms,end_ms = measured['steady_clock_start_ms'],measured['steady_clock_end_ms']
        states = [s for s in states if start_ms <= s['steady_clock_ms'] <= end_ms]
        (out/f'visit-{index:02d}-visibility.json').write_text(json.dumps(states,indent=2)+'\n')
        after = query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
        pause = query('getall WorldInfo Pauser',r'\.Pauser = ([^\r\n]+)')
        if rotation!=after or any(p.strip()!='None' for p in pause):
            raise RuntimeError('Measured camera or pause state changed')
        gaps = [b['steady_clock_ms']-a['steady_clock_ms'] for a,b in zip(states,states[1:])]
        if (len(states)<2 or states[0]['steady_clock_ms']-start_ms>750 or
            end_ms-states[-1]['steady_clock_ms']>750 or any(g<=0 or g>1000 for g in gaps)):
            raise RuntimeError('Incomplete quiet visibility timeline: '+str(len(states))+' samples')
        invalid = [s for s in states if s['minimized'] or not s['visible'] or s['cloaked'] or s['foreground_pid']!=pid]
        if invalid: raise RuntimeError('Quiet visibility precondition failed: '+repr(invalid[:3]))
        if not capture(win[0],win[2],win[3],out/f'visit-{index:02d}-after.png'):
            raise RuntimeError('Capture failed')
        report['visits'].append(dict(label=label,checkpoint='01_02',checkpoint_reference=refs,
            rotation_before=rotation,rotation_after=after,pause_after=pause,window_states=states,
            steady_clock_start_ms=measured['steady_clock_start_ms'],steady_clock_end_ms=measured['steady_clock_end_ms']))
        (out/'session-progress.json').write_text(json.dumps(report,indent=2)+'\n')

    try:
        # Complete all fixed setup screenshots at travel +90 before measuring.
        wait(170)
        pid = json.loads((out/'running.json').read_text())['pid']; report['pid'] = pid
        with ExitStack() as stack:
            telemetry = stack.enter_context(GpuTelemetry(out))
            active_visit(0,'before-minimize')
            hwnd = window()[0]
            user.ShowWindow(hwnd,6)
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                alive(); report['transitions'].append(state(hwnd)); time.sleep(.5)
            if not all(s['minimized'] for s in report['transitions'][2:]):
                raise RuntimeError('Minimization was interrupted')
            user.ShowWindow(hwnd,9)
            subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),str(pid),
                '--focus','--seconds','.1'],check=True,timeout=10,stdout=subprocess.DEVNULL)
            wait(10)
            active_visit(1,'after-restore')
            (out/'recovery.complete').write_text('Presentation recovery measurements complete.\n')
            if child.wait(timeout=100): raise RuntimeError('Native helper failed')
        telemetry = None
        native = json.loads((out/'probe.json').read_text())
        travel = json.loads((out/'travel.json').read_text())
        if native['timed_out'] or native['exit_code_before_cleanup']!=0:
            raise RuntimeError('Native game did not close normally')
        report['source_profile_unchanged'] = travel['source_profile_unchanged']
        report['retail_checkpoints_unchanged'] = travel['retail_checkpoints_unchanged']
        if not report['source_profile_unchanged'] or not report['retail_checkpoints_unchanged']:
            raise RuntimeError('Source data changed')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc(); raise
    finally:
        if child.poll() is None and pid:
            for win in game_windows(pid): user.PostMessageW(w.HWND(win[0]),0x10,0,0)
        child.wait(timeout=550)
        report['elapsed_seconds'] = time.monotonic()-started
        if out.exists(): (out/'session-pacing.json').write_text(json.dumps(report,indent=2)+'\n')
    print(out)


if __name__=='__main__': main()
