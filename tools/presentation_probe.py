"""Measure this game's fullscreen host presentation with process-filtered PresentMon."""
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--no-tearing', action='store_true')
    parser.add_argument('--settings', action='store_true', help='Exercise live VSync Off, canceled defaults, and On through PC Display')
    parser.add_argument('--saved-vsync', action='store_true', help='Load VSync from the copied profile instead of overriding it to On')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    profile = out.with_name(out.name + '-profile')
    if out.exists() or profile.exists() or not source.is_dir():
        parser.error('Use new output/profile paths and an existing test profile')
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    original = hashes()
    shutil.copytree(source, profile)
    monitor = root / '.tools/presentmon/PresentMon-2.5.1-x64.exe'
    if hashlib.sha256(monitor.read_bytes()).hexdigest() != '9bec3083069f58f911e6a512f4806db51a27bd096103087bc1d05ef54c80a191':
        raise ValueError('Require the pinned PresentMon 2.5.1 standalone executable')
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOT_')}
    if args.settings:
        env['AOT_INPUT_SCRIPT'] = str(root / 'config/input-presentation-settings.script')
        env['AOT_FRAME_LOG'] = str(out / 'frames.csv')
        env['AOT_WAIT_LOG'] = str(out / 'waits.csv')
        env['AOT_RENDER_WAIT_LOG'] = str(out / 'render-waits.csv')
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(out),
               '--user-data', str(profile), '--seconds', '145' if args.settings else '50', '--capture-interval', '200',
               '--fullscreen', '--', '--input_backend=xinput',
               '--aot_fps=60', '--readback_resolve=fast', '--resolution_scale=1']
    if not args.saved_vsync:
        command.append('--vsync=true')
    if args.no_tearing:
        command.append('--d3d12_allow_variable_refresh_rate_and_tearing=false')
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    started, pid, monitor_result = time.monotonic(), None, None
    try:
        while not (out / 'running.json').exists():
            if process.poll() is not None or time.monotonic() - started > 30:
                raise RuntimeError('Native child did not start')
            time.sleep(.1)
        pid = json.loads((out / 'running.json').read_text())['pid']
        while time.monotonic() - started < 15:
            if process.poll() is not None:
                raise RuntimeError('Native child exited before capture')
            time.sleep(.1)
        monitor_command = [str(monitor), '--process_id', str(pid), '--timed', '120' if args.settings else '10',
                           '--terminate_after_timed', '--terminate_on_proc_exit',
                           '--session_name', f'AotPresentProbe-{pid}', '--no_console_stats',
                           '--no_track_input', '--v1_metrics', '--qpc_time_ms',
                           '--output_file', str(out / 'presents.csv')]
        with (out / 'presentmon.log').open('w') as log:
            monitor_process = subprocess.Popen(monitor_command, stdout=log, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                if args.settings:
                    for target in (48, 54, 60, 69, 74, 81, 87, 94, 102, 108):
                        while time.monotonic() - started < target:
                            if process.poll() is not None:
                                raise RuntimeError('Native settings sequence exited early')
                            time.sleep(.1)
                        targets = windows(pid)
                        if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / f'settings-{target:03d}.png'):
                            raise RuntimeError('Could not capture native settings')
                monitor_process.wait(timeout=35 if args.settings else 20)
                monitor_result = monitor_process
            finally:
                if monitor_process.poll() is None:
                    monitor_process.terminate()
                    monitor_process.wait(timeout=10)
        if not args.settings:
            targets = windows(pid)
            if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / 'scene.png'):
                raise RuntimeError('Could not capture native scene')
    finally:
        if pid is not None:
            targets = windows(pid)
            if len(targets) == 1:
                c.windll.user32.PostMessageW(c.c_void_p(targets[0][0]), 0x10, 0, 0)
        process.wait(timeout=60)
        if out.exists():
            (out / 'presentation-scenario.json').write_text(json.dumps({
                'source_profile_sha256': original, 'source_profile_unchanged': hashes() == original,
                'no_tearing_requested': args.no_tearing,
                'settings_sequence': args.settings,
                'saved_vsync': args.saved_vsync,
                'presentmon_exit_code': monitor_result.returncode if monitor_result else None
            }, indent=2) + '\n')
    report = json.loads((out / 'probe.json').read_text())
    if process.returncode or report['timed_out'] or report['exit_code_before_cleanup'] != 0 or hashes() != original:
        raise RuntimeError('Native completion or source-profile preservation failed')
    if monitor_result.returncode != 0 or not (out / 'presents.csv').is_file():
        raise RuntimeError('PresentMon did not produce a successful capture; see presentmon.log')
    if args.settings:
        log = (out / 'runtime.log').read_text(errors='replace')
        for expected in ('PC VSync presentation refreshed: allow_tearing=true',
                         'PC VSync presentation refreshed: allow_tearing=false',
                         'PC Exit confirmed; requesting window close'):
            if expected not in log:
                raise RuntimeError('Settings sequence incomplete: missing ' + expected)
    print(out)


if __name__ == '__main__':
    main()
