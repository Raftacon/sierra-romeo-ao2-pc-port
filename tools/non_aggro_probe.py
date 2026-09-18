"""Exercise both retail aggro extremes and neutral shading in one copied-profile run.

Uses verified 01_02 and console controls, not out-of-combat gunfire. Retains
lossless motion, timed screenshots, and queried material state; successful
completion alone does not certify visual parity.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from probe import capture, game_windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--cache-root', type=Path, required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--preserve-scene', action=argparse.BooleanOptionalAction, default=None,
                        help='Override scene preservation; omitted uses the runtime default')
    parser.add_argument('--vblank-wake', action=argparse.BooleanOptionalAction, default=None,
                        help='Override GPU VBlank notification; omitted uses the runtime default')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out.exists(): parser.error('Require new output directory')
    command = [sys.executable, str(root/'tools/checkpoint_probe.py'), '--output', str(out),
               '--profile', str(args.profile.resolve()), '--checkpoint', '01_02',
               '--verify-checkpoint', '--observe-seconds', '120', '--gpu-plugin', 'spatial',
               '--window', '1920', '1080', '--controller-only', '--capture-interval', '30',
               '--cache-root', str(args.cache_root.resolve())]
    if args.preserve_scene is not None or args.vblank_wake is not None: command += ['--extra']
    if args.preserve_scene is not None: command += ['--aot_preserve_scene_before_shadows='+str(args.preserve_scene).lower()]
    if args.vblank_wake is not None: command += ['--aot_gpu_vblank_wake='+str(args.vblank_wake).lower()]
    started = time.monotonic()
    report = {'command': command, 'phases': [], 'complete': False}
    child = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    pad, last_pad, video = out/'controller.state', None, None
    def alive():
        if child.poll() is not None: raise RuntimeError('Checkpoint probe exited before scenario completion')
    def hold(seconds, rx=0):
        nonlocal last_pad
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            alive()
            value = '0000 0 0 0 0 '+str(rx)+' 0\n'
            if value != last_pad:
                pad.write_text(value); last_pad = value
            else: os.utime(pad, None)
            time.sleep(.05)
    def commands(lines):
        with (out/'game.commands').open('a') as f: f.write('\n'.join(lines)+'\n')
    def snapshot(name):
        windows = game_windows(pid)
        if len(windows) != 1 or not capture(windows[0][0], windows[0][2], windows[0][3], out/name):
            raise RuntimeError('Expected one capturable game window')
    try:
        while time.monotonic()-started < 125:
            alive()
            log = out/'runtime.log'
            if log.exists() and 'Game [getall AO2CheckpointManager CurrCheckpoint]' in log.read_text(errors='replace'):
                break
            time.sleep(.1)
        else: raise RuntimeError('Checkpoint verification response was not ready')
        pid = json.loads((out/'running.json').read_text())['pid']; report['pid'] = pid
        subprocess.run([sys.executable, str(root/'tools/pc_input_probe.py'), str(pid),
                        '--focus', '--seconds', '.1'], check=True, timeout=10, stdout=subprocess.DEVNULL)
        hold(1); snapshot('neutral-before.png')
        for name, setup in (('aggro-one', ['SetAggro 1', 'SetPAIAggro 0']),
                            ('aggro-reversed', ['SetAggro 99', 'SetPAIAggro 0']),
                            ('neutral-restored', ['SetAggro 50', 'FreeAggro'])):
            offset = (out/'runtime.log').stat().st_size
            commands(setup); hold(2)
            commands(['getall AO2CharacterNative GlobalAggroRating',
                      'getall AO2CharacterNative AggroBlendLevel',
                      'getall AO2CharacterNative LastHasAggroTransparency'])
            hold(1); snapshot(name+'.png')
            with (out/'runtime.log').open('rb') as f:
                f.seek(offset); response = f.read().decode(errors='replace')
            (out/(name+'-state.log')).write_text(response)
            report['phases'].append({'name': name, 'commands': setup,
                                     'seconds': time.monotonic()-started,
                                     'state_file': name+'-state.log'})
            video = subprocess.Popen([sys.executable, str(root/'tools/window_video_probe.py'),
                '--pid', str(pid), '--output', str(out/(name+'-motion')), '--seconds', '10',
                '--ffmpeg', args.ffmpeg], creationflags=subprocess.CREATE_NO_WINDOW)
            # Exceed the right-stick dead zone. Record the game rotation on
            # either side of the turn rather than assuming injected input moved it.
            commands(['getall AO2PlayerController Rotation'])
            hold(3); hold(1, rx=16000); hold(.2)
            commands(['getall AO2PlayerController Rotation'])
            snapshot(name+'-turned.png')
            hold(1, rx=-16000); hold(.2)
            commands(['getall AO2PlayerController Rotation'])
            hold(6)
            if video.wait(timeout=30) != 0: raise RuntimeError('Motion capture failed')
            video = None
        hold(.1)
        child.wait(timeout=120)
        done = json.loads((out/'probe.json').read_text())
        travel = json.loads((out/'travel.json').read_text())
        if child.returncode != 0 or done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
            raise RuntimeError('Native run did not close normally')
        if not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']:
            raise RuntimeError('Source profile or checkpoint changed')
        if not any(e.get('checkpoint_reference_verified') == '01_02' for e in travel['events']):
            raise RuntimeError('Checkpoint verification missing')
        report['complete'] = True
    finally:
        if pad.exists(): pad.write_text('0000 0 0 0 0 0 0\n')
        if video is not None: video.wait(timeout=40)
        if child.poll() is None: child.wait(timeout=300)
        report['elapsed_seconds'] = time.monotonic()-started
        report['exit_code'] = child.returncode
        if out.exists(): (out/'non-aggro.json').write_text(json.dumps(report, indent=2)+'\n')
    print(out)


if __name__ == '__main__': main()
