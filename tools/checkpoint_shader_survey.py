"""Survey actual shader use across verified checkpoints in one copied-profile run.

First-use CSV observations identify capture candidates, not visible correctness.
No RenderDoc, GPU replay, purchases or writes to the source profile are involved.
"""
import argparse
import csv
from ctypes import wintypes, windll
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from inspect_checkpoint import checkpoint_metadata
from probe import capture, game_windows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--profile',type=Path,required=True)
    p.add_argument('--cache-root',type=Path,required=True)
    p.add_argument('--checkpoint',action='append',required=True)
    p.add_argument('--stop-on',action='append',default=[],help='Stop after a scene draws this guest VS hash')
    p.add_argument('--motion-video',type=Path,help='FFmpeg executable: record an eight-second camera sweep per checkpoint')
    p.add_argument('--post-effect',choices=('none','fxaa','fxaa_extreme'),default='fxaa')
    p.add_argument('--terrain-impacts',action='store_true',
                   help='After each look, aim down and fire a bounded burst; inspect images to establish terrain impacts')
    a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=a.output.resolve(); profile=out.with_name(out.name+'-profile')
    source=a.profile.resolve(); directory=root/'assets/AO2Game/Checkpoints'
    if out.exists() or profile.exists() or not source.is_dir() or not 1<=len(a.checkpoint)<=12:
        p.error('Require new output/profile, existing source and 1-12 checkpoints')
    if source in profile.parents or profile in source.parents: p.error('Require separate profile paths')
    if a.motion_video:
        if not a.motion_video.is_file():p.error('Require an existing FFmpeg executable')
        parent=next(x for x in out.parents if x.exists())
        if shutil.disk_usage(parent).free<max(4,2+len(a.checkpoint)/4)*1024**3:
            p.error('Insufficient space for bounded motion recordings')
    if any(not re.fullmatch(r'[A-Za-z0-9_]+',n) or not (directory/n).is_file() for n in a.checkpoint):
        p.error('Require existing local retail checkpoint names')
    if any(not re.fullmatch(r'[0-9A-Fa-f]{16}',n) for n in a.stop_on): p.error('Require 16-digit VS hashes')
    targets={n.lower() for n in a.stop_on}
    metadata={n:checkpoint_metadata(directory/n) for n in a.checkpoint}
    def hashes(folder):
        return {str(f.relative_to(folder)):hashlib.sha256(f.read_bytes()).hexdigest()
                for f in folder.rglob('*') if f.is_file()}
    before=hashes(source); shutil.copytree(source,profile)
    env={k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_INPUT_SCRIPT=str(root/'config/input-graphics.script'),
               AOT_INPUT_STATE=str(out/'controller.state'),AOT_GAME_COMMANDS=str(out/'game.commands'))
    command=[sys.executable,str(root/'tools/probe.py'),'--output',str(out),
        '--user-data',str(profile),'--cache-root',str(a.cache_root.resolve()),
        '--gpu-plugin','spatial','--seconds',str(90+len(a.checkpoint)*100),
        '--capture-interval','0','--log-level','info','--use-saved-settings','--',
        '--fullscreen=false','--window_width=1920','--window_height=1080',
        '--aot_keyboard_mouse=false','--input_backend=xinput','--vsync=true','--aot_fps=60',
        '--resolution_scale=1','--readback_resolve=fast','--swap_post_effect='+a.post_effect,
        '--aot_shader_survey_path='+str(out/'shader-use.csv')]
    report={'command':command,'scenes':[],'complete':False}
    started=time.monotonic(); child=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    pid=None; video=None; pad=out/'controller.state'; runtime=out/'runtime.log'
    def alive():
        if child.poll() is not None: raise RuntimeError('Owned survey process exited')
    def hold(seconds,buttons='0000',rx=0,ry=0,lt=0,rt=0):
        end=time.monotonic()+seconds
        pad.write_text(f'{buttons} {lt} {rt} 0 0 {rx} {ry}\n')
        while time.monotonic()<end:
            alive(); os.utime(pad,None); time.sleep(.05)
        pad.write_text('0000 0 0 0 0 0 0\n')
    def send(line):
        with (out/'game.commands').open('a') as f:f.write(line+'\n')
    def query(line,pattern,timeout=8,missing_ok=False):
        offset=runtime.stat().st_size; send(line); deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            alive()
            with runtime.open('rb') as f:f.seek(offset); tail=f.read().decode(errors='replace')
            matches=re.findall(pattern,tail)
            if matches:return matches
            time.sleep(.1)
        if missing_ok:return []
        raise RuntimeError('Query did not return: '+line)
    def focus():
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),str(pid),
            '--focus','--seconds','.1'],check=True,timeout=10,stdout=subprocess.DEVNULL)
    def snapshot(name):
        w=game_windows(pid)
        if len(w)!=1 or not capture(w[0][0],w[0][2],w[0][3],out/name): raise RuntimeError('Capture failed')
    def menu_tags(name):
        path=out/(name+'-scenes.json')
        subprocess.run([sys.executable,str(root/'tools/inspect_hud_objects.py'),'--probe',str(out),
            '--menu','--output',str(path)],check=True,timeout=25,stdout=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        data=json.loads(path.read_text())
        clients=[x for x in data['objects'] if x['class']=='UTGameUISceneClient' and not x['name'].startswith('Default__')]
        if len(clients)!=1 or not clients[0].get('active_scene_list_unchanged'):
            raise RuntimeError('Unstable active-menu observation')
        return [x['scene_tag'] for x in clients[0]['active_scenes']]
    try:
        while time.monotonic()-started<75:
            alive();time.sleep(.1)
        pid=json.loads((out/'running.json').read_text())['pid'];report['pid']=pid
        rows=list(csv.DictReader((out/'shader-use.csv').open()))
        report['startup_pairs']=rows
        report['startup_target_matches']=[r for r in rows if r['vs_hash'].zfill(16) in targets]
        seen={(r['vs_hash'],r['ps_hash']) for r in rows}
        for n in a.checkpoint:
            scene={'checkpoint':n,'travel_seconds':time.monotonic()-started};report['scenes'].append(scene)
            send('open Checkpoint?LoadSaveGame?CheckpointToLoad='+n+'?Difficulty=1')
            hold(12); focus()
            deadline=time.monotonic()+60
            while True:
                refs=query('getall AO2CheckpointManager CurrCheckpoint',r"CurrCheckpoint = AO2Checkpoint'([^']+)'",timeout=5,missing_ok=True)
                if set(refs)=={metadata[n]['actor_path']}:break
                if time.monotonic()>deadline:raise RuntimeError('Wrong checkpoint: '+repr(refs))
                hold(2)
            scene['checkpoint_reference']=refs
            if a.motion_video:
                hold(5)
                tags=menu_tags(n+'-before');scene['menus_before']=tags
                # Decline only the identified retail mid-mission shopping
                # confirmation; never infer a gameplay scene from Pauser alone.
                if 'AO2Confirmation' in tags and 'MidMissionSetDataStore' in tags:
                    snapshot(n+'-shopping-prompt.png');hold(.2,'2000');hold(3)
                    scene['shopping_declined']=True
                    tags=menu_tags(n+'-after-shopping');scene['menus_after_shopping']=tags
                if 'AO2Confirmation' in tags:raise RuntimeError('Unresolved confirmation before motion')
            pause=query('getall WorldInfo Pauser',r'\.Pauser = ([^\r\n]+)')
            if any(m.strip()!='None' for m in pause):
                hold(.2,'0010');hold(1)
                pause=query('getall WorldInfo Pauser',r'\.Pauser = ([^\r\n]+)')
            if any(m.strip()!='None' for m in pause):raise RuntimeError('Still paused')
            scene['pause']=pause;hold(2);snapshot(n+'-before.png')
            scene['rotation_before']=query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
            if a.motion_video:
                video_path=out/(n+'-motion')
                video=subprocess.Popen([sys.executable,str(root/'tools/window_video_probe.py'),
                    '--pid',str(pid),'--output',str(video_path),'--seconds','8',
                    '--ffmpeg',str(a.motion_video.resolve())],creationflags=subprocess.CREATE_NO_WINDOW)
                deadline=time.monotonic()+10;samples=video_path/'frames.jsonl'
                while not samples.exists() or not samples.stat().st_size:
                    if video.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('Motion recording did not begin')
                    hold(.1)
                hold(1)
            hold(3,rx=24000);hold(2);snapshot(n+'-turned.png')
            scene['rotation_after']=query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
            if video is not None:
                if video.wait(timeout=15)!=0:raise RuntimeError('Motion recording failed')
                scene['motion_video']=str(video_path)
                video=None
                scene['menus_after']=menu_tags(n+'-after')
                scene['pause_after']=query('getall WorldInfo Pauser',r'\.Pauser = ([^\r\n]+)')
                if ('AO2Confirmation' in scene['menus_after'] or scene['rotation_before']==scene['rotation_after'] or
                    any(x.strip()!='None' for x in scene['pause_after'])):
                    raise RuntimeError('Motion view was paused, unmoved or interrupted by a confirmation')
            if a.terrain_impacts:
                scene['before_impact_pairs']=list(csv.DictReader((out/'shader-use.csv').open()))
                hold(.7,ry=-24000);hold(1,lt=255)
                snapshot(n+'-impact-aim.png')
                scene['impact_rotation']=query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
                scene['fire_seconds']=time.monotonic()-started
                hold(2,lt=255,rt=255);hold(2,lt=255)
                snapshot(n+'-impact-after.png')
            rows=list(csv.DictReader((out/'shader-use.csv').open()))
            pairs={(r['vs_hash'],r['ps_hash']) for r in rows}
            scene['new_pairs']=[r for r in rows if (r['vs_hash'],r['ps_hash']) not in seen]
            seen=pairs
            scene['target_matches']=[r for r in scene['new_pairs'] if r['vs_hash'].zfill(16) in targets]
            scene['observed_seconds']=time.monotonic()-started
            (out/'survey-progress.json').write_text(json.dumps(report,indent=2)+'\n')
            if scene['target_matches']:break
        w=game_windows(pid)
        if len(w)!=1:raise RuntimeError('Missing owned window on close')
        windll.user32.PostMessageW(wintypes.HWND(w[0][0]),0x0010,0,0)
        child.wait(timeout=30)
        done=json.loads((out/'probe.json').read_text())
        if child.returncode or done['timed_out'] or done['exit_code_before_cleanup']!=0:
            raise RuntimeError('Native survey did not close normally')
        report['complete']=True
    except BaseException:
        report['error']=traceback.format_exc()
        raise
    finally:
        if pad.exists():pad.write_text('0000 0 0 0 0 0 0\n')
        if child.poll() is None and pid is not None:
            w=game_windows(pid)
            if len(w)==1:windll.user32.PostMessageW(wintypes.HWND(w[0][0]),0x0010,0,0)
        child.wait(timeout=90+len(a.checkpoint)*100)
        if video is not None:video.wait(timeout=20)
        report['source_profile_unchanged']=before==hashes(source)
        report['retail_checkpoints_unchanged']=all(hashlib.sha256((directory/n).read_bytes()).hexdigest()==m['sha256'] for n,m in metadata.items())
        report['elapsed_seconds']=time.monotonic()-started
        if out.exists():(out/'shader-survey.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['source_profile_unchanged'] or not report['retail_checkpoints_unchanged']:
        raise RuntimeError('Source files changed')
    print(out)


if __name__=='__main__':main()
