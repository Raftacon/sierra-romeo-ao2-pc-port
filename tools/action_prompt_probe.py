"""Exercise contextual E/A artwork and real E input at the canyon step-jump.

Uses a copied training profile via checkpoint_probe; screenshots need inspection.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--inventory', action='store_true', help='Read HUD/texture objects during the pull-up')
    parser.add_argument('--trace-coop-texture', action='store_true', help='Enable bounded native resource/binding logs')
    parser.add_argument('--cancel-step-jump', action='store_true', help='Compare co-op artwork and cancel before climbing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    if out.exists() or not source.is_dir():
        parser.error('Use a new output and an existing training profile')
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    original, events = hashes(), []
    env = dict(os.environ)
    env.pop('AOT_TRACE_COOP_TEXTURE', None)
    env.pop('AOT_REPLACE_COOP_TEXTURE', None)
    if args.trace_coop_texture:
        env['AOT_TRACE_COOP_TEXTURE'] = '1'
    process = subprocess.Popen([sys.executable, str(root / 'tools/checkpoint_probe.py'),
        '--output', str(out), '--profile', str(source), '--checkpoint', '04_01',
        '--observe-seconds', '90', '--capture-interval', '15'], creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, env=env)
    started = time.monotonic()
    def wait_until(seconds):
        while time.monotonic() - started < seconds:
            if process.poll() is not None:
                raise RuntimeError('Checkpoint probe exited before observation')
            time.sleep(.1)
    def call(tool, *options):
        subprocess.run([sys.executable, str(root / 'tools' / tool), str(pid), *options],
                       check=True, stdout=subprocess.DEVNULL, timeout=15)
    def key(value, duration='.1'):
        call('pc_input_probe.py', '--focus', '--key', value, '--seconds', duration)
        events.append({'seconds': time.monotonic() - started, 'key': value, 'duration': duration})
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
        wait_until(100)
        pid = json.loads((out / 'running.json').read_text())['pid']
        # Keep incoming fire from opening the low-health tutorial during comparison.
        with (out / 'game.commands').open('a') as commands:
            commands.write('god\ngetall AO2PlayerController bGodMode\n')
        events.append({'seconds': time.monotonic() - started, 'command': 'god; query bGodMode'})
        if args.cancel_step_jump:
            wait_until(105)
            snapshot('keyboard-cancel.png')
            keyboard_mode(False)
            snapshot('controller-cancel.png')
            keyboard_mode(True)
            snapshot('keyboard-restored-cancel.png')
            key('F')
            time.sleep(1)
            snapshot('after-cancel.png')
            time.sleep(5)
            snapshot('after-cancel-settled.png')
        else:
            wait_until(110)
            key('D', '.3')
            key('W', '.3')
            time.sleep(3)
            key('W', '.5')
            deadline = time.monotonic() + 8
            while 'Keyboard action material replaced:' not in (out / 'runtime.log').read_text(errors='replace'):
                if time.monotonic() >= deadline or process.poll() is not None:
                    raise RuntimeError('No contextual keyboard material draw observed')
                time.sleep(.1)
            time.sleep(.3)
            snapshot('keyboard-action.png')
            keyboard_mode(False)
            snapshot('controller-action.png')
            keyboard_mode(True)
            snapshot('keyboard-restored.png')
            key('E')
            time.sleep(1)
            snapshot('during-pull-up.png')
            if args.inventory:
                subprocess.run([sys.executable, str(root / 'tools/inspect_hud_objects.py'),
                    '--probe', str(out), '--output', str(out / 'hud-during-pull-up.json')],
                    check=True, stdout=subprocess.DEVNULL, timeout=30)
            time.sleep(5)
            snapshot('after-pull-up.png')
    finally:
        # checkpoint_probe owns its bounded native process and collects terminal evidence.
        process.wait(timeout=200)
        if out.exists():
            (out / 'action-scenario.json').write_text(json.dumps({
                'events': events, 'source_profile_sha256': original,
                'source_profile_unchanged': hashes() == original,
                'cancel_step_jump': args.cancel_step_jump,
                'interpretation': 'Inspect captures for prompt artwork, mode switching and the selected action.'
            }, indent=2) + '\n')
    report = json.loads((out / 'probe.json').read_text())
    if process.returncode or report['timed_out'] or report['exit_code_before_cleanup'] != 0 or hashes() != original:
        raise RuntimeError('Native cleanup or source-profile preservation check failed')
    print(out)


if __name__ == '__main__':
    main()
