"""Trace a bounded Somalia movement/fire route using a copied test profile."""
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
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--turn-around', action='store_true', help='Turn away from the starting ladder before moving through the courtyard')
    parser.add_argument('--gpu-plugin', choices=('xenos','spatial'), default='xenos', help='Select the actual renderer under investigation')
    parser.add_argument('--cache-root', type=Path, help='Separate shader cache for the selected renderer')
    parser.add_argument('--gpu-timing', action='store_true', help='Trace packet waits and VBlank callbacks in the spatial renderer')
    parser.add_argument('--gpu-vblank-watch', type=lambda s: int(s, 0), help='Optional aligned physical word to observe across callbacks')
    args = parser.parse_args()
    if args.gpu_timing and args.gpu_plugin != 'spatial':
        parser.error('--gpu-timing requires spatial renderer')
    if args.gpu_vblank_watch is not None and (not args.gpu_timing or
            not 0 <= args.gpu_vblank_watch <= 0x1FFFFFFC or args.gpu_vblank_watch % 4):
        parser.error('Require GPU timing and an aligned physical watch address below 512 MiB')
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    if out.exists() or not source.is_dir():
        parser.error('Use a new output directory and an existing test profile')
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    original, events = hashes(), []
    env = dict(os.environ)
    for key in ('AOT_RENDER_WAIT_LOG', 'AOT_GPU_WAIT_LOG', 'AOT_GPU_COPY_LOG',
                'AOT_GPU_VBLANK_LOG', 'AOT_GPU_VBLANK_WATCH', 'AOT_RECT_LOG',
                'AOT_TRACE_FONT', 'AOT_TRACE_HUD', 'AOT_TRACE_COOP_TEXTURE'):
        env.pop(key, None)
    env['AOT_FRAME_PHASE_LOG'] = str(out / 'phases.csv')
    env['AOT_WAIT_LOG'] = str(out / 'waits.csv')
    env['AOT_RENDER_WAIT_LOG'] = str(out / 'render-waits.csv')
    if args.gpu_timing:
        env['AOT_GPU_WAIT_LOG'] = str(out / 'gpu-waits.csv')
        env['AOT_GPU_VBLANK_LOG'] = str(out / 'gpu-vblank.csv')
        if args.gpu_vblank_watch is not None:
            env['AOT_GPU_VBLANK_WATCH'] = hex(args.gpu_vblank_watch)
    command = [sys.executable, str(root / 'tools/checkpoint_probe.py'),
               '--output', str(out), '--profile', str(source), '--checkpoint', '01_02',
               '--capture-interval', '30', '--observe-seconds', '150', '--verify-checkpoint',
               '--gpu-plugin', args.gpu_plugin]
    if args.gpu_plugin=='spatial':
        command += ['--window', '1920', '1080']
    if args.cache_root:
        command += ['--cache-root', str(args.cache_root.resolve())]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    started = time.perf_counter()
    def wait_until(target):
        while time.perf_counter() - started < target:
            if process.poll() is not None:
                raise RuntimeError('Checkpoint probe exited during the scenario')
            time.sleep(.1)
    def snapshot(name):
        targets = windows(pid)
        if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / name):
            raise RuntimeError('Could not capture native scene')
        events.append({'wall_seconds': time.perf_counter() - started, 'capture': name})
    try:
        wait_until(100)
        pid = json.loads((out / 'running.json').read_text())['pid']
        with (out / 'game.commands').open('a') as f:
            f.write('god\ngetall AO2PlayerController bGodMode\ngetall AO2PlayerController Rotation\n')
        events.append({'wall_seconds': time.perf_counter() - started,
                       'commands': 'god; query bGodMode and Rotation'})
        # No timing claim assumes these inputs complete a scripted objective.
        # Endpoint captures establish the actual route and tutorial/menu state.
        route = [(110, ['--key', 'W', '--seconds', '4']),
                 (117, ['--move', '400', '0', '--seconds', '1']),
                 (121, ['--mouse-button', 'left', '--seconds', '2']),
                 (129, ['--key', 'D', '--seconds', '3']),
                 (136, ['--key', 'W', '--seconds', '4']),
                 (144, ['--move', '-800', '0', '--seconds', '2']),
                 (150, ['--mouse-button', 'left', '--seconds', '2']),
                 (158, ['--key', 'R', '--seconds', '.15']),
                 (162, ['--key', 'A', '--seconds', '3']),
                 (169, ['--key', 'S', '--seconds', '4']),
                 (177, ['--move', '400', '0', '--seconds', '1']),
                 (182, ['--mouse-button', 'left', '--seconds', '2']),
                 (190, ['--key', 'W', '--seconds', '4'])]
        if args.turn_around:
            route.insert(0, (106, ['--move', '6500', '0', '--seconds', '2']))
        snapshot('motion-start.png')
        for target, options in route:
            wait_until(target)
            begin = time.perf_counter() - started
            subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                            str(pid), '--focus', *options], check=True,
                           stdout=subprocess.DEVNULL, timeout=15)
            events.append({'wall_start_seconds': begin,
                           'wall_end_seconds': time.perf_counter() - started, 'input': options})
            if target in (121, 150, 190):
                snapshot(f'motion-{target}.png')
        wait_until(210)
        snapshot('motion-end.png')
    finally:
        process.wait(timeout=300)
        if out.exists():
            (out / 'motion-scenario.json').write_text(json.dumps({
                'events': events, 'source_profile_sha256': original,
                'steady_clock_origin_ms': started * 1000, 'turn_around': args.turn_around,
                'gpu_plugin': args.gpu_plugin,
                'gpu_timing': args.gpu_timing, 'gpu_vblank_watch': args.gpu_vblank_watch,
                'source_profile_unchanged': hashes() == original,
                'scope': 'Scripted startup plus Windows keyboard/mouse; no physical controller driver. Inspect scenes before interpreting timing.'
            }, indent=2) + '\n')
    report = json.loads((out / 'probe.json').read_text())
    if process.returncode or report['timed_out'] or report['exit_code_before_cleanup'] != 0 or hashes() != original:
        raise RuntimeError('Native completion or profile preservation failed')
    print(out)


if __name__ == '__main__':
    main()
