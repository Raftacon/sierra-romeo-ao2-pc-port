"""Measure an idle training scene without repeated screenshots or command-file polling."""
import argparse
from ctypes import wintypes, windll
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from probe import windows, capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--gpu-poll-backoff', action='store_true', help='Enable experimental GPU polling backoff')
    parser.add_argument('--gpu-control', action='store_true', help='Use the locally symbolized GPU with its three rendering experiments disabled')
    parser.add_argument('--gpu-wait-timing', action='store_true', help='Trace GPU packet waits using --gpu-control')
    parser.add_argument('--gpu-copy-timing', action='store_true', help='Trace legacy resolve copies using --gpu-control')
    parser.add_argument('--precise-gpu-sleep', action='store_true', help='Use the experimental packet timer with --gpu-control')
    parser.add_argument('--precise-vblank-sleep', action='store_true', help='Use the experimental VBlank deadline timer with --gpu-control')
    parser.add_argument('--gpu-vblank-timing', action='store_true', help='Trace guest VSync callback cadence with --gpu-control')
    parser.add_argument('--gpu-vblank-watch', type=lambda s: int(s, 0), help='Optional aligned physical word to inspect before/after VSync callbacks')
    parser.add_argument('--render-wait-timing', action='store_true', help='Trace waits between original rendering-thread fences')
    parser.add_argument('--wait-timing', action='store_true', help='Attribute original game-thread kernel waits')
    parser.add_argument('--phase-timing', action='store_true', help='Record opt-in frame-phase attribution')
    parser.add_argument('--normal-input', action='store_true', help='Navigate with Windows keyboard input while keeping the physical XInput driver active')
    parser.add_argument('--measurement-seconds', type=int, default=25, help='Measured game-frame window length (25 to 300 seconds)')
    args = parser.parse_args()
    if args.gpu_vblank_watch is not None and (not args.gpu_vblank_timing or not 0 <= args.gpu_vblank_watch <= 0x1FFFFFFC or args.gpu_vblank_watch % 4):
        parser.error('--gpu-vblank-watch requires VSync tracing and an aligned physical address below 512 MiB')
    if args.gpu_wait_timing or args.gpu_copy_timing or args.precise_gpu_sleep or args.precise_vblank_sleep or args.gpu_vblank_timing:
        args.gpu_control = True
    if not 25 <= args.measurement_seconds <= 300:
        parser.error('--measurement-seconds must be between 25 and 300')
    measurement_end = 75 + args.measurement_seconds
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    profile = output.with_name(output.name + '-profile')
    source = args.profile.resolve()
    if output.exists() or profile.exists(): parser.error('Use new output/profile paths')
    if not source.is_dir() or source in profile.parents or profile in source.parents:
        parser.error('A separate existing test profile is required')
    hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    shutil.copytree(source, profile)
    env = dict(os.environ)
    for name in ('AOT_INPUT_SCRIPT', 'AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE', 'AOT_PROFILE', 'AOT_GAME_COMMANDS',
                 'AOT_TRACE_MOUSE', 'AOT_TRACE_FONT', 'AOT_TRACE_HUD', 'AOT_TEST_KBM', 'AOT_FRAME_PHASE_LOG', 'AOT_WAIT_LOG', 'AOT_RENDER_WAIT_LOG', 'AOT_GPU_WAIT_LOG', 'AOT_GPU_COPY_LOG', 'AOT_GPU_VBLANK_LOG', 'AOT_GPU_VBLANK_WATCH'):
        env.pop(name, None)
    env['AOT_FRAME_LOG'] = str(output / 'frame-times.csv')
    if not args.normal_input:
        env['AOT_INPUT_SCRIPT'] = str(root / 'config/input-graphics.script')
    if args.render_wait_timing:
        env['AOT_RENDER_WAIT_LOG'] = str(output / 'render-waits.csv')
    if args.wait_timing:
        env['AOT_WAIT_LOG'] = str(output / 'waits.csv')
    if args.phase_timing:
        env['AOT_FRAME_PHASE_LOG'] = str(output / 'phases.csv')
    if args.gpu_wait_timing:
        env['AOT_GPU_WAIT_LOG'] = str(output / 'gpu-waits.csv')
    if args.gpu_copy_timing:
        env['AOT_GPU_COPY_LOG'] = str(output / 'gpu-copies.csv')
    if args.gpu_vblank_timing:
        env['AOT_GPU_VBLANK_LOG'] = str(output / 'gpu-vblank.csv')
    if args.gpu_vblank_watch is not None:
        env['AOT_GPU_VBLANK_WATCH'] = hex(args.gpu_vblank_watch)
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(output),
               '--user-data', str(profile), '--seconds', str(measurement_end + 45), '--capture-interval', str(measurement_end + 60),
               '--', '--input_backend=xinput', '--readback_resolve=fast', '--vsync=true',
               '--swap_post_effect=fxaa', '--anisotropic_override=5', '--aot_fps=60',
               '--resolution_scale=1', '--aot_precise_frame_pacing=' + str(not args.legacy).lower(),
               '--aot_gpu_poll_backoff=' + str(args.gpu_poll_backoff).lower()]
    if args.gpu_control:
        command[command.index('--'):command.index('--')] = ['--gpu-plugin', 'aot']
        command += ['--aot_resolve_readback_sync=false', '--aot_viewport_depth_key=false',
                    '--aot_vertex_residency_validation=false',
                    '--aot_precise_gpu_sleep=' + str(args.precise_gpu_sleep).lower(),
                    '--aot_precise_vblank_sleep=' + str(args.precise_vblank_sleep).lower()]
    if args.normal_input:
        command.append('--aot_keyboard_mouse=true')
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    cpu_samples = []
    try:
        while not (output / 'running.json').exists():
            if process.poll() is not None or time.monotonic() - start > 30:
                raise RuntimeError('Probe did not start')
            time.sleep(.1)
        pid = json.loads((output / 'running.json').read_text())['pid']
        if args.normal_input:
            events = []
            try:
                for target, key in [(22, 'Enter'), (30, 'Enter'), (38, 'Enter'),
                                    (46, 'Down'), (48, 'Enter'), (56, 'Enter'), (64, 'Enter')]:
                    while time.monotonic() - start < target:
                        if process.poll() is not None:
                            raise RuntimeError('Game exited during keyboard navigation')
                        time.sleep(.1)
                    subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                                    str(pid), '--focus', '--key', key, '--seconds', '.15' if key == 'Down' else '.2'],
                                   check=True, stdout=subprocess.DEVNULL, timeout=10)
                    events.append({'wall_seconds': time.monotonic() - start, 'key': key})
                    time.sleep(1)
                    targets = windows(pid)
                    if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3],
                                                        output / f'navigation-{target:03d}.png'):
                        raise RuntimeError('Could not capture keyboard navigation')
            finally:
                (output / 'keyboard-navigation.json').write_text(json.dumps(events, indent=2) + '\n')
        for target, name in [(75, 'training-start.png'), (measurement_end + 25, 'training-end.png')]:
            while time.monotonic() - start < target:
                if process.poll() is not None: raise RuntimeError('Game exited before measurement')
                time.sleep(.1)
            found = windows(pid)
            if len(found) != 1: raise RuntimeError('Expected one game window')
            hwnd, _, width, height = found[0]
            if not capture(hwnd, width, height, output / name): raise RuntimeError('Capture failed')
            # Whole-process CPU over the wall-time interval between endpoint
            # captures, separate from the game-frame timing window below.
            import ctypes as c
            kernel = c.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([c.POINTER(wintypes.FILETIME)] * 4)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle: raise c.WinError(c.get_last_error())
            try:
                created, exited, kt, ut = [wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kt), c.byref(ut)):
                    raise c.WinError(c.get_last_error())
                cpu = sum((v.dwHighDateTime << 32) | v.dwLowDateTime for v in (kt, ut)) / 10000000
                cpu_samples.append({'wall_seconds': time.monotonic() - start, 'process_cpu_seconds': cpu})
            finally:
                kernel.CloseHandle(handle)
        windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
    finally:
        process.wait(timeout=160)
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Probe did not close normally')
    elapsed = 0
    values = []
    for row in csv.DictReader((output / 'frame-times.csv').open()):
        interval = float(row['interval_ms'])
        elapsed += interval / 1000
        if 75 <= elapsed < measurement_end: values.append(interval)
    if not values: raise RuntimeError('No measurement frames')
    ordered = sorted(values)
    unchanged = hashes == {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in source.rglob('*') if p.is_file()}
    result = {'source_profile_sha256': hashes, 'source_profile_unchanged': unchanged,
              'legacy': args.legacy, 'normal_input': args.normal_input,
              'gpu_poll_backoff': args.gpu_poll_backoff, 'gpu_control': args.gpu_control,
              'gpu_wait_timing': args.gpu_wait_timing,
              'gpu_copy_timing': args.gpu_copy_timing,
              'precise_gpu_sleep': args.precise_gpu_sleep,
              'precise_vblank_sleep': args.precise_vblank_sleep,
              'gpu_vblank_timing': args.gpu_vblank_timing, 'gpu_vblank_watch': args.gpu_vblank_watch,
              'endpoint_cpu_samples': cpu_samples,
              'phase_timing': args.phase_timing, 'wait_timing': args.wait_timing, 'render_wait_timing': args.render_wait_timing,
              'window_game_frame_seconds': [75, measurement_end], 'frames': len(values),
              'covered_seconds': sum(values) / 1000, 'mean_ms': statistics.mean(values),
              'p95_ms': ordered[int(len(ordered) * .95)],
              'p99_ms': ordered[int(len(ordered) * .99)], 'max_ms': max(values),
              'stddev_ms': statistics.pstdev(values), 'over_20ms': sum(v > 20 for v in values),
              'interpretation': 'Inspect scene captures; timing alone is not a gameplay pass.'}
    (output / 'pacing.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_profile_sha256'}, indent=2))


if __name__ == '__main__': main()
