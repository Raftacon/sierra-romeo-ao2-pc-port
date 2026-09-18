"""Observe the fresh-campaign tutorial movie with bounded native captures."""
import argparse
from ctypes import wintypes, windll
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from probe import windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--guest-height', type=int, choices=[720, 1080], default=720)
    parser.add_argument('--scale', type=int, choices=[1, 2, 3], default=1)
    parser.add_argument('--readback', choices=['fast', 'full'], default='fast')
    parser.add_argument('--seconds', type=int, default=200)
    args = parser.parse_args()
    if not 150 <= args.seconds <= 300:
        parser.error('--seconds must be between 150 and 300')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists(): parser.error('Use a new output path; the probe creates its own fresh profile')
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-new-campaign.script'),
               AOT_GAME_COMMANDS=str(output / 'game.commands'), AOT_TEST_KBM='1')
    width = args.guest_height * 16 // 9
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(output),
               '--seconds', str(args.seconds + 40), '--capture-interval', '1',
               '--use-saved-settings', '--fullscreen', '--',
               f'--window_width={width}', f'--window_height={args.guest_height}',
               f'--video_mode_width={width}', f'--video_mode_height={args.guest_height}',
               '--input_backend=xinput', '--readback_resolve=' + args.readback,
               '--vsync=true', '--aot_fps=60', f'--resolution_scale={args.scale}',
               '--swap_post_effect=fxaa', '--anisotropic_override=5',
               '--readback_resolve_half_pixel_offset=true']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    pid = None
    last_query = 0
    queries = []
    try:
        while process.poll() is None and time.monotonic() - start < args.seconds:
            now = time.monotonic() - start
            if pid is None and (output / 'running.json').exists():
                pid = json.loads((output / 'running.json').read_text())['pid']
                print('Native PID', pid, flush=True)
            if pid is not None and now > 55 and now - last_query > 2:
                with (output / 'game.commands').open('a') as commands:
                    commands.write('getall SeqAct_Interp bIsPlaying\n')
                queries.append(now)
                last_query = now
            time.sleep(.1)
        if process.poll() is None and pid:
            for hwnd, *_ in windows(pid):
                windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
    finally:
        process.wait(timeout=55)
        if output.exists():
            (output / 'movie-survey.json').write_text(json.dumps({
                'query_seconds': queries, 'fresh_profile': True,
                'interpretation': 'Read-only cinematic queries; inspect actual movie state and captures.'
            }, indent=2) + '\n')
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Native probe did not close normally; inspect probe.json')
    print(output)


if __name__ == '__main__': main()
