"""Capture fresh impacts on the courtyard wall reported during campaign motion.

This is a visual diagnostic with copied saves. Recording and RenderDoc perturb
timing; neither route completion nor its frame times establish visual parity.
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
    parser.add_argument('--renderdoc', type=Path)
    parser.add_argument('--no-renderdoc', action='store_true', help='Run the route without GPU capture/injection')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--msaa-alignment', choices=('on', 'off'), help='Explicit experimental renderer override')
    parser.add_argument('--wall-projection', choices=('on', 'off'), help='Explicit static-group override for matched wall-impact comparisons')
    parser.add_argument('--draw-trace', action='store_true', help='Capture draw state also without RenderDoc')
    parser.add_argument('--capture-frames', type=int, default=24)
    parser.add_argument('--capture-after-ms', type=int, default=125000,
                        help='Capture elapsed GPU time; 120000 includes impact creation, 125000 follows the burst')
    args = parser.parse_args()
    root, out = Path(__file__).resolve().parents[1], args.output.resolve()
    if (out.exists() or not args.profile.is_dir() or not 1 <= args.capture_frames <= 60 or
            not 118000 <= args.capture_after_ms <= 135000):
        parser.error('Require new output, existing profile, 1-60 GPU frames and capture time 118000-135000ms')
    if not args.no_renderdoc and (args.renderdoc is None or
            not all((args.renderdoc / (name + '.exe')).is_file() for name in ('qrenderdoc', 'renderdoccmd'))):
        parser.error('Require a portable RenderDoc directory')
    env = dict(os.environ)
    for key in ('AOT_FRAME_PHASE_LOG', 'AOT_WAIT_LOG', 'AOT_RENDER_WAIT_LOG',
                'AOT_GPU_WAIT_LOG', 'AOT_GPU_VBLANK_LOG', 'AOT_GPU_VBLANK_WATCH',
                'AOT_GPU_COPY_LOG', 'AOT_RECT_LOG', 'AOT_TRACE_FONT', 'AOT_TRACE_HUD'):
        env.pop(key, None)
    command = [sys.executable, str(root / 'tools/checkpoint_probe.py'),
               '--output', str(out), '--profile', str(args.profile.resolve()),
               '--checkpoint', '01_02', '--verify-checkpoint', '--gpu-plugin', 'spatial',
               '--window', '1920', '1080', '--capture-interval', '30',
               '--cache-root', str(args.cache_root.resolve())]
    if not args.no_renderdoc:
        command += ['--renderdoc', str((args.renderdoc / 'renderdoccmd.exe').resolve())]
    command += ['--extra', '--aot_projection_precision=true']
    if args.wall_projection:
        command += ['--aot_wall_projection_precision=' + ('true' if args.wall_projection == 'on' else 'false')]
    if args.msaa_alignment:
        command += ['--aot_msaa_alignment=' + ('true' if args.msaa_alignment == 'on' else 'false')]
    if not args.no_renderdoc or args.draw_trace:
        command += ['--aot_renderdoc_capture=' + ('false' if args.no_renderdoc else 'true'),
                    '--aot_draw_capture_path=' + str(out / 'draws.csv'),
                    '--aot_draw_capture_after_ms=' + str(args.capture_after_ms),
                    '--aot_renderdoc_capture_frames=' + str(args.capture_frames)]
    child = subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    started, events, helpers, logs = time.monotonic(), [], [], []
    report = {'command': command, 'events': events, 'scope': __doc__.strip()}
    def wait_until(target):
        while time.monotonic() - started < target:
            if child.poll() is not None:
                raise RuntimeError('Checkpoint probe exited before route completion')
            time.sleep(.1)
    def helper(name, command, env=env):
        log = (out / (name + '.log')).open('w')
        logs.append(log)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        helpers.append((name, process))
    try:
        wait_until(100)
        pid = json.loads((out / 'running.json').read_text())['pid']
        report['pid'] = pid
        if not args.no_renderdoc:
            collector_env = dict(env, AOT_RENDERDOC_ROOT=str(root), AOT_RENDERDOC_PROBE=str(out),
                                 AOT_RENDERDOC_PASSIVE='1')
            helper('renderdoc-collector', [str((args.renderdoc / 'qrenderdoc.exe').resolve()),
                   '--python', str(root / 'tools/renderdoc_capture.py')], collector_env)
        with (out / 'game.commands').open('a') as f:
            f.write('god\ngetall AO2PlayerController bGodMode\n')
        route = [(106, ['--move', '6500', '0', '--seconds', '2']),
                 (110, ['--key', 'W', '--seconds', '4']),
                 (117, ['--move', '400', '0', '--seconds', '1']),
                 (121, ['--mouse-button', 'left', '--seconds', '2']),
                 (128, ['--move', '80', '0', '--seconds', '.8']),
                 (130, ['--move', '-80', '0', '--seconds', '.8'])]
        for target, options in route:
            wait_until(target)
            if target == 121:
                helper('video', [sys.executable, str(root / 'tools/window_video_probe.py'),
                       '--pid', str(pid), '--output', str(out / 'motion'), '--seconds', '24',
                       '--ffmpeg', args.ffmpeg])
            begin = time.monotonic() - started
            subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                            str(pid), '--focus', *options], check=True, timeout=15,
                           stdout=subprocess.DEVNULL)
            events.append({'wall_start_seconds': begin,
                           'wall_end_seconds': time.monotonic() - started, 'input': options})
            targets = game_windows(pid)
            if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3],
                                               out / f'route-{target}.png'):
                raise RuntimeError('Could not capture route state')
        wait_until(148)
    except BaseException as error:
        report['route_error'] = repr(error)
        raise
    finally:
        # The checkpoint probe owns normal close and the hard deadline.
        child.wait(timeout=230)
        report['checkpoint_helper_exit'] = child.returncode
        for name, process in helpers:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                report[name + '_timeout'] = True
            report[name + '_exit'] = process.returncode
        for log in logs:
            log.close()
        if out.exists():
            (out / 'courtyard-decal-scenario.json').write_text(json.dumps(report, indent=2) + '\n')
    if child.returncode or any(process.returncode for _, process in helpers):
        raise RuntimeError('Native or capture helper failed')
    if not args.no_renderdoc:
        gpu = json.loads((out / 'renderdoc-capture.json').read_text())
        if gpu.get('error') or len(gpu.get('captures', [])) != 1:
            raise RuntimeError('Require complete GPU capture')
    native = json.loads((out / 'probe.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Require normal native exit')
    print(out)


if __name__ == '__main__':
    main()
