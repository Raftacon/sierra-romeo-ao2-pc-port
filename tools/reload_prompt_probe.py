"""Exercise low-ammo keyboard/controller prompts from a copied training save."""
import argparse
from ctypes import windll, wintypes
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
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    profile = out.with_name(out.name + '-profile')
    if out.exists() or profile.exists() or not source.is_dir() or source in profile.parents or profile in source.parents:
        parser.error('Use new output/profile paths and a separate existing source profile')
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    original = hashes()
    shutil.copytree(source, profile)
    env = dict(os.environ)
    for name in ('AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE', 'AOT_PROFILE', 'AOT_RECT_LOG'):
        env.pop(name, None)
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-graphics.script'), AOT_TEST_KBM='1')
    process = subprocess.Popen([sys.executable, str(root / 'tools/probe.py'), '--output', str(out),
        '--user-data', str(profile), '--seconds', '160', '--capture-interval', '5', '--',
        '--input_backend=xinput', '--readback_resolve=fast', '--vsync=true', '--aot_fps=60',
        '--swap_post_effect=fxaa', '--aot_keyboard_mouse=true'], env=env,
        stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    started, events, pid = time.monotonic(), [], None
    def call(tool, *options):
        subprocess.run([sys.executable, str(root / 'tools' / tool), str(pid), *options],
                       check=True, stdout=subprocess.DEVNULL, timeout=15)
    def key(value):
        call('pc_input_probe.py', '--focus', '--key', value, '--seconds', '.1')
    def snapshot(filename):
        targets = windows(pid)
        if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / filename):
            raise RuntimeError('Could not capture native window')
        events.append({'seconds': time.monotonic() - started, 'capture': filename})
    def keyboard_mode(enabled):
        key('Tilde')
        call('window_message.py', '--text', 'aot_keyboard_mouse ' + str(enabled).lower())
        key('Enter')
        key('Tilde')
        time.sleep(.5)
        events.append({'seconds': time.monotonic() - started, 'keyboard_mode_requested': enabled})
    try:
        while time.monotonic() - started < 80:
            if process.poll() is not None:
                raise RuntimeError('Game exited before training observation')
            time.sleep(.1)
        pid = json.loads((out / 'running.json').read_text())['pid']
        key('K')
        time.sleep(.5)
        for i, duration in enumerate([1.5] + [.45] * 6):
            call('pc_input_probe.py', '--focus', '--mouse-button', 'left', '--seconds', str(duration))
            time.sleep(.3)
            snapshot(f'after-fire-{i}.png')
            if 'Keyboard reload material replaced:' in (out / 'runtime.log').read_text(errors='replace'):
                break
        else:
            raise RuntimeError('No keyboard reload draw was observed')
        snapshot('keyboard-reload.png')
        keyboard_mode(False)
        snapshot('controller-reload.png')
        keyboard_mode(True)
        snapshot('keyboard-restored.png')
        key('R')
        time.sleep(3)
        snapshot('after-reload.png')
    finally:
        if pid:
            targets = windows(pid)
            if len(targets) == 1:
                windll.user32.PostMessageW(wintypes.HWND(targets[0][0]), 0x10, 0, 0)
        process.wait(timeout=190)
        if out.exists():
            (out / 'reload-scenario.json').write_text(json.dumps({'events': events,
                'source_profile_sha256': original, 'source_profile_unchanged': hashes() == original,
                'interpretation': 'Inspect the screenshots for prompt contents, mode switching and reload behavior.'}, indent=2) + '\n')
    report = json.loads((out / 'probe.json').read_text())
    if process.returncode or report['timed_out'] or report['exit_code_before_cleanup'] != 0 or hashes() != original:
        raise RuntimeError('Native cleanup or source-profile preservation check failed')
    print(out)


if __name__ == '__main__':
    main()
