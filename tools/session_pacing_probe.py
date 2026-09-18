"""Compare verified quiet scenes before/after repeated level travel in one session.

Copies the profile, retains rendering corrections, records GPU telemetry and
process resources, then closes its owned game normally. No input or captures
occur inside the 80-second measured windows. This is not a combat benchmark.
"""
import argparse
from ctypes import windll,wintypes
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
from probe import capture,game_windows
from quiet_pacing_probe import sample_quiet_window
from gpu_telemetry import GpuTelemetry


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--cache-root',type=Path,required=True)
    parser.add_argument('--checkpoint',action='append',required=True)
    parser.add_argument('--hud-text-contrast',action=argparse.BooleanOptionalAction,default=None,
                        help='Override the copied profile only; otherwise retain its saved preference')
    parser.add_argument('--gpu-timing',action='store_true',
                        help='Also retain GPU packet-wait and VBlank traces')
    parser.add_argument('--swap-timing',action='store_true',
                        help='Attribute CPU wall time inside image refresh and presenter handoff')
    parser.add_argument('--immediate-guest-present',action=argparse.BooleanOptionalAction,default=None,
                        help='Opt-in guest interval experiment; host VSync and frame cap stay enabled')
    parser.add_argument('--batch-occlusion',action=argparse.BooleanOptionalAction,default=None,
                        help='Compare real visibility-query batching across level travel')
    parser.add_argument('--post-effect',choices=('fxaa','fxaa_extreme','none'),default='fxaa_extreme')
    parser.add_argument('--trace-readback-retirement',action='store_true',
                        help='Bounded eviction/fence observations; may perturb performance')
    a=parser.parse_args();root=Path(__file__).resolve().parents[1]
    out=a.output.resolve();source=a.profile.resolve();profile=out.with_name(out.name+'-profile')
    directory=root/'assets/AO2Game/Checkpoints'
    if out.exists() or profile.exists() or not source.is_dir() or not 1<=len(a.checkpoint)<=12:
        parser.error('Require new output/profile paths, existing source and 1-12 checkpoints')
    if source in profile.parents or profile in source.parents:parser.error('Require separate profiles')
    if shutil.disk_usage(out.parent).free < 1024**3:
        parser.error('Require at least 1 GiB free for session traces')
    if any(not re.fullmatch(r'[A-Za-z0-9_]+',n) or not (directory/n).is_file() for n in a.checkpoint):
        parser.error('Require existing retail checkpoint names')
    metadata={n:checkpoint_metadata(directory/n) for n in a.checkpoint}
    def hashes(folder):
        return {str(f.relative_to(folder)):hashlib.sha256(f.read_bytes()).hexdigest()
                for f in folder.rglob('*') if f.is_file()}
    before=hashes(source);shutil.copytree(source,profile)
    env={k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    env.update(AOT_INPUT_SCRIPT=str(root/'config/input-graphics.script'),
        AOT_INPUT_STATE=str(out/'controller.state'),AOT_GAME_COMMANDS=str(out/'game.commands'),
        AOT_FRAME_LOG=str(out/'frame-times.csv'),AOT_FRAME_PHASE_LOG=str(out/'phases.csv'),
        AOT_WAIT_LOG=str(out/'waits.csv'),AOT_RENDER_WAIT_LOG=str(out/'render-waits.csv'))
    if a.trace_readback_retirement:env['AOT_TRACE_READBACK_RETIREMENT']='1'
    if a.gpu_timing:
        env.update(AOT_GPU_WAIT_LOG=str(out/'gpu-waits.csv'),AOT_GPU_VBLANK_LOG=str(out/'gpu-vblank.csv'))
    if a.swap_timing:env['AOT_SWAP_LOG']=str(out/'swap-timing.csv')
    deadline=120+len(a.checkpoint)*150
    command=[sys.executable,str(root/'tools/probe.py'),'--output',str(out),
        '--user-data',str(profile),'--cache-root',str(a.cache_root.resolve()),
        '--gpu-plugin','spatial','--seconds',str(deadline),'--capture-interval','0',
        '--log-level','info','--use-saved-settings','--',
        '--fullscreen=true','--window_width=1920','--window_height=1080',
        '--aot_keyboard_mouse=false','--input_backend=xinput','--vsync=true','--aot_fps=60',
        '--resolution_scale=1','--aot_spatial_upscale=true','--swap_post_effect='+a.post_effect,
        '--aot_gpu_vblank_wake=true','--aot_projection_precision=true',
        '--aot_automatic_projection_precision=true','--aot_projection_source_snapshots=true',
        '--readback_resolve=fast','--anisotropic_override=5',
        '--readback_resolve_half_pixel_offset=true']
    if a.hud_text_contrast is not None:
        command+=['--aot_hud_text_contrast='+str(a.hud_text_contrast).lower()]
    if a.immediate_guest_present is not None:
        command+=['--aot_immediate_guest_present='+str(a.immediate_guest_present).lower()]
    if a.batch_occlusion is not None:
        command+=['--aot_batch_occlusion_queries='+str(a.batch_occlusion).lower()]
    report={'command':command,'checkpoint_metadata':metadata,'source_profile_before':before,'visits':[],'complete':False}
    child=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    started=time.monotonic();pid=None;telemetry=None
    runtime=out/'runtime.log'
    def alive():
        if child.poll() is not None:raise RuntimeError('Owned native session ended early')
        if telemetry is not None:telemetry.check()
    def hold(seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:alive();time.sleep(.2)
    def send(line):
        with (out/'game.commands').open('a') as f:f.write(line+'\n')
    def query(line,pattern,missing_ok=False):
        offset=runtime.stat().st_size;send(line);end=time.monotonic()+5
        while time.monotonic()<end:
            alive()
            with runtime.open('rb') as f:f.seek(offset);tail=f.read().decode(errors='replace')
            values=re.findall(pattern,tail)
            if values:return values
            time.sleep(.1)
        if missing_ok:return []
        raise RuntimeError('No response to '+line)
    def snapshot(name):
        windows=game_windows(pid)
        if len(windows)!=1 or not capture(windows[0][0],windows[0][2],windows[0][3],out/name):
            raise RuntimeError('Owned window capture failed')
    def close_window():
        if pid is not None:
            for hwnd,_,_,_ in game_windows(pid):windll.user32.PostMessageW(wintypes.HWND(hwnd),0x0010,0,0)
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
        hold(75);pid=json.loads((out/'running.json').read_text())['pid'];report['pid']=pid
        (out/'controller.state').write_text('0000 0 0 0 0 0 0\n')
        telemetry=GpuTelemetry(out).__enter__()
        for index,name in enumerate(a.checkpoint):
            prefix='visit-%02d'%index
            visit={'checkpoint':name,'travel_seconds':time.monotonic()-started};report['visits'].append(visit)
            send('open Checkpoint?LoadSaveGame?CheckpointToLoad='+name+'?Difficulty=1')
            hold(12);end=time.monotonic()+60
            while True:
                refs=query('getall AO2CheckpointManager CurrCheckpoint',r"CurrCheckpoint = AO2Checkpoint'([^']+)'",missing_ok=True)
                if set(refs)=={metadata[name]['actor_path']}:break
                if time.monotonic()>end:raise RuntimeError('Checkpoint did not match: '+repr(refs))
                hold(2)
            visit['checkpoint_reference']=refs
            hold(12);snapshot(prefix+'-before.png')
            visit['menus_before']=menu_tags(prefix+'-before')
            if visit['menus_before']:raise RuntimeError('Quiet scene has an active menu')
            hold(5)
            visit['rotation_before']=query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
            (out/'session-progress.json').write_text(json.dumps(report,indent=2)+'\n')
            sample_quiet_window(out,pid,alive,prefix+'-window.json')
            visit['rotation_after']=query('getall AO2PlayerController Rotation',r'Rotation = (\([^\r\n]+)')
            visit['pause_after']=query('getall WorldInfo Pauser',r'\.Pauser = ([^\r\n]+)')
            if any(p.strip()!='None' for p in visit['pause_after']):raise RuntimeError('Scene became paused')
            visit['menus_after']=menu_tags(prefix+'-after')
            if visit['menus_after']:raise RuntimeError('Quiet scene acquired an active menu')
            if visit['rotation_before']!=visit['rotation_after']:raise RuntimeError('Camera moved during quiet scene')
            snapshot(prefix+'-after.png')
            visit['finished_seconds']=time.monotonic()-started
            (out/'session-progress.json').write_text(json.dumps(report,indent=2)+'\n')
        close_window();child.wait(timeout=45)
        native=json.loads((out/'probe.json').read_text())
        if child.returncode or native['timed_out'] or native['exit_code_before_cleanup']!=0:
            raise RuntimeError('Session did not close normally')
        report['complete']=True
    except BaseException:
        report['error']=traceback.format_exc();raise
    finally:
        if child.poll() is None:close_window()
        child.wait(timeout=deadline+45)
        if telemetry is not None:
            telemetry.__exit__(*sys.exc_info())
        try:
            report['source_profile_unchanged']=before==hashes(source)
            report['retail_checkpoints_unchanged']=all(hashlib.sha256((directory/n).read_bytes()).hexdigest()==m['sha256'] for n,m in metadata.items())
        except Exception:
            report['complete']=False
            report['verification_error']=traceback.format_exc()
            raise
        finally:
            report['elapsed_seconds']=time.monotonic()-started
            if out.exists():(out/'session-pacing.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['source_profile_unchanged'] or not report['retail_checkpoints_unchanged']:
        raise RuntimeError('Source data changed')
    print(out)


if __name__=='__main__':main()
