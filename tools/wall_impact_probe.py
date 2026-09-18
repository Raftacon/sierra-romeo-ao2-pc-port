"""Bounded wall/decal capture using a copied profile and scripted XInput.

Inspect the images to establish actual impacts. A completed script is not a
visual-parity pass. Optional RenderDoc injection captures a selected GPU interval
and perturbs timing; do not interpret this route as a performance test.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from probe import capture, game_windows
from inspect_checkpoint import checkpoint_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--renderdoc', type=Path, help='Portable RenderDoc directory')
    parser.add_argument('--cache-root', type=Path, help='Separate shader cache for a controlled comparison')
    parser.add_argument('--projection-precision', action='store_true', help='Enable guarded projection correction for this probe; otherwise explicitly disable it for comparison')
    parser.add_argument('--wall-projection-precision', action='store_true', help='Explicitly enable the shared static group for this comparison')
    parser.add_argument('--automatic-projection', action='store_true', help='Use arithmetic recognition instead of the legacy shader groups')
    parser.add_argument('--ffmpeg', default='ffmpeg', help='FFmpeg executable for lossless capture')
    parser.add_argument('--no-video', action='store_true', help='Keep screenshots/GPU capture without the large lossless window recording')
    parser.add_argument('--checkpoint', default='01_04')
    parser.add_argument('--turn-x', type=int, default=-24000)
    parser.add_argument('--turn-seconds', type=float, default=.65)
    parser.add_argument('--approach-seconds', type=float, default=0)
    parser.add_argument('--fire-seconds', type=float, default=.6, help='Zero inspects existing wall marks')
    parser.add_argument('--fire-at-ms', type=int, default=0,
                        help='Optional elapsed launch time to begin firing, for a capture during the aggro transition')
    parser.add_argument('--no-aim', action='store_true', help='Keep the ordinary third-person view during recording and sweeps')
    parser.add_argument('--sweep-seconds', type=float, default=2)
    parser.add_argument('--sweep-cycles', type=int, default=1)
    parser.add_argument('--capture-after-ms', type=int, default=132000,
                        help='GPU diagnostic elapsed time for the optional RenderDoc frame')
    parser.add_argument('--capture-frames', type=int, default=1,
                        help='One RenderDoc capture spanning 1-120 guest frames; first-frame CSV only')
    args = parser.parse_args()
    if args.wall_projection_precision and not args.projection_precision:
        parser.error('--wall-projection-precision requires --projection-precision')
    if args.automatic_projection and not args.projection_precision:
        parser.error('--automatic-projection requires --projection-precision')
    if args.fire_at_ms and not 110000 <= args.fire_at_ms <= 130000:
        parser.error('--fire-at-ms must be zero or between 110000 and 130000')
    root, out = Path(__file__).resolve().parents[1], args.output.resolve()
    checkpoint = root / 'assets/AO2Game/Checkpoints' / args.checkpoint
    if (Path(args.checkpoint).name != args.checkpoint or not checkpoint.is_file() or
            not -32768 <= args.turn_x <= 32767 or not 0 <= args.turn_seconds <= 2 or
            not 0 <= args.approach_seconds <= 5 or not 0 <= args.fire_seconds <= 8 or
            not 0 < args.sweep_seconds <= 2 or not 1 <= args.sweep_cycles <= 5 or
            args.sweep_seconds * args.sweep_cycles > 2 or not 115000 <= args.capture_after_ms <= 145000):
        parser.error('Require existing checkpoint, bounded stick, turn <=2s, approach <=5s, fire <=8s, 1-5 sweep cycles totaling <=4s, and capture time 115000-145000ms')
    actor = checkpoint_metadata(checkpoint)['actor_path']
    if out.exists():
        parser.error('Use a new output directory')
    if args.renderdoc and not all((args.renderdoc / f'{name}.exe').is_file()
                                  for name in ('qrenderdoc', 'renderdoccmd')):
        parser.error('RenderDoc directory must contain qrenderdoc and renderdoccmd')
    if not 1 <= args.capture_frames <= 120 or (args.capture_frames != 1 and not args.renderdoc):
        parser.error('--capture-frames requires RenderDoc and a count between 1 and 120')
    command = [sys.executable, str(root / 'tools/checkpoint_probe.py'),
               '--output', str(out), '--profile', str(args.profile.resolve()),
               '--checkpoint', args.checkpoint, '--verify-checkpoint', '--gpu-plugin', 'spatial',
               '--window', '1920', '1080', '--controller-only', '--capture-interval', '30']
    if args.cache_root:
        command += ['--cache-root', str(args.cache_root.resolve())]
    extra = ['--aot_projection_precision=' + str(args.projection_precision).lower()]
    extra += ['--aot_wall_projection_precision=' + str(args.wall_projection_precision).lower()]
    extra += ['--aot_automatic_projection_precision=' + str(args.automatic_projection).lower()]
    if args.renderdoc:
        command += ['--renderdoc', str((args.renderdoc / 'renderdoccmd.exe').resolve())]
        extra += ['--aot_renderdoc_capture=true',
                    '--aot_draw_capture_path=' + str(out / 'draws.csv'),
                    '--aot_draw_capture_after_ms=' + str(args.capture_after_ms)]
        extra += ['--aot_renderdoc_capture_frames=' + str(args.capture_frames)]
    command += ['--extra', *extra]
    started, events = time.monotonic(), []
    child = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    collector, video, pid = None, None, None
    report = {'command': command, 'events': events, 'scope': __doc__.strip(),
              'no_aim': args.no_aim, 'lossless_video': not args.no_video}
    last_pad_text = None
    aim_trigger = 0 if args.no_aim else 255

    def alive():
        if child.poll() is not None:
            raise RuntimeError('Checkpoint probe exited before scenario completion')

    def hold(seconds, lt=0, rt=0, rx=0, ly=0):
        nonlocal last_pad_text
        events.append({'wall_seconds': time.monotonic() - started,
                       'duration': seconds, 'lt': lt, 'rt': rt, 'rx': rx, 'ly': ly})
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            alive()
            pad_path = out / 'controller.state'
            pad_text = f'0000 {lt} {rt} 0 {ly} {rx} 0\n'
            if pad_text != last_pad_text:
                # The runtime can deny atomic replacement on Windows. Change
                # data only at input transitions; do not repeatedly truncate a
                # held state, which briefly makes ReadLivePad return neutral.
                pad_path.write_text(pad_text)
                last_pad_text = pad_text
            else:
                # Refresh the 1.5-second watchdog without exposing empty data.
                os.utime(pad_path, None)
            time.sleep(.05)

    def snapshot(name):
        targets = game_windows(pid)
        if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / name):
            raise RuntimeError('Expected one capturable game window')
        events.append({'wall_seconds': time.monotonic() - started, 'capture': name})

    try:
        ready = f"CurrCheckpoint = AO2Checkpoint'{actor}'"
        while time.monotonic() - started < 115:
            alive()
            log = out / 'runtime.log'
            if log.exists() and ready in log.read_text(errors='replace'):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Verified checkpoint not ready before scenario deadline')
        pid = json.loads((out / 'running.json').read_text())['pid']
        report['pid'] = pid
        if args.renderdoc:
            env = dict(os.environ, AOT_RENDERDOC_ROOT=str(root),
                       AOT_RENDERDOC_PROBE=str(out), AOT_RENDERDOC_PASSIVE='1')
            collector = subprocess.Popen([str((args.renderdoc / 'qrenderdoc.exe').resolve()),
                '--python', str(root / 'tools/renderdoc_capture.py')], env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'), str(pid),
                        '--focus', '--seconds', '.1'], check=True, timeout=10,
                       stdout=subprocess.DEVNULL)
        hold(args.turn_seconds, rx=args.turn_x)
        hold(args.approach_seconds, ly=24000)
        hold(1)
        snapshot('wall-before-aim.png')
        hold(2, lt=aim_trigger)
        snapshot('wall-before-fire.png')
        if args.fire_at_ms:
            hold(max(0, args.fire_at_ms / 1000 - (time.monotonic() - started)), lt=aim_trigger)
        if not args.no_video:
            video = subprocess.Popen([sys.executable, str(root / 'tools/window_video_probe.py'),
                '--pid', str(pid), '--output', str(out / 'motion'), '--seconds', '24',
                '--ffmpeg', args.ffmpeg], creationflags=subprocess.CREATE_NO_WINDOW)
        hold(args.fire_seconds, lt=aim_trigger, rt=255)
        hold(3, lt=aim_trigger)
        snapshot('wall-after-fire.png')
        for _ in range(args.sweep_cycles):
            hold(args.sweep_seconds, lt=aim_trigger, rx=10000)
            hold(args.sweep_seconds, lt=aim_trigger, rx=-10000)
        hold(3, lt=aim_trigger)
        snapshot('wall-after-sweep.png')
        # Keep the requested view mode through the GPU capture window.
        hold(max(1, 143 - (time.monotonic() - started)), lt=aim_trigger)
        snapshot('wall-stationary.png')
        if video is not None:
            video.wait(timeout=10)
            if video.returncode:
                raise RuntimeError('Lossless recording failed')
    finally:
        if out.exists():
            (out / 'controller.state').write_text('0000 0 0 0 0 0 0\n')
        # The checkpoint helper owns normal WM_CLOSE and a separate hard deadline.
        child.wait(timeout=230)
        for owned in (video, collector):
            if owned is not None:
                try:
                    owned.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    owned.kill()
                    owned.wait()
                    report['helper_timeout'] = True
        if out.exists():
            report['checkpoint_helper_exit'] = child.returncode
            (out / 'wall-scenario.json').write_text(json.dumps(report, indent=2) + '\n')
    if child.returncode or report.get('helper_timeout'):
        raise RuntimeError('Native scenario or helper completion failed')
    if args.renderdoc:
        gpu = json.loads((out / 'renderdoc-capture.json').read_text())
        if gpu.get('error') or len(gpu.get('captures', [])) != 1:
            raise RuntimeError('Expected one complete GPU capture')
    print(out)


if __name__ == '__main__':
    main()
