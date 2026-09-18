"""Replay repeated PC Display visits and capture live threads only on a stall.

No regular screenshots by default: PrintWindow itself can perturb presentation.
An unchanged log is a trigger for observation, not proof of a game deadlock.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from probe import windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture-interval', type=float, default=300)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out.exists(): parser.error('Use a new output directory')
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-spatial-settings.script'),
               AOT_FRAME_LOG=str(out / 'frame-times.csv'),
               AOT_FRAME_PHASE_LOG=str(out / 'frame-phases.csv'),
               AOT_WAIT_LOG=str(out / 'game-waits.csv'),
               AOT_RENDER_WAIT_LOG=str(out / 'render-waits.csv'))
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(out),
               '--gpu-plugin', 'spatial', '--seconds', '200', '--script-seconds', '145', '--capture-interval', str(args.capture_interval),
               '--use-saved-settings', '--', '--window_width=1920', '--window_height=1080',
               '--fullscreen=false', '--input_backend=xinput', '--aot_keyboard_mouse=false',
               '--readback_resolve=fast', '--resolution_scale=1', '--aot_fps=60', '--vsync=true',
               '--swap_post_effect=fxaa', '--anisotropic_override=5', '--d3d12_debug=true',
               '--d3d12_break_on_error=true']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    sampled, close_requested, pid = False, False, None
    events = []
    script_start_unix = None
    user = c.WinDLL('user32', use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.GetWindowThreadProcessId.restype = w.DWORD
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - start
            if pid is None and (out / 'running.json').exists():
                pid = json.loads((out / 'running.json').read_text())['pid']
            log, frames = out / 'runtime.log', out / 'frame-times.csv'
            if script_start_unix is None and log.exists():
                match = re.search(r'AOT scripted controller clock started: unix_ms=(\d+)', log.read_text(errors='replace'))
                if match: script_start_unix = int(match[1]) / 1000
            if elapsed > 90 and not sampled and log.exists() and frames.exists():
                ages = [time.time() - path.stat().st_mtime for path in (log, frames)]
                owned = windows(pid) if pid else []
                if min(ages) > 4 and owned:
                    sampled = True
                    events.append({'seconds': elapsed, 'event': 'log and frame output inactive', 'ages': ages})
                    text = log.read_text(errors='replace')
                    ids = re.findall(r'\[t(\d+)\] XMPGetPlaybackController', text)
                    queries = [('gpu', ['--thread-name', 'GPU Commands (F8000018)'])]
                    if ids: queries.append(('game', ['--thread', ids[-1]]))
                    window_pid = w.DWORD()
                    ui_tid = user.GetWindowThreadProcessId(owned[0][0], c.byref(window_pid))
                    if window_pid.value == pid: queries.append(('ui', ['--thread', str(ui_tid)]))
                    for label, selection in queries:
                        sampled_process = subprocess.run([sys.executable, str(root / 'tools/sample_native_thread.py'),
                            '--probe', str(out), *selection, '--seconds', '2', '--stack-bytes', '256',
                            '--output', str(out / (label + '-stall.json'))], capture_output=True, text=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                        (out / (label + '-sampler.txt')).write_text(sampled_process.stdout + sampled_process.stderr)
                        events.append({'event': 'sampler', 'label': label, 'returncode': sampled_process.returncode})
                    subprocess.run([sys.executable, str(root / 'tools/inspect_hud_objects.py'), '--menu',
                        '--probe', str(out), '--output', str(out / 'menu-stall.json')], timeout=25,
                        stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            cleanup_due = elapsed >= 190 or (script_start_unix is not None and time.time() - script_start_unix >= 140)
            if cleanup_due and not close_requested and pid:
                owned = windows(pid)
                if owned:
                    close_requested = True
                    events.append({'seconds': elapsed, 'event': 'bounded WM_CLOSE cleanup'})
                    user.PostMessageW(w.HWND(owned[0][0]), 0x10, 0, 0)
            time.sleep(.5)
    finally:
        process.wait(timeout=210)
        report = {'events': events, 'stall_sampled': sampled, 'cleanup_requested': close_requested,
                  'scope': __doc__.strip()}
        if out.exists(): (out / 'menu-stability.json').write_text(json.dumps(report, indent=2) + '\n')
    native = json.loads((out / 'probe.json').read_text())
    report.update(elapsed_seconds=native['elapsed_seconds'], timed_out=native['timed_out'],
                  exit_code=native['exit_code_before_cleanup'])
    (out / 'menu-stability.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return int(close_requested or native['timed_out'] or native['exit_code_before_cleanup'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
