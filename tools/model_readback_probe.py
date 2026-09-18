"""Compare stationary training characters with fast or synchronous GPU readback.

Copies a test profile and records a lossless character crop. Completion does not
establish absence of flicker or exact state parity between separate runs.
"""
import argparse
import ctypes as c
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
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--readback', required=True, choices=['fast', 'full'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    profile = out.with_name(out.name + '-profile')
    if out.exists() or profile.exists() or not source.is_dir() or source in profile.parents:
        parser.error('Use new output/profile paths and a separate existing test profile')
    def hashes():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    original = hashes()
    shutil.copytree(source, profile)
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOT_')}
    env['AOT_INPUT_SCRIPT'] = str(root / 'config/input-graphics.script')
    env['AOT_FRAME_LOG'] = str(out / 'frame-times.csv')
    command = [sys.executable, str(root / 'tools/probe.py'), '--output', str(out),
               '--user-data', str(profile), '--seconds', '135', '--capture-interval', '150',
               '--', '--input_backend=xinput', '--readback_resolve=' + args.readback,
               '--vsync=true', '--aot_fps=60', '--resolution_scale=1',
               '--swap_post_effect=fxaa', '--anisotropic_override=5']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    started, pid, events = time.monotonic(), None, []
    try:
        while time.monotonic() - started < 90:
            if process.poll() is not None:
                raise RuntimeError('Native probe exited before the recording')
            time.sleep(.1)
        pid = json.loads((out / 'running.json').read_text())['pid']
        def snapshot(name):
            targets = windows(pid)
            if len(targets) != 1 or not capture(targets[0][0], targets[0][2], targets[0][3], out / name):
                raise RuntimeError('Could not capture native character view')
        snapshot('before.png')
        events.append({'event': 'recording_start', 'wall_seconds': time.monotonic() - started})
        subprocess.run([sys.executable, str(root / 'tools/window_video_probe.py'),
                        '--pid', str(pid), '--output', str(out / 'characters'), '--seconds', '5',
                        '--crop', '650', '255', '420', '480'], check=True, timeout=40,
                       stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        events.append({'event': 'recording_end', 'wall_seconds': time.monotonic() - started})
        snapshot('after.png')
    finally:
        if pid is not None:
            targets = windows(pid)
            if len(targets) == 1:
                c.windll.user32.PostMessageW(c.c_void_p(targets[0][0]), 0x10, 0, 0)
        process.wait(timeout=60)
        if out.exists():
            (out / 'model-scenario.json').write_text(json.dumps({
                'readback': args.readback, 'events': events,
                'source_profile_sha256': original, 'source_profile_unchanged': hashes() == original,
                'scope': __doc__.strip()
            }, indent=2) + '\n')
    native = json.loads((out / 'probe.json').read_text())
    if process.returncode or native['timed_out'] or native['exit_code_before_cleanup'] != 0 or hashes() != original:
        raise RuntimeError('Native completion or source-profile preservation failed')
    print(out)


if __name__ == '__main__':
    main()
