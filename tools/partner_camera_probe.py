"""Verify original partner-camera toggling at a retail checkpoint on a copied save.

Records three stationary timing windows (initial, toggled, restored). These are
short within-run observations, not a rendering-parity or campaign-wide FPS test.
"""
import argparse
import csv
import ctypes as c
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

from inspect_checkpoint import checkpoint_metadata
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--checkpoint', default='01_02')
    parser.add_argument('--scale', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--gpu-plugin', default='xenos')
    parser.add_argument('--upscaler', choices=('bilinear', 'fsr'), default='bilinear')
    parser.add_argument('--window', type=int, nargs=2, default=(1280, 720), metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--gpu-telemetry', action='store_true', help='Record device-wide NVIDIA utilization once per second')
    args = parser.parse_args()
    if args.upscaler != 'bilinear' and args.gpu_plugin != 'spatial':
        parser.error('FSR requires --gpu-plugin=spatial')
    if not all(320 <= value <= 4096 for value in args.window):
        parser.error('Window dimensions must be in [320, 4096]')
    smi = shutil.which('nvidia-smi') if args.gpu_telemetry else None
    if args.gpu_telemetry and not smi:
        parser.error('--gpu-telemetry requires nvidia-smi')
    root = Path(__file__).resolve().parents[1]
    source, out = args.profile.resolve(), args.output.resolve()
    profile = out.with_name(out.name + '-profile')
    if out.exists() or profile.exists() or not source.is_dir() or source in profile.parents or profile in source.parents:
        parser.error('Use new output/profile paths and a separate existing source profile')
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.checkpoint):
        parser.error('Invalid checkpoint name')
    checkpoint = root / 'assets/AO2Game/Checkpoints' / args.checkpoint
    expected = checkpoint_metadata(checkpoint)

    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}

    original = hashes()
    shutil.copytree(source, profile)
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-graphics.script'),
               AOT_INPUT_STATE=str(out / 'controller.state'), AOT_FRAME_LOG=str(out / 'frame-times.csv'),
               AOT_GAME_COMMANDS=str(out / 'game.commands'))
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(out),
               '--gpu-plugin', args.gpu_plugin,
               '--user-data', str(profile), '--seconds', '240', '--capture-interval', '300', '--use-saved-settings',
               '--', '--input_backend=xinput', '--readback_resolve=fast',
               '--vsync=true', '--aot_fps=60', '--resolution_scale=' + str(args.scale),
               '--fullscreen=false', '--window_width=' + str(args.window[0]), '--window_height=' + str(args.window[1]),
               '--bind_debug_overlay=None', '--bind_console=None', '--bind_settings=None', '--bind_achievements=None',
               '--swap_post_effect=fxaa', '--anisotropic_override=5',
               '--readback_resolve_half_pixel_offset=true']
    if args.gpu_plugin == 'spatial':
        command += ['--aot_spatial_upscale=' + str(args.upscaler == 'fsr').lower(),
                    '--aot_upscale_output_size=' + 'x'.join(map(str, args.window))]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    started, pid, events, phases = time.monotonic(), None, [], []
    success = False
    measuring = None
    telemetry, telemetry_file = None, None
    c.windll.user32.GetForegroundWindow.restype = c.c_void_p

    def record(**event):
        events.append({'wall_seconds': time.monotonic() - started, **event})

    def wait(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError('Native probe exited before observation completed')
            if measuring is not None and c.windll.user32.GetForegroundWindow() != measuring['hwnd']:
                measuring['focus_lost'] = True
            time.sleep(.1)

    def send(command):
        with (out / 'game.commands').open('a') as stream:
            stream.write(command + '\n')
        record(command=command)

    def query(command, pattern):
        log = out / 'runtime.log'
        offset = len(log.read_text(errors='replace'))
        send(command)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            matches = re.findall(pattern, log.read_text(errors='replace')[offset:])
            if matches:
                record(query=command, responses=matches)
                return matches
            wait(.1)
        raise RuntimeError('No recognized query response: ' + command)

    def verify_checkpoint():
        found = query('getall AO2CheckpointManager CurrCheckpoint',
                      r"CurrCheckpoint = AO2Checkpoint'([^']+)'")
        if set(found) != {expected['actor_path']}:
            raise RuntimeError('Requested checkpoint actor is not active: ' + repr(found))

    def camera_state():
        found = query('getall AO2PlayerController bUsePartnerPIP', r'bUsePartnerPIP = (True|False)')
        if len(found) != 1:
            raise RuntimeError('Expected exactly one player controller camera state')
        return found[0] == 'True'

    def rotation():
        found = query('getall AO2PlayerController Rotation', r'Rotation = (\([^\r\n]+\))')
        if len(found) != 1:
            raise RuntimeError('Expected exactly one player controller rotation')
        return found[0]

    def snapshot(name):
        found = windows(pid)
        if len(found) != 1 or not capture(found[0][0], found[0][2], found[0][3], out / name):
            raise RuntimeError('Could not capture owned native window')
        record(capture=name)

    def frame_marker():
        # Frame CSV flushes every 60 frames. Use only complete flushed rows and
        # settle for two seconds before each window, excluding captures/input.
        text = (out / 'frame-times.csv').read_text()
        rows = list(csv.DictReader(text[:text.rfind('\n') + 1].splitlines()))
        if not rows:
            raise RuntimeError('No completed frame timing rows')
        return int(rows[-1]['frame'])

    def settled_frame_marker():
        # At low FPS the 60-frame flush interval may exceed the settle delay.
        # Wait for a new flush after settling, not merely the latest old row.
        previous = frame_marker()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            wait(.1)
            current = frame_marker()
            if current > previous:
                return current
        raise RuntimeError('Frame timing did not advance after settling')

    try:
        wait(75)
        pid = json.loads((out / 'running.json').read_text())['pid']
        if smi:
            telemetry_file = (out / 'gpu-telemetry.csv').open('w')
            telemetry = subprocess.Popen([smi,
                '--query-gpu=timestamp,index,name,utilization.gpu,memory.used,power.draw,clocks.current.graphics',
                '--format=csv', '--loop=1'], stdout=telemetry_file, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
        send('open Checkpoint?LoadSaveGame?CheckpointToLoad=' + args.checkpoint + '?Difficulty=1')
        wait(35)
        verify_checkpoint()
        subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                        str(pid), '--focus', '--seconds', '.1'],
                       check=True, timeout=10, stdout=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        record(focus='owned window before every measurement; no keyboard/mouse driver')
        initial = camera_state()
        for index, label in enumerate(('initial', 'toggled', 'restored')):
            if index:
                # Use the test controller so desktop mouse movement cannot
                # change the game camera while comparing these views.
                (out / 'controller.state').write_text('0002 0 0 0 0 0 0\n')
                try:
                    wait(.2)
                finally:
                    (out / 'controller.state').write_text('0000 0 0 0 0 0 0\n')
                record(input='scripted D-pad Down', phase=label)
                wait(2)
            active = camera_state()
            if active != (not initial if index == 1 else initial):
                raise RuntimeError('Original input did not toggle/restore partner-camera state')
            snapshot(label + '.png')
            initial_rotation = rotation()
            wait(2)
            start_frame = settled_frame_marker()
            phase = {'name': label, 'camera_enabled': active, 'start_frame_exclusive': start_frame,
                     'start_marker_after_settle': True,
                     'wall_start_seconds': time.monotonic() - started,
                     'local_start': datetime.now().isoformat(timespec='milliseconds'),
                     'hwnd': windows(pid)[0][0], 'focus_lost': False,
                     'rotation_before': initial_rotation}
            measuring = phase
            wait(20)
            measuring = None
            phase.update(end_frame_inclusive=frame_marker(), wall_end_seconds=time.monotonic() - started)
            phase['local_end'] = datetime.now().isoformat(timespec='milliseconds')
            phase['rotation_after'] = rotation()
            phase['rotation_unchanged'] = phase['rotation_before'] == phase['rotation_after']
            phases.append(phase)
        verify_checkpoint()
        snapshot('end.png')
        success = True
    finally:
        if telemetry is not None:
            if telemetry.poll() is None:
                telemetry.terminate()
            telemetry.wait(timeout=10)
        if telemetry_file is not None:
            telemetry_file.close()
        if pid is not None:
            found = windows(pid)
            if len(found) == 1:
                c.windll.user32.PostMessageW(c.c_void_p(found[0][0]), 0x10, 0, 0)
        process.wait(timeout=250)
        if out.exists():
            report = {'checkpoint': expected, 'scale': args.scale, 'window': args.window,
                      'upscaler': args.upscaler,
                      'gpu_telemetry': bool(smi), 'events': events, 'phases': phases,
                      'state_sequence_verified': success,
                      'source_profile_sha256': original, 'source_profile_unchanged': hashes() == original,
                      'checkpoint_unchanged': checkpoint_metadata(checkpoint) == expected,
                      'scope': __doc__.strip()}
            (out / 'partner-camera.json').write_text(json.dumps(report, indent=2) + '\n')
    native = json.loads((out / 'probe.json').read_text())
    if process.returncode or native['timed_out'] or native['exit_code_before_cleanup'] != 0 or not report['source_profile_unchanged'] or not report['checkpoint_unchanged']:
        raise RuntimeError('Native completion or source preservation failed')
    with (out / 'frame-times.csv').open() as stream:
        frames = [(int(row['frame']), float(row['interval_ms'])) for row in csv.DictReader(stream)]
    for phase in phases:
        values = [ms for frame, ms in frames if phase['start_frame_exclusive'] < frame <= phase['end_frame_inclusive']]
        if not values or not all(0 < value < float('inf') for value in values):
            raise RuntimeError('Missing or invalid measurement frames')
        ordered = sorted(values)
        phase.update(frames=len(values), covered_seconds=sum(values) / 1000,
                     mean_ms=statistics.mean(values), p95_ms=ordered[int(.95 * len(ordered))],
                     p99_ms=ordered[int(.99 * len(ordered))], max_ms=max(values),
                     over_25ms=sum(value > 25 for value in values))
    (out / 'partner-camera.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(phases, indent=2))


if __name__ == '__main__':
    main()
