"""Compare native rendering from a copied training checkpoint with real mouse input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from probe import capture, windows
from ctypes import wintypes, windll


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--readback', choices=['fast', 'full', 'some'], default='fast')
    parser.add_argument('--gpu-plugin', default='xenos')
    parser.add_argument('--cache-root', type=Path)
    parser.add_argument('--initial-mouse', nargs=2, type=int, default=[600, 0], metavar=('DX', 'DY'))
    parser.add_argument('--fire-seconds', type=float, default=.25)
    parser.add_argument('--before-fire-burst', action='store_true')
    parser.add_argument('--capture-motion', action='store_true', help='Capture continuously during slower camera turns')
    parser.add_argument('--extra', nargs=argparse.REMAINDER, default=[])
    args = parser.parse_args()
    if not 0 < args.fire_seconds <= 2: parser.error('Fire duration must be in (0, 2] seconds')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    profile = output.with_name(output.name + '-profile')
    if output.exists() or profile.exists(): parser.error('Use new output/profile paths')
    source = args.profile.resolve()
    if not source.is_dir() or source in profile.parents or profile in source.parents:
        parser.error('A separate existing test profile is required')
    manifest = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    shutil.copytree(source, profile)
    env = dict(os.environ)
    env['AOT_INPUT_SCRIPT'] = str(root / 'config/input-graphics.script')
    env['AOT_TEST_KBM'] = '1'
    env['AOT_FRAME_LOG'] = str(output / 'frame-times.csv')
    for name in ('AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE'):
        env.pop(name, None)
    env['AOT_TRACE_MOUSE'] = '1'
    env['AOT_GAME_COMMANDS'] = str(output / 'game.commands')
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(output),
               '--user-data', str(profile), '--seconds', '165', '--capture-interval', '5',
               '--gpu-plugin', args.gpu_plugin,
               *(['--cache-root', str(args.cache_root.resolve())] if args.cache_root else []), '--',
               '--input_backend=xinput', '--readback_resolve=' + args.readback,
               '--vsync=true', '--swap_post_effect=fxaa', '--aot_fps=60',
               '--resolution_scale=1', '--aot_keyboard_mouse=true', *args.extra]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    events = []
    try:
        while not (output / 'running.json').exists():
            if process.poll() is not None or time.monotonic() - start > 30:
                raise RuntimeError('Probe did not create its child process report')
            time.sleep(.1)
        pid = json.loads((output / 'running.json').read_text())['pid']
        def input_event(*options, capture_motion=None):
            input_command = [sys.executable, str(root / 'tools/pc_input_probe.py'),
                             str(pid), '--focus', *options]
            if capture_motion:
                motion = subprocess.Popen(input_command, stdout=subprocess.DEVNULL)
                try:
                    burst(capture_motion, 5, until=motion)
                finally:
                    if motion.wait(timeout=10) != 0: raise RuntimeError('Camera input failed')
            else:
                subprocess.run(input_command, check=True, stdout=subprocess.DEVNULL)
            events.append({'seconds': time.monotonic() - start, 'input': options})
        def burst(name, seconds=5, until=None):
            directory = output / name
            directory.mkdir()
            begin = time.monotonic()
            index = 0
            while time.monotonic() - begin < seconds and (until is None or until.poll() is None):
                targets = windows(pid)
                if len(targets) != 1: raise RuntimeError('Game window disappeared')
                hwnd, title, width, height = targets[0]
                if not capture(hwnd, width, height, directory / f'{index:03d}.png'):
                    raise RuntimeError('Window capture failed')
                index += 1
                time.sleep(.08)
            events.append({'seconds': time.monotonic() - start, 'burst': name, 'frames': index})
        while True:
            log = output / 'runtime.log'
            ready = log.exists() and 'Mouse intention names:' in log.read_text(errors='replace')
            if ready and time.monotonic() - start > 72: break
            if process.poll() is not None or time.monotonic() - start > 115:
                raise RuntimeError('Training camera did not become available')
            time.sleep(.5)
        time.sleep(3)
        input_event('--key', 'K', '--seconds', '.1')
        capture_deadline = time.monotonic() + 30
        while 'PC relative mouse capture enabled' not in log.read_text(errors='replace'):
            if time.monotonic() > capture_deadline: raise RuntimeError('Relative mouse capture did not engage')
            time.sleep(.1)
        time.sleep(1)
        commands = output / 'game.commands'
        commands.write_text('getall AO2PlayerController Rotation\n')
        time.sleep(.3)
        input_event('--move', *map(str, args.initial_mouse), '--seconds', '1')
        if args.before_fire_burst:
            time.sleep(1)
            burst('before-fire', 2)
        input_event('--mouse-button', 'left', '--seconds', str(args.fire_seconds))
        time.sleep(2)
        with commands.open('a') as file: file.write('getall AO2PlayerController Rotation\n')
        burst('stationary')
        for direction in (1, -1, 1, -1):
            input_event('--move', str(direction * 180), '0', '--seconds', '2' if args.capture_motion else '.7',
                        capture_motion='during-move-' + str(len(events)) if args.capture_motion else None)
            burst('after-move-' + str(len(events)), 2)
        input_event('--key', 'Escape', '--seconds', '.1')
        time.sleep(.5)
        burst('paused', 3)
        targets = windows(pid)
        if len(targets) == 1:
            windll.user32.PostMessageW(wintypes.HWND(targets[0][0]), 0x0010, 0, 0)
    finally:
        if output.exists():
            (output / 'scenario.json').write_text(json.dumps({'profile_sha256': manifest,
                'events': events}, indent=2) + '\n')
        # Let the existing bounded probe own cleanup and write its result.
        process.wait(timeout=180)
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Native probe did not close normally; inspect probe.json')
    print(output)


if __name__ == '__main__': main()
