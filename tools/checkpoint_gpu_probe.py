"""Capture a GPU frame after verifying any named retail checkpoint.

Uses a copied profile and the existing owned-process capture bridge. A completed
capture is evidence for draw inspection, not proof of campaign or visual parity.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from inspect_checkpoint import checkpoint_metadata
from probe import capture, game_windows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--cache-root', type=Path, required=True)
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--renderdoc', type=Path, required=True)
    p.add_argument('--fullscreen', action='store_true')
    p.add_argument('--post-effect', choices=('fxaa','fxaa_extreme','none'), default='fxaa')
    p.add_argument('--terrain-impacts', action='store_true',
                   help='Reproduce the bounded courtyard ground-fire survey before capture')
    p.add_argument('--explosive-impacts', action='store_true',
                   help='After terrain fire, select the grenade wheel sector and attempt one throw')
    p.add_argument('--explosion-video', type=Path, help='FFmpeg executable for a bounded throw recording')
    p.add_argument('--source-snapshots', action='store_true', help='Opt in to calculated-source correction for this native comparison')
    p.add_argument('--post-impact-sweep', action='store_true', help='Turn the camera after capture while the throw recording continues')
    a = p.parse_args()
    if a.explosive_impacts and not a.terrain_impacts:
        p.error('--explosive-impacts requires --terrain-impacts')
    if a.explosion_video and (not a.explosive_impacts or not a.explosion_video.is_file()):
        p.error('--explosion-video requires --explosive-impacts and an existing FFmpeg executable')
    if a.post_impact_sweep and not a.explosion_video:
        p.error('--post-impact-sweep requires --explosion-video')
    root = Path(__file__).resolve().parents[1]
    checkpoint = root / 'assets/AO2Game/Checkpoints' / a.checkpoint
    if (a.output.exists() or not a.profile.is_dir() or
            checkpoint.parent.resolve() != (root/'assets/AO2Game/Checkpoints').resolve() or
            not checkpoint.is_file()):
        p.error('Require a new output, existing profile and local retail checkpoint name')
    if not all((a.renderdoc/(n+'.exe')).is_file() for n in ('qrenderdoc','renderdoccmd')):
        p.error('Require both RenderDoc tools')
    out = a.output.resolve()
    identity = checkpoint_metadata(checkpoint)['actor_path']
    command = [sys.executable,str(root/'tools/checkpoint_probe.py'),
        '--output',str(out),'--profile',str(a.profile.resolve()),
        '--checkpoint',a.checkpoint,'--verify-checkpoint','--controller-only',
        '--gpu-plugin','spatial','--window','1920','1080',
        '--cache-root',str(a.cache_root.resolve()),
        '--renderdoc',str((a.renderdoc/'renderdoccmd.exe').resolve()),
        '--post-effect',a.post_effect]
    if a.fullscreen:command+=['--fullscreen']
    command+=['--extra','--aot_projection_precision=true','--aot_renderdoc_capture=true',
        '--aot_projection_source_snapshots='+str(a.source_snapshots).lower(),
        '--aot_shader_survey_path='+str(out/'shader-use.csv'),
        '--aot_draw_capture_path='+str(out/'draws.csv'),
        '--aot_draw_capture_trigger_file='+str(out/'capture.trigger')]
    started = time.monotonic()
    report = {'command':command,'checkpoint_actor':identity,'complete':False}
    child = subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW)
    collector = log = video = None
    pad = out/'controller.state'
    def hold(seconds, rx=0, ry=0, lt=0, rt=0, lx=0, buttons='0000'):
        pad.write_text(f'{buttons} {lt} {rt} {lx} 0 {rx} {ry}\n')
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            if child.poll() is not None: raise RuntimeError('Session exited during input')
            os.utime(pad,None); time.sleep(.05)
        pad.write_text('0000 0 0 0 0 0 0\n')
    def pause_state(label):
        offset = runtime.stat().st_size
        with (out/'game.commands').open('a') as f:
            f.write('getall WorldInfo Pauser\n')
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            if child.poll() is not None: raise RuntimeError('Session exited during pause query')
            with runtime.open('rb') as f:
                f.seek(offset); response=f.read().decode(errors='replace')
            matches=re.findall(r'\.Pauser = ([^\r\n]+)',response)
            if matches:
                report.setdefault('pause_queries',[]).append({'label':label,'responses':matches})
                return all(m.strip()=='None' for m in matches)
            time.sleep(.1)
        raise RuntimeError('Pause query did not return')
    try:
        while time.monotonic()-started < 135:
            if child.poll() is not None: raise RuntimeError('Checkpoint session exited before capture')
            runtime = out/'runtime.log'
            if runtime.is_file() and "CurrCheckpoint = AO2Checkpoint'"+identity+"'" in runtime.read_text(errors='replace'):
                break
            time.sleep(.2)
        else: raise RuntimeError('Requested checkpoint not verified')
        report['verified_seconds'] = time.monotonic()-started
        pid=json.loads((out/'running.json').read_text())['pid']
        report['pid']=pid
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),
            str(pid),'--focus','--seconds','.1'],check=True,timeout=10)
        if not pause_state('before capture setup'):
            # Resume only an observed paused session, then verify the effect.
            pad.write_text('0010 0 0 0 0 0 0\n')
            time.sleep(.2)
            pad.write_text('0000 0 0 0 0 0 0\n')
            time.sleep(1)
            if not pause_state('after Start'): raise RuntimeError('Resume did not clear pause')
        log = (out/'collector.log').open('x')
        env = dict(os.environ,AOT_RENDERDOC_ROOT=str(root),AOT_RENDERDOC_PROBE=str(out),
                   AOT_RENDERDOC_PASSIVE='1',AOT_RENDERDOC_FRAMES='1')
        collector = subprocess.Popen([str((a.renderdoc/'qrenderdoc.exe').resolve()),
            '--python',str(root/'tools/renderdoc_capture.py')],env=env,
            stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        # Give the collector time to connect, and retain a short settled frame.
        settle = time.monotonic()+5
        while time.monotonic()<settle:
            if child.poll() is not None or collector.poll() is not None:
                raise RuntimeError('Owned process exited before trigger')
            time.sleep(.1)
        # Collector startup must not leave the game unfocused or paused.
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),
            str(pid),'--focus','--seconds','.1'],check=True,timeout=10)
        if not pause_state('at trigger'): raise RuntimeError('Game paused before capture')
        if a.terrain_impacts:
            hold(3,rx=24000);hold(2);hold(.7,ry=-24000);hold(1,lt=255)
            report['fire_seconds'] = time.monotonic()-started
            hold(2,lt=255,rt=255);hold(2,lt=255)
            if a.explosive_impacts:
                # Ask the partner to hold position, then move off his launch
                # line. A thrown grenade can otherwise bounce off him offscreen.
                hold(.2,buttons='0008');hold(.5);hold(2,lx=24000);hold(1)
                # The wheel is held, not toggled. Keep LB pressed while Down
                # selects its grenade sector; Down alone orders the partner.
                hold(.5,buttons='0100');hold(.2,buttons='0102')
                hold(.4,buttons='0100');hold(2)
                windows=game_windows(pid)
                if len(windows)!=1 or not capture(windows[0][0],windows[0][2],windows[0][3],out/'grenade-selection.png'):
                    raise RuntimeError('Missing grenade selection observation')
                if a.explosion_video:
                    video = subprocess.Popen([sys.executable,str(root/'tools/window_video_probe.py'),
                        '--pid',str(pid),'--output',str(out/'explosion-motion'),'--seconds',
                        '16' if a.post_impact_sweep else '12',
                        '--ffmpeg',str(a.explosion_video.resolve())],creationflags=subprocess.CREATE_NO_WINDOW)
                    deadline=time.monotonic()+10
                    samples=out/'explosion-motion'/'frames.jsonl'
                    while not samples.exists() or not samples.stat().st_size:
                        if video.poll() is not None or time.monotonic()>deadline:
                            raise RuntimeError('Throw recorder did not begin')
                        hold(.1)
                hold(1,lt=255)
                report['throw_seconds'] = time.monotonic()-started
                hold(.2,lt=255,rt=255);hold(7)
            if not pause_state('after impact setup'): raise RuntimeError('Game paused after impact setup')
        windows=game_windows(pid)
        if len(windows)!=1 or not capture(windows[0][0],windows[0][2],windows[0][3],out/'before-gpu-trigger.png'):
            raise RuntimeError('Missing owned game window')
        with (out/'capture.trigger').open('x') as trigger: trigger.write(str(time.time()))
        report['trigger_seconds'] = time.monotonic()-started
        if a.post_impact_sweep:
            hold(1);hold(2,rx=16000);hold(2,rx=-16000)
        child.wait(timeout=150)
        collector.wait(timeout=35)
        if video is not None and video.wait(timeout=30)!=0:
            raise RuntimeError('Throw recording failed')
        done=json.loads((out/'probe.json').read_text())
        travel=json.loads((out/'travel.json').read_text())
        gpu=json.loads((out/'renderdoc-capture.json').read_text())
        if (child.returncode or collector.returncode or done['timed_out'] or
                done['exit_code_before_cleanup']!=0 or gpu.get('error') or len(gpu['captures'])!=1):
            raise RuntimeError('Native run or capture did not complete')
        if not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']:
            raise RuntimeError('Source save/checkpoint changed')
        report['complete']=True
    finally:
        if pad.exists(): pad.write_text('0000 0 0 0 0 0 0\n')
        if child.poll() is None: child.wait(timeout=300)
        if collector is not None:
            try: collector.wait(timeout=35)
            except subprocess.TimeoutExpired:
                collector.kill(); collector.wait(); report['collector_timeout']=True
        if video is not None and video.poll() is None:
            video.wait(timeout=30)
        if log is not None: log.close()
        report['elapsed_seconds']=time.monotonic()-started
        if out.exists(): (out/'checkpoint-gpu.json').write_text(json.dumps(report,indent=2)+'\n')
    print(out)


if __name__=='__main__': main()
