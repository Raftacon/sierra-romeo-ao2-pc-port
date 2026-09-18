"""Compare light visibility at a verified checkpoint using copied profiles.

Camera inputs are wall-clock scripted, so use captures to establish alignment.
The query trace is sampled; this run is not an uninstrumented FPS benchmark.
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
    parser.add_argument('--checkpoint', default='05_00')
    parser.add_argument('--quick', action='store_true', help='Start camera observations after the verified 30-second load check')
    parser.add_argument('--sweep-steps', type=int, default=8, choices=range(1, 25))
    parser.add_argument('--pitch-stick', type=int, default=11000,
                        help='Right-stick Y for the first camera step; negative looks down')
    parser.add_argument('--fake', action='store_true', help='Negative control: force the old fixed visibility result')
    parser.add_argument('--batch', action=argparse.BooleanOptionalAction, default=None,
                        help='Override batching of real visibility-query readbacks')
    args = parser.parse_args()
    if not -32768 <= args.pitch_stick <= 32767:
        parser.error('--pitch-stick must fit a signed XInput axis')
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out.exists(): parser.error('Require a new output directory')
    env = {k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env['AOT_OCCLUSION_LOG'] = str(out / 'occlusion.csv')
    cmd = [sys.executable, str(root / 'tools/checkpoint_probe.py'), '--output', str(out),
           '--profile', str(args.profile.resolve()), '--checkpoint', args.checkpoint, '--verify-checkpoint',
           '--gpu-plugin', 'spatial', '--cache-root', str(root / 'artifacts/native-cache'),
           '--controller-only', '--window', '1280', '720', '--capture-interval', '0', '--log-level', 'info',
           '--observe-seconds', '240', '--finish-trigger', str(out / 'finish'),
           '--check-window', '30' if args.quick else '90', '--extra',
           '--aot_automatic_projection_precision=true', '--aot_projection_fma=true',
           '--occlusion_query_enable=' + str(not args.fake).lower()]
    if args.batch is not None:
        cmd.append('--aot_batch_occlusion_queries=' + str(args.batch).lower())
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    started = time.monotonic()
    events = []
    report = {'command':cmd, 'fake':args.fake, 'complete':False}
    def wait(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if process.poll() is not None: raise RuntimeError('Checkpoint process ended early')
            time.sleep(.05)
    def snapshot(name):
        windows = game_windows(pid)
        if len(windows) != 1: raise RuntimeError('Expected one native game window')
        h,_,w,height = windows[0]
        if not capture(h,w,height,out/name): raise RuntimeError('Capture failed')
        events.append({'seconds':time.monotonic()-started,'image':name})
    try:
        while not (out / ('after-travel-030.png' if args.quick else 'after-travel-090.png')).exists():
            wait(.2)
            if time.monotonic()-started > 240: raise RuntimeError('Verified checkpoint load timed out')
        pid = json.loads((out / 'running.json').read_text())['pid']
        wait(4)
        snapshot('sweep-start.png')
        for i in range(args.sweep_steps):
            state = '0000 0 0 0 0 19000 ' + (str(args.pitch_stick) if i == 0 else '0')
            events.append({'seconds':time.monotonic()-started,'state':state,'duration':.8})
            (out / 'controller.state').write_text(state+'\n')
            wait(.8)
            (out / 'controller.state').write_text('0000 0 0 0 0 0 0\n')
            wait(1)
            snapshot('sweep-%02d.png'%i)
        report['complete'] = True
    finally:
        if out.exists():
            (out / 'controller.state').write_text('0000 0 0 0 0 0 0\n')
            (out / 'finish').write_text('Camera observations finished\n')
        process.wait(timeout=180)
        report.update(exit_code=process.returncode,events=events,elapsed=time.monotonic()-started)
        (out / 'occlusion-scenario.json').write_text(json.dumps(report,indent=2)+'\n')
    if process.returncode: raise RuntimeError('Checkpoint probe failed')
    print(json.dumps({'output':str(out),'complete':report['complete'],'elapsed':report['elapsed']}))


if __name__ == '__main__': main()
