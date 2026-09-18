"""Replay the first vault and precision-shooting exercise for visual inspection.

This is a diagnostic route, not an assertion of campaign or rendering parity.
Uses real Windows input and a fresh copy of a training checkpoint profile.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from ctypes import wintypes, windll
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--gpu-plugin', default='xenos')
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--extra', nargs=argparse.REMAINDER, default=[])
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
    for name in ('AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE'): env.pop(name, None)
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(output),
               '--user-data', str(profile), '--seconds', '210', '--capture-interval', '5',
               '--gpu-plugin', args.gpu_plugin, *(['--fullscreen'] if args.fullscreen else []),
               '--', '--input_backend=xinput',
               '--readback_resolve=fast', '--aot_keyboard_mouse=true', '--aot_fps=60',
               '--resolution_scale=1', '--vsync=true', '--aot_mouse_sensitivity=0.08',
               '--swap_post_effect=fxaa', '--anisotropic_override=5',
               '--readback_resolve_half_pixel_offset=true', *args.extra]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    events = []
    try:
        def wait_for(predicate, seconds):
            deadline = time.monotonic() + seconds
            while not predicate():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Training route readiness timed out')
                time.sleep(.1)
        wait_for(lambda: (output / 'running.json').exists(), 30)
        pid = json.loads((output / 'running.json').read_text())['pid']
        log = output / 'runtime.log'
        wait_for(lambda: time.monotonic() - start > 75 and log.exists() and
                 'Mouse intention names:' in log.read_text(errors='replace'), 110)
        def input_command(options):
            return [sys.executable, str(root / 'tools/pc_input_probe.py'),
                    str(pid), '--focus', *options]
        def send(*options):
            subprocess.run(input_command(options), check=True, stdout=subprocess.DEVNULL)
            events.append({'seconds': time.monotonic() - start, 'input': options})
        def snapshot(name):
            targets = windows(pid)
            if len(targets) != 1: raise RuntimeError('Game window disappeared')
            hwnd, _, width, height = targets[0]
            if not capture(hwnd, width, height, output / name): raise RuntimeError('Capture failed')
            events.append({'seconds': time.monotonic() - start, 'capture': name})
        def aim_fire(seconds, index):
            # Each input subprocess validates this exact game's foreground
            # window and releases its own button, including failure paths.
            aim = subprocess.Popen(input_command(['--mouse-button', 'right', '--seconds', '3']),
                                   stdout=subprocess.DEVNULL)
            try:
                time.sleep(.7)
                snapshot(f'target-{index}-aim.png')
                send('--mouse-button', 'left', '--seconds', str(seconds))
                snapshot(f'target-{index}-fired.png')
            finally:
                if aim.wait(timeout=10) != 0: raise RuntimeError('Aim input failed')
            events.append({'seconds': time.monotonic() - start, 'aim_fire_seconds': seconds})
        send('--key', 'K', '--seconds', '.1')
        wait_for(lambda: 'PC relative mouse capture enabled' in log.read_text(errors='replace'), 30)
        time.sleep(1)
        send('--key', 'W', '--seconds', '1')
        send('--key', 'Space', '--seconds', '.2')
        time.sleep(2)
        send('--key', 'W', '--seconds', '2')
        snapshot('after-vault.png')
        time.sleep(20)
        snapshot('precision-start.png')
        # The final correction returns to the left member of the distant pair.
        for i, (x, y) in enumerate([(-170, 70), (-230, 23), (2013, 16), (155, 20), (-135, 14)], 1):
            send('--mouse-button', 'right', '--move', str(x), str(y), '--seconds', '1')
            aim_fire(.3 if i == 1 else .2, i)
            snapshot(f'target-{i}.png')
        time.sleep(10)
        snapshot('after-precision.png')
        targets = windows(pid)
        if len(targets) == 1:
            windll.user32.PostMessageW(wintypes.HWND(targets[0][0]), 0x0010, 0, 0)
    finally:
        if output.exists():
            (output / 'route.json').write_text(json.dumps({'profile_sha256': hashes,
                'events': events, 'interpretation': 'Inspect captures and game state; no automatic gameplay pass.'}, indent=2) + '\n')
        process.wait(timeout=240)
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Native probe did not close normally; inspect probe.json')
    print(output)


if __name__ == '__main__': main()
