"""Reproduce the courtyard car's missing triangular material with copied saves.

Controller-only setup, queried checkpoint/pause/rotation, lossless motion and an
optional manual GPU trigger in one live session. Completion is not parity.
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
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--wall-projection', choices=('default','on','off'), default='default')
    parser.add_argument('--automatic-projection', action='store_true')
    parser.add_argument('--preserve-scene', action=argparse.BooleanOptionalAction, default=None,
                        help='Override scene preservation; omitted uses the runtime default')
    parser.add_argument('--vblank-wake', action=argparse.BooleanOptionalAction, default=None,
                        help='Override GPU VBlank notification; omitted uses the runtime default')
    parser.add_argument('--next-checkpoint', help='Optional verified destination after the car recording')
    args = parser.parse_args()
    root, out = Path(__file__).resolve().parents[1], args.output.resolve()
    if out.exists() or not args.profile.is_dir(): parser.error('Require new output and an existing copied-save source')
    if args.renderdoc and not all((args.renderdoc/(n+'.exe')).is_file() for n in ('renderdoccmd','qrenderdoc')):
        parser.error('Require RenderDoc capture and collector tools')
    command = [sys.executable,str(root/'tools/checkpoint_probe.py'),'--output',str(out),
        '--profile',str(args.profile.resolve()),'--checkpoint','01_02','--verify-checkpoint',
        '--gpu-plugin','spatial','--window','1920','1080','--controller-only',
        '--observe-seconds','120','--cache-root',str(args.cache_root.resolve())]
    if args.renderdoc: command += ['--renderdoc',str((args.renderdoc/'renderdoccmd.exe').resolve())]
    if args.next_checkpoint: command += ['--next-checkpoint',args.next_checkpoint]
    command += ['--extra','--aot_projection_precision=true']
    if args.preserve_scene is not None: command += ['--aot_preserve_scene_before_shadows='+str(args.preserve_scene).lower()]
    if args.vblank_wake is not None: command += ['--aot_gpu_vblank_wake='+str(args.vblank_wake).lower()]
    if args.automatic_projection: command += ['--aot_automatic_projection_precision=true']
    if args.wall_projection!='default': command += ['--aot_wall_projection_precision='+str(args.wall_projection=='on').lower()]
    if args.renderdoc:
        command += ['--aot_renderdoc_capture=true','--aot_draw_capture_path='+str(out/'draws.csv'),
                    '--aot_draw_capture_trigger_file='+str(out/'capture.trigger')]
    started = time.monotonic()
    report = {'command':command,'events':[],'complete':False}
    child = subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW)
    helpers, logs = [], []
    pad, previous = out/'controller.state', None
    def alive():
        if child.poll() is not None: raise RuntimeError('Owned checkpoint session exited')
    def event(**data):
        report['events'].append(dict(seconds=time.monotonic()-started,**data))
    def hold(seconds,rx=0):
        nonlocal previous
        value = '0000 0 0 0 0 '+str(rx)+' 0\n'; end = time.monotonic()+seconds
        event(duration=seconds,right_stick_x=rx)
        while time.monotonic()<end:
            alive()
            if value != previous: pad.write_text(value); previous = value
            else: os.utime(pad,None)
            time.sleep(.05)
    def snapshot(name):
        w = game_windows(pid)
        if len(w)!=1 or not capture(w[0][0],w[0][2],w[0][3],out/name): raise RuntimeError('Missing owned game window')
        event(image=name)
    def commands(lines):
        with (out/'game.commands').open('a') as f: f.write('\n'.join(lines)+'\n')
        event(commands=lines)
    def helper(name,cmd,env=None):
        log = open(out/(name+'.log'),'x'); logs.append(log)
        p = subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        helpers.append((name,p)); return p
    try:
        ready = "CurrCheckpoint = AO2Checkpoint'01_map_store_shell.TheWorld.PersistentLevel.AO2Checkpoint_4'"
        while time.monotonic()-started<125:
            alive()
            if (out/'runtime.log').exists() and ready in (out/'runtime.log').read_text(errors='replace'): break
            time.sleep(.2)
        else: raise RuntimeError('Required checkpoint was not verified')
        pid = json.loads((out/'running.json').read_text())['pid']; report['pid'] = pid
        if args.renderdoc:
            helper('collector',[str((args.renderdoc/'qrenderdoc.exe').resolve()),'--python',str(root/'tools/renderdoc_capture.py')],
                dict(os.environ,AOT_RENDERDOC_ROOT=str(root),AOT_RENDERDOC_PROBE=str(out),AOT_RENDERDOC_PASSIVE='1'))
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),str(pid),'--focus','--seconds','.1'],check=True,timeout=10)
        offset = (out/'runtime.log').stat().st_size
        commands(['getall WorldInfo Pauser','getall AO2PlayerController Rotation']); hold(1)
        with (out/'runtime.log').open('rb') as f: f.seek(offset); response = f.read().decode(errors='replace')
        if 'Pauser = None' not in response: raise RuntimeError('Game pause state not confirmed clear')
        snapshot('car-before-turn.png')
        # Same bounded right-stick route used to locate the visibly broken car.
        # Preserve neutral gaps and sample rotation; do not infer movement from input.
        for duration in (2,2.7,1.2,1): hold(duration,24000); hold(.3)
        commands(['getall AO2PlayerController Rotation','getall WorldInfo Pauser']); hold(1)
        snapshot('car-before-capture.png')
        video = helper('video',[sys.executable,str(root/'tools/window_video_probe.py'),'--pid',str(pid),
            '--output',str(out/'car-motion'),'--seconds','12','--ffmpeg',args.ffmpeg])
        if args.renderdoc:
            alive()
            with (out/'capture.trigger').open('x') as f: f.write(str(time.time()))
            event(capture_trigger=True)
        hold(3); hold(.4,16000); hold(.3); hold(.4,-16000); hold(9)
        commands(['getall AO2PlayerController Rotation'])
        snapshot('car-after-capture.png')
        if video.wait(timeout=15)!=0: raise RuntimeError('Motion recording failed')
        child.wait(timeout=130)
        done = json.loads((out/'probe.json').read_text()); travel = json.loads((out/'travel.json').read_text())
        if child.returncode or done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise RuntimeError('Native run did not close normally')
        if not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']: raise RuntimeError('Source saves changed')
        for name,p in helpers:
            if p.wait(timeout=30)!=0: raise RuntimeError('Failed '+name)
        if args.renderdoc:
            gpu = json.loads((out/'renderdoc-capture.json').read_text())
            if gpu.get('error') or len(gpu.get('captures',[]))!=1: raise RuntimeError('Expected one collected GPU capture')
        report['complete'] = True
    finally:
        if pad.exists(): pad.write_text('0000 0 0 0 0 0 0\n')
        if child.poll() is None: child.wait(timeout=260)
        for name,p in helpers:
            try: p.wait(timeout=30)
            except subprocess.TimeoutExpired: p.kill(); p.wait(); report[name+'_timeout'] = True
        for log in logs: log.close()
        report['elapsed_seconds'] = time.monotonic()-started
        if out.exists(): (out/'car-scenario.json').write_text(json.dumps(report,indent=2)+'\n')
    print(out)


if __name__ == '__main__': main()
