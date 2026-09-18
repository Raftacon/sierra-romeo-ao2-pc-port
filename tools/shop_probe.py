"""Repeat shop previews and upgrade transitions from a verified copied checkpoint."""
import argparse
from ctypes import windll,wintypes
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from probe import capture,game_windows

def validate_scene_report(path,label):
    expected={
        'shopping-prompt':'AO2Confirmation',
        'shop-main':'ContactsScene','shop-return-main':'ContactsScene',
        'shop-primary':'WeaponSelectUIScene','shop-secondary':'WeaponSelectUIScene',
        'shop-purchased':'PurchaseOkayUIScene',
        'shop-upgrades':'WeaponUpgradesScene','shop-barrel':'WeaponUpgradesScene',
        'shop-barrel-next':'WeaponUpgradesScene','shop-gear':'InventoryArmorScene',
        'shop-armor':'ArmorUpgradeScene','shop-mask':'MaskUpgradeScene'}[label]
    report=json.loads(path.read_text())
    clients=[o for o in report['objects'] if o['class']=='UTGameUISceneClient'
             and not o['name'].startswith('Default__')]
    if len(clients)!=1 or not clients[0].get('active_scene_list_unchanged'):
        raise RuntimeError('Missing or unstable active scene observation: '+label)
    tags=[s['scene_tag'] for s in clients[0]['active_scenes']]
    if expected not in tags:
        raise RuntimeError('Unexpected menu for '+label+': '+repr(tags))
    return tags

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--profile',type=Path,required=True)
    p.add_argument('--cache-root',type=Path,required=True);p.add_argument('--ffmpeg',required=True)
    p.add_argument('--readback',choices=('fast','full'),default='fast')
    p.add_argument('--post-effect',choices=('none','fxaa','fxaa_extreme'),default='fxaa')
    p.add_argument('--completed-readback',action=argparse.BooleanOptionalAction,default=None,
                   help='Override the completed-snapshot readback experiment')
    p.add_argument('--target-path',choices=('rtv','rov'))
    p.add_argument('--shop-settle',type=int,default=8,help='Initial shop loading wait, 8-45 seconds')
    p.add_argument('--inspect-menus',action=argparse.BooleanOptionalAction,default=True,
                   help='Check live active scene names beside menu screenshots (default enabled)')
    p.add_argument('--automatic-projection',action='store_true')
    p.add_argument('--projection-fma',action=argparse.BooleanOptionalAction,default=None)
    p.add_argument('--frame-color-probe',action='store_true',
                   help='Inspect each FSR output for wide magenta bands; diagnostic overhead, not a benchmark')
    p.add_argument('--exercise-transitions',action='store_true',
                   help='Record extra bounded gear and weapon selection cycles, including complete transitions')
    p.add_argument('--exercise-cartridge',action='store_true',
                   help='Record drilling into cartridge capacity choices and browsing without buying')
    p.add_argument('--exercise-ak47',action='store_true',
                   help='Select and purchase AK-47 inside the copied test profile, then exercise all four capacity choices without buying upgrades')
    p.add_argument('--exercise-s-system',action='store_true',
                   help='Select and purchase S-System inside the copied test profile, then exercise all three capacity choices without buying upgrades')
    p.add_argument('--record-video',action=argparse.BooleanOptionalAction,default=True,
                   help='Keep desktop recordings; disabling requires continuous frame-color coverage and forbids a GPU capture')
    p.add_argument('--test-cash',type=int,default=0,
                   help='Call retail GiveCashMoney in the isolated test session before shopping (0-1000000); never modifies the source profile')
    p.add_argument('--preserve-scene',action=argparse.BooleanOptionalAction,default=None,
                   help='Override scene preservation; omitted uses the runtime default')
    p.add_argument('--trace-engine-resolves',action='store_true',help='Bounded read-only engine resolve trace around the GPU capture trigger')
    p.add_argument('--compare-shadows',action='store_true',help='Temporarily toggle dynamic shadows at the primary preview, then verify restoration')
    p.add_argument('--renderdoc',type=Path)
    p.add_argument('--capture-at',choices=('primary','gear','armor','mask'),default='primary',
                   help='Verified shop screen for the optional GPU capture')
    p.add_argument('--window',type=int,nargs=2,default=(1920,1080),metavar=('WIDTH','HEIGHT'))
    p.add_argument('--video-mode',type=int,nargs=2,metavar=('WIDTH','HEIGHT'),help='Game-facing mode comparison; runtime default values still fall back to window dimensions')
    a=p.parse_args();root=Path(__file__).resolve().parents[1];out=a.output.resolve()
    if a.exercise_ak47 and a.exercise_s_system:p.error('Select one purchased weapon route')
    if not 0<=a.test_cash<=1000000:p.error('Test cash must be between 0 and 1000000')
    if a.exercise_ak47 or a.exercise_s_system:a.exercise_cartridge=True
    if not a.record_video and (not a.frame_color_probe or a.renderdoc):
        p.error('Without desktop recordings, require --frame-color-probe and no RenderDoc capture')
    if out.exists():p.error('Require new output directory')
    existing_parent=next(parent for parent in out.parents if parent.exists())
    # Continuous color monitoring retains at most 32 full-size candidate PPMs,
    # plus CSV/JSON and menu PNGs. The 2 GiB floor is only for that bounded mode;
    # lossless video and GPU captures retain the larger original reservation.
    required_gib=8 if a.record_video else max(2,
        ((32*3+128*4)*a.window[0]*a.window[1]+512*1024**2+1024**3-1)//1024**3)
    if shutil.disk_usage(existing_parent).free < required_gib * 1024**3:
        p.error(f'Shop diagnostics require at least {required_gib} GiB free on the output drive')
    if a.trace_engine_resolves and not a.renderdoc:p.error('Engine resolve trace requires the RenderDoc capture trigger')
    if a.compare_shadows and a.capture_at!='primary':p.error('Shadow comparison requires the primary preview capture')
    if a.capture_at!='primary' and not a.renderdoc:p.error('A different GPU capture screen requires RenderDoc')
    if not 8<=a.shop_settle<=45:p.error('Shop settle must be 8-45 seconds')
    command=[sys.executable,str(root/'tools/checkpoint_probe.py'),'--output',str(out),
        '--profile',str(a.profile.resolve()),'--checkpoint','08_05','--verify-checkpoint',
        '--gpu-plugin','spatial','--window',str(a.window[0]),str(a.window[1]),'--controller-only','--observe-seconds',
        '360' if a.exercise_transitions or a.exercise_cartridge else '240' if a.inspect_menus or a.shop_settle>8 else '180',
        '--cache-root',str(a.cache_root.resolve()),'--readback',a.readback,'--post-effect',a.post_effect]
    command+=['--finish-trigger',str(out/'scenario.complete')]
    if a.renderdoc:command+=['--renderdoc',str((a.renderdoc/'renderdoccmd.exe').resolve())]
    command+=['--extra','--aot_wall_projection_precision=true',
              '--aot_automatic_projection_precision='+str(a.automatic_projection).lower()]
    if a.completed_readback is not None:
        command+=['--aot_completed_resolve_readback='+str(a.completed_readback).lower(),
                  '--aot_trace_completed_readback=true']
    if a.target_path:command+=['--render_target_path_d3d12='+a.target_path]
    if a.projection_fma is not None:command+=['--aot_projection_fma='+str(a.projection_fma).lower()]
    if a.frame_color_probe:command+=['--aot_spatial_upscale=true']
    if a.preserve_scene is not None:command+=['--aot_preserve_scene_before_shadows='+str(a.preserve_scene).lower()]
    if a.video_mode:
        if not 640<=a.video_mode[0]<=4095 or not 480<=a.video_mode[1]<=4095:p.error('Video mode outside runtime bounds')
        command+=['--video_mode_width='+str(a.video_mode[0]),'--video_mode_height='+str(a.video_mode[1])]
    if a.renderdoc:command+=['--aot_renderdoc_capture=true','--aot_draw_capture_path='+str(out/'draws.csv'),
        '--aot_draw_capture_trigger_file='+str(out/'capture.trigger')]
    env=os.environ.copy()
    env.pop('AOT_FRAME_COLOR_PROBE',None)
    if a.frame_color_probe:env['AOT_FRAME_COLOR_PROBE']=str(out/'frame-colors')
    if a.trace_engine_resolves:
        env['AOT_RHI_RESOLVE_LOG']=str(out/'engine-resolves.csv')
        env['AOT_RHI_RESOLVE_TRIGGER']=str(out/'capture.trigger')
    steady_origin_ms=time.perf_counter()*1000
    started=time.monotonic();child=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    report={'command':command,'steady_clock_origin_ms':steady_origin_ms,'frame_color_probe':a.frame_color_probe,'desktop_recordings':a.record_video,'weapon_route':'s-system' if a.exercise_s_system else 'ak47' if a.exercise_ak47 else 'hk36','engine_trace_environment':{k:env[k] for k in ('AOT_RHI_RESOLVE_LOG','AOT_RHI_RESOLVE_TRIGGER') if k in env},'events':[],'complete':False};helpers=[];logs=[]
    def event(**kw):report['events'].append(dict(seconds=time.monotonic()-started,**kw))
    def alive():
        if child.poll() is not None:raise RuntimeError('Owned checkpoint probe exited')
    def hold(seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:alive();time.sleep(.05)
    def press(button,delay=3):
        # Keep directional taps below the menu's repeat interval. At the faster
        # UI cadence, the old 180 ms pulse can advance more than one row.
        pulse=.07 if button in ('0001','0002','0004','0008') else .18
        event(button=button,settle_seconds=delay,pulse_seconds=pulse)
        pad.write_text(button+' 0 0 0 0 0 0\n');hold(pulse)
        pad.write_text('0000 0 0 0 0 0 0\n');hold(delay)
    def snapshot(label,scene_label=None):
        w=game_windows(pid)
        if len(w)!=1 or not capture(w[0][0],w[0][2],w[0][3],out/(label+'.png')):raise RuntimeError('Window capture failed')
        event(image=label+'.png')
        if a.inspect_menus:
            for attempt in range(3):
                scene_path=out/(label+('-retry'+str(attempt) if attempt else '')+'-scenes.json')
                observed=subprocess.run([sys.executable,str(root/'tools/inspect_hud_objects.py'),'--probe',str(out),
                    '--menu','--output',str(scene_path)],timeout=25,capture_output=True,text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if observed.returncode:
                    error=observed.stderr
                    retryable='Active scene is absent from observed object table' in error
                else:
                    try:
                        tags=validate_scene_report(scene_path,scene_label or label)
                        event(scene_tags=tags,image=label+'.png',scene_report=scene_path.name)
                        break
                    except RuntimeError as failure:
                        error=str(failure)
                        retryable=error.startswith('Missing or unstable active scene observation:')
                event(scene_observation_rejected=error,scene_report=scene_path.name)
                if not retryable or attempt==2:raise RuntimeError(error)
                hold(1.5)
    def shadow_state(label):
        path=out/(label+'-engine.json')
        subprocess.run([sys.executable,str(root/'tools/inspect_hud_objects.py'),'--probe',str(out),
            '--engine','--output',str(path)],check=True,timeout=25,stdout=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        raw=json.loads(path.read_text())
        engines=[o for o in raw['objects'] if o['class']=='GameEngine' and not o['name'].startswith('Default__')]
        if len(engines)!=1 or raw['failed_reads']:raise RuntimeError('Invalid live engine observation')
        words=raw['native_system_settings']['words'];enabled=int(words[0x16C//4],16)
        if enabled not in (0,1):raise RuntimeError('Invalid dynamic-shadow state')
        result={'engine':engines[0]['object'],'enabled':enabled,'words':words}
        event(shadow_state=result,label=label)
        return result
    def set_shadows(enabled):
        command='SCALE SET DYNAMICSHADOWS '+('TRUE' if enabled else 'FALSE')
        with (out/'game.commands').open('a') as commands:commands.write(command+'\n')
        event(command=command);hold(3)
    def trigger_capture():
        if not a.renderdoc:return
        alive()
        with (out/'capture.trigger').open('x') as f:f.write(str(time.time()))
        event(capture_trigger=True);hold(3)
    def helper(name,cmd,env=None):
        log=(out/(name+'.log')).open('x');logs.append(log)
        proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        helpers.append((name,proc));return proc
    def transition_recording(name):
        if not a.record_video:return None
        proc=helper(name,[sys.executable,str(root/'tools/window_video_probe.py'),'--pid',str(pid),
            '--output',str(out/name),'--seconds','30','--ffmpeg',a.ffmpeg])
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            alive()
            if proc.poll() is not None:raise RuntimeError('Recording exited before its first sample')
            stamps=out/name/'frames.jsonl'
            if stamps.exists() and stamps.stat().st_size:
                event(recording=name,first_sample_observed=True);return proc
            time.sleep(.05)
        raise RuntimeError('Recording did not produce its first sample')
    def finish_recording(proc,name):
        if proc is None:return
        if proc.wait(timeout=40)!=0:raise RuntimeError('Failed '+name)
        result=json.loads((out/name/'result.json').read_text())
        if result.get('exit_code')!=0 or result.get('timed_out'):raise RuntimeError('Incomplete '+name)
        event(recording=name,completed=True)
    try:
        expected="CurrCheckpoint = AO2Checkpoint'08_map_persistent.TheWorld.PersistentLevel.AO2Checkpoint_4'"
        while time.monotonic()-started<130:
            alive()
            if (out/'runtime.log').exists() and expected in (out/'runtime.log').read_text(errors='replace'):break
            time.sleep(.2)
        else:raise RuntimeError('Checkpoint did not verify')
        pid=json.loads((out/'running.json').read_text())['pid'];pad=out/'controller.state'
        subprocess.run([sys.executable,str(root/'tools/pc_input_probe.py'),str(pid),'--focus','--seconds','.1'],check=True,timeout=10)
        if a.renderdoc:helper('collector',[str((a.renderdoc/'qrenderdoc.exe').resolve()),'--python',str(root/'tools/renderdoc_capture.py')],
            dict(os.environ,AOT_RENDERDOC_ROOT=str(root),AOT_RENDERDOC_PROBE=str(out),AOT_RENDERDOC_PASSIVE='1'))
        snapshot('shopping-prompt')
        if a.test_cash:
            command='GiveCashMoney '+str(a.test_cash)
            with (out/'game.commands').open('a') as commands:commands.write(command+'\n')
            event(test_profile_command=command);hold(2)
        press('1000',a.shop_settle);snapshot('shop-main')
        video=transition_recording('shop-motion')
        press('1000',4);snapshot('shop-primary')
        if a.compare_shadows:
            initial=shadow_state('shop-primary-initial')
            try:
                set_shadows(not initial['enabled'])
                toggled=shadow_state('shop-primary-toggled')
                expected_words=list(initial['words']);expected_words[0x16C//4]=toggled['words'][0x16C//4]
                if toggled['enabled']!=1-initial['enabled'] or toggled['engine']!=initial['engine'] or toggled['words']!=expected_words:
                    raise RuntimeError('Shadow toggle changed unexpected settings')
                snapshot('shop-primary-shadows-toggled','shop-primary')
                trigger_capture()
            finally:
                set_shadows(initial['enabled'])
                if shadow_state('shop-primary-restored')!=initial:raise RuntimeError('Shadow settings failed to restore')
            snapshot('shop-primary-shadows-restored','shop-primary')
        elif a.capture_at=='primary':trigger_capture()
        if a.exercise_ak47 or a.exercise_s_system:
            weapon='s-system' if a.exercise_s_system else 'ak47'
            for _ in range(2 if a.exercise_s_system else 1):press('0002',2)
            snapshot('shop-'+weapon+'-selected','shop-primary')
            # Fresh source profile: A opens Buy/Customize, A buys the weapon.
            # Only the helper's isolated profile may be written by gameplay.
            press('1000',2)
            w=game_windows(pid)
            if len(w)!=1 or not capture(w[0][0],w[0][2],w[0][3],out/('shop-'+weapon+'-purchase.png')):
                raise RuntimeError(weapon+' purchase capture failed')
            event(image='shop-'+weapon+'-purchase.png')
            press('1000',3);snapshot('shop-'+weapon+'-owned','shop-purchased')
        press('1000');snapshot('shop-upgrades')
        press('1000');snapshot('shop-barrel')
        press('0002',2);snapshot('shop-barrel-next')
        if a.exercise_cartridge:
            finish_recording(video,'shop-motion')
            extra_video=transition_recording('cartridge-selection-motion')
            press('2000',1)
            press('0002',.5);press('0002',.5)
            snapshot('shop-cartridge-category','shop-upgrades')
            press('1000',1);snapshot('shop-cartridge-options','shop-upgrades')
            press('0002',1);snapshot('shop-cartridge-next','shop-upgrades')
            # A opens the choice list above. Within it use only directions/B;
            # never confirm a purchase. Images establish the actual capacity
            # labels; the shared scene name alone cannot prove selected content.
            if a.exercise_ak47 or a.exercise_s_system:
                option_count=4 if a.exercise_ak47 else 3
                for index in range(2,option_count):
                    press('0002',1);snapshot('shop-cartridge-option-'+str(index),'shop-upgrades')
                for button in (['0001']*(option_count-1)+['0002']*(option_count-1))*2:press(button,.4)
                for _ in range(4):press('2000',.6);press('1000',.6)
                snapshot('shop-cartridge-reentered','shop-upgrades')
            else:
                for button in ['0001','0002']*4:press(button,.4)
                for _ in range(4):press('2000',.6);press('1000',.6)
                snapshot('shop-cartridge-reentered','shop-upgrades')
            press('2000',1)
            press('0001',.5);press('0001',.5);press('1000',1)
            snapshot('shop-barrel-restored','shop-barrel')
            finish_recording(extra_video,'cartridge-selection-motion')
        if a.exercise_transitions:
            finish_recording(video,'shop-motion')
            extra_video=transition_recording('weapon-selection-motion')
            for button in ['0002']*6+['0001']*6:press(button,.4)
            press('2000',4);press('2000',4)
            for button in ['0002']*3+['0001']*3:press(button,.5)
            press('2000',4)
            finish_recording(extra_video,'weapon-selection-motion')
        else:
            press('2000',4);press('2000',4);press('2000',4)
        snapshot('shop-return-main')
        finish_recording(video,'shop-motion')
        press('0002',1);press('1000',4);snapshot('shop-secondary')
        press('2000',4)
        snapshot('shop-main-after-secondary','shop-main')
        # Returning from the weapon list resets Contacts to Primary (row 0).
        for _ in range(3):press('0002',1)
        press('1000',4);snapshot('shop-gear')
        if a.capture_at=='gear':trigger_capture()
        press('1000',4);snapshot('shop-armor')
        if a.capture_at=='armor':trigger_capture()
        press('2000',4);press('0002',1);press('1000',4);snapshot('shop-mask')
        if a.capture_at=='mask':trigger_capture()
        if a.exercise_transitions:
            # Navigation only: do not confirm purchases. Both endpoints are
            # verified through retail scene names, not inferred from screenshots.
            extra_video=transition_recording('gear-selection-motion')
            for button in ['0002']*5+['0001']*5:press(button,.4)
            press('2000',1);press('0001',.5);press('1000',1)
            for button in ['0002']*3+['0001']*3:press(button,.4)
            finish_recording(extra_video,'gear-selection-motion')
            snapshot('shop-armor-cycled','shop-armor')
        if a.frame_color_probe:
            event(frame_color_finish_requested=True)
            (out/'frame-colors/finish.trigger').write_text('Finish before the runtime hard-exits.\n')
            deadline=time.monotonic()+15
            while not (out/'frame-colors/summary.json').exists():
                alive()
                if time.monotonic()>deadline:raise RuntimeError('Native frame-color completion did not arrive')
                time.sleep(.1)
        (out/'scenario.complete').write_text('Shop observations and requested recordings completed.\n')
        event(finish_requested=True)
        child.wait(timeout=140)
        done=json.loads((out/'probe.json').read_text());travel=json.loads((out/'travel.json').read_text())
        if child.returncode or done.get('timed_out') or done.get('exit_code_before_cleanup')!=0:raise RuntimeError('Native run did not close normally')
        if not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']:raise RuntimeError('Source saves changed')
        if a.frame_color_probe:
            from analyze_frame_colors import analyze
            colors=analyze(out/'frame-colors')
            begin=colors['steady_clock_first_ms']-steady_origin_ms
            end=colors['steady_clock_last_ms']-steady_origin_ms
            finish=next(e['seconds'] for e in report['events'] if e.get('frame_color_finish_requested'))*1000
            if not 0<=begin<75000 or end<finish-1000 or end>time.perf_counter()*1000-steady_origin_ms:
                raise RuntimeError('Frame color timeline does not surround the shop route')
            (out/'frame-colors/analysis.json').write_text(json.dumps(colors,indent=2)+'\n')
            report['frame_color_coverage_verified']=True
        for name,proc in helpers:
            if proc.wait(timeout=30)!=0:raise RuntimeError('Failed '+name)
        if a.renderdoc:
            gpu=json.loads((out/'renderdoc-capture.json').read_text())
            if gpu.get('error') or len(gpu.get('captures',[]))!=1:raise RuntimeError('Missing GPU capture')
        report['complete']=True
    except Exception as error:
        report['error']=str(error)
        # The scenario has already failed; close its owned window promptly
        # instead of silently waiting through the remaining observation budget.
        if out.exists() and (out/'running.json').exists():
            owned_pid=json.loads((out/'running.json').read_text())['pid']
            for hwnd,_,_,_ in game_windows(owned_pid):
                windll.user32.PostMessageW(wintypes.HWND(hwnd),0x0010,0,0)
        raise
    finally:
        if out.exists() and (out/'controller.state').exists():(out/'controller.state').write_text('0000 0 0 0 0 0 0\n')
        if child.poll() is None:child.wait(timeout=360)
        for name,proc in helpers:
            try:proc.wait(timeout=30)
            except subprocess.TimeoutExpired:proc.kill();proc.wait();report[name+'_timeout']=True
        for log in logs:log.close()
        report['elapsed_seconds']=time.monotonic()-started
        if out.exists():(out/'shop-scenario.json').write_text(json.dumps(report,indent=2)+'\n')
    print(out)

if __name__=='__main__':main()
