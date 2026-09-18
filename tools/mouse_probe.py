"""Measure real mouse movement against native camera rotation at a selected FPS.

Uses a copied training checkpoint and the normal release GPU plugin. Run cases
sequentially without compiling in the background. Requires Windows/Pillow.
"""
import argparse
import ctypes
from ctypes import wintypes, windll
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--fps', type=int, choices=[30, 60], required=True)
    parser.add_argument('--readback', choices=['fast', 'full'], default='fast')
    parser.add_argument('--stall-during-motion', action='store_true',
                        help='Inject an 800 ms game-thread stall during the first movement')
    parser.add_argument('--check-transitions', action='store_true',
                        help='Also check minimize/restore, console and chapter-movie capture release')
    args = parser.parse_args()
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
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-graphics.script'),
               AOT_TEST_KBM='1', AOT_TRACE_MOUSE='1',
               AOT_FRAME_LOG=str(output / 'frame-times.csv'),
               AOT_GAME_COMMANDS=str(output / 'game.commands'))
    for key in ('AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE', 'AOT_PROFILE'): env.pop(key, None)
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(output),
               '--user-data', str(profile), '--seconds', '160', '--capture-interval', '15',
               '--', '--input_backend=xinput', '--aot_keyboard_mouse=true',
               '--readback_resolve=' + args.readback, '--aot_fps=' + str(args.fps),
               '--resolution_scale=1', '--vsync=true', '--aot_mouse_sensitivity=0.08']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    result = {'fps_target': args.fps, 'readback': args.readback, 'profile_sha256': hashes,
              'samples': []}
    try:
        while not (output / 'running.json').exists():
            if process.poll() is not None or time.monotonic() - start > 30:
                raise RuntimeError('Probe did not create its child process report')
            time.sleep(.1)
        pid = json.loads((output / 'running.json').read_text())['pid']
        log = output / 'runtime.log'
        def log_text(): return log.read_text(errors='replace') if log.exists() else ''
        def send(*options):
            subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                            str(pid), '--focus', *options], check=True,
                           stdout=subprocess.DEVNULL)
        def wait_for(predicate, seconds):
            deadline = time.monotonic() + seconds
            while not predicate():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Native mouse probe readiness/query timed out')
                time.sleep(.1)
        wait_for(lambda: time.monotonic() - start > 75 and 'Mouse intention names:' in log_text(), 110)
        send('--key', 'K', '--seconds', '.1')
        wait_for(lambda: 'PC relative mouse capture enabled' in log_text(), 10)
        time.sleep(1)
        pattern = re.compile(r'AO2PlayerController Checkpoint\.TheWorld\.[^\n]*?Rotation = \(Pitch=(-?\d+),Yaw=(-?\d+),Roll=(-?\d+)\)')
        def rotation():
            count = len(pattern.findall(log_text()))
            with (output / 'game.commands').open('a') as commands:
                commands.write('getall AO2PlayerController Rotation\n')
            wait_for(lambda: len(pattern.findall(log_text())) > count, 5)
            return tuple(map(int, pattern.findall(log_text())[-1]))
        def measure(x, y, seconds, paused=False):
            before = rotation()
            stalled = args.stall_during_motion and not result['samples']
            if stalled:
                moving = subprocess.Popen([sys.executable, str(root / 'tools/pc_input_probe.py'),
                    str(pid), '--focus', '--move', str(x), str(y), '--seconds', str(seconds)],
                    stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    time.sleep(.15)
                    with (output / 'game.commands').open('a') as commands:
                        commands.write('@test-frame-stall\n')
                finally:
                    if moving.wait(timeout=10) != 0: raise RuntimeError('Mouse input failed')
                wait_for(lambda: 'Test game-thread stall: end' in log_text(), 5)
            else:
                send('--move', str(x), str(y), '--seconds', str(seconds))
            time.sleep(.5)
            after = rotation()
            delta = tuple((b - a + 32768) % 65536 - 32768 for a, b in zip(before, after))
            expected = (0, 0, 0) if paused else (-y * 5, x * 5, 0)
            result['samples'].append({'mouse_pixels': [x, y], 'requested_seconds': seconds,
                'paused': paused, 'stalled': stalled, 'before': before, 'after': after, 'delta': delta,
                'expected_units': expected,
                'passed': all(abs(a - b) <= (0 if paused else 20) for a, b in zip(delta, expected))})
        measure(600, 0, 1)
        measure(-600, 0, .25)
        measure(0, -300, 1)
        measure(0, 300, .25)
        targets = windows(pid)
        if len(targets) != 1: raise RuntimeError('Game window disappeared')
        hwnd, _, width, height = targets[0]
        if not capture(hwnd, width, height, output / 'gameplay.png'):
            raise RuntimeError('Gameplay capture failed')
        send('--key', 'Escape', '--seconds', '.1')
        time.sleep(.7)
        measure(300, -150, .5, paused=True)
        if args.check_transitions:
            class CursorInfo(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD),
                            ('cursor', wintypes.HANDLE), ('position', wintypes.POINT)]
            def cursor_visible():
                info = CursorInfo(); info.size = ctypes.sizeof(info)
                if not windll.user32.GetCursorInfo(ctypes.byref(info)):
                    raise RuntimeError('GetCursorInfo failed')
                return bool(info.flags & 1)
            transitions = result['transitions'] = []
            def cursor_check(label, visible):
                wait_for(lambda: cursor_visible() == visible, 5)
                transitions.append({'state': label, 'cursor_visible': cursor_visible(),
                                    'expected_visible': visible})
            send('--key', 'Escape', '--seconds', '.1')
            cursor_check('resumed gameplay', False)
            windll.user32.ShowWindow(wintypes.HWND(hwnd), 6)
            wait_for(lambda: bool(windll.user32.IsIconic(wintypes.HWND(hwnd))), 5)
            cursor_check('minimized', True)
            windll.user32.ShowWindow(wintypes.HWND(hwnd), 9)
            send('--key', 'K', '--seconds', '.1')
            cursor_check('restored gameplay', False)
            send('--key', 'Tilde', '--seconds', '.1')
            cursor_check('console', True)
            if not capture(hwnd, width, height, output / 'console.png'):
                raise RuntimeError('Console capture failed')
            measure(300, -150, .5, paused=True)
            send('--key', 'Tilde', '--seconds', '.1')
            cursor_check('closed console', False)
            measure(120, 0, .25)
            with (output / 'game.commands').open('a') as commands:
                commands.write('open Checkpoint?LoadSaveGame?CheckpointToLoad=04_100?Difficulty=1\n')
            wait_for(lambda: 'name=LVL4_Full,' in log_text(), 30)
            time.sleep(.5)
            # Focused movies intentionally hide the cursor, but must release
            # relative capture. Visibility alone cannot distinguish the two.
            cursor_check('chapter movie', False)
            clip = wintypes.RECT()
            if not windll.user32.GetClipCursor(ctypes.byref(clip)):
                raise RuntimeError('GetClipCursor failed')
            desktop = tuple(windll.user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            bounds = (clip.left, clip.top, clip.right, clip.bottom)
            expected = (desktop[0], desktop[1], desktop[0] + desktop[2], desktop[1] + desktop[3])
            capture_events = re.findall(r'PC (relative mouse capture enabled|mouse capture released)', log_text())
            released = bool(capture_events) and capture_events[-1] == 'mouse capture released'
            transitions.append({'state': 'chapter movie capture', 'clip_bounds': bounds,
                                'desktop_bounds': expected, 'capture_released': released})
            if bounds != expected or not released:
                raise RuntimeError('Movie retained mouse capture')
            if not capture(hwnd, width, height, output / 'movie.png'):
                raise RuntimeError('Movie capture failed')
        result['passed'] = all(sample['passed'] for sample in result['samples'])
        windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
    finally:
        if output.exists():
            (output / 'mouse.json').write_text(json.dumps(result, indent=2) + '\n')
        process.wait(timeout=180)
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Native game did not close normally; inspect probe.json')
    print(json.dumps(result, indent=2))
    if not result.get('passed'): raise SystemExit(1)


if __name__ == '__main__': main()
