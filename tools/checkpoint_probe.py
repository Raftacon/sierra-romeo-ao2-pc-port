"""Travel through the retail checkpoint loader using a copied test profile."""
import argparse
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
from probe import capture, game_windows
from inspect_checkpoint import checkpoint_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--verify-checkpoint', action='store_true',
                        help='Query the live checkpoint manager and require the requested retail actor reference after loading')
    parser.add_argument('--capture-interval', type=int, default=5, choices=range(0, 61),
                        metavar='SECONDS', help='Periodic screenshot spacing; 0 disables periodic captures, phase captures remain enabled')
    parser.add_argument('--next-checkpoint',
                        help='Verify console queries, travel again at +60s and query the new player')
    parser.add_argument('--observe-seconds', type=int, default=90,
                        help='Seconds to observe after travel (90 to 600; default 90)')
    parser.add_argument('--finish-trigger', type=Path,
                        help='Optional new file whose creation ends observation after the required check window')
    parser.add_argument('--check-window', type=int, choices=(30, 90), default=90,
                        help='Required post-travel checks before finish-trigger can end the run; 30 is for single-checkpoint visual iteration')
    parser.add_argument('--survey-cinematics', type=int, default=0, metavar='SECONDS',
                        help='For up to 60 seconds after travel (or remote-event activation), sample cinematic state and capture once per second')
    parser.add_argument('--remote-event',
                        help='At +30s, request one named retail remote event through the diagnostic activation bridge')
    parser.add_argument('--scale', type=int, choices=[1, 2, 3], default=1)
    parser.add_argument('--readback', choices=['fast', 'full', 'none'], default='fast',
                        help='Resolve readback mode; none is a correctness-risk diagnostic, not a gameplay preset')
    parser.add_argument('--gpu-plugin', default='xenos')
    parser.add_argument('--cache-root', type=Path, help='Separate shader cache for a controlled comparison')
    parser.add_argument('--renderdoc', type=Path, help='Opt-in renderdoccmd.exe for the owned probe process')
    parser.add_argument('--post-effect', choices=['saved', 'none', 'fxaa', 'fxaa_extreme'], default='fxaa')
    parser.add_argument('--fullscreen', action='store_true', help='Use the native fullscreen window')
    parser.add_argument('--window', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--controller-only', action='store_true',
                        help='Isolate camera input from desktop mouse/keyboard; live controller.state is available in the output directory')
    parser.add_argument('--log-level', choices=('info','debug'), default='debug')
    parser.add_argument('--guest-vsync', action=argparse.BooleanOptionalAction, default=True,
                        help='Diagnostic guest refresh-timer selection; host presentation can be overridden separately')
    parser.add_argument('--extra', nargs=argparse.REMAINDER, default=[])
    parser.add_argument('--continue-tutorial', action='store_true',
                        help='Continue the 04_00 tutorial at +30s (XInput A with --controller-only, otherwise E)')
    args = parser.parse_args()
    if args.check_window == 30 and (args.next_checkpoint or args.survey_cinematics):
        parser.error('--check-window 30 is for a single checkpoint without a cinematic survey')
    if args.finish_trigger:
        args.finish_trigger = args.finish_trigger.resolve()
        if args.finish_trigger.exists():
            parser.error('--finish-trigger must not already exist')
    if args.window and not all(320 <= value <= 4096 for value in args.window):
        parser.error('Window dimensions must be in [320, 4096]')
    if not 90 <= args.observe_seconds <= 600:
        parser.error('--observe-seconds must be between 90 and 600')
    if not 0 <= args.survey_cinematics <= 60:
        parser.error('--survey-cinematics must be between 0 and 60')
    if args.remote_event and not re.fullmatch(r'[A-Za-z0-9_]{1,96}', args.remote_event):
        parser.error('--remote-event requires a single event name (up to 96 characters)')
    root = Path(__file__).resolve().parents[1]
    checkpoint = root / 'assets/AO2Game/Checkpoints' / args.checkpoint
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.checkpoint) or not checkpoint.is_file():
        parser.error('Checkpoint must name an existing retail checkpoint file')
    if args.next_checkpoint and (not re.fullmatch(r'[A-Za-z0-9_]+', args.next_checkpoint) or
                                not (checkpoint.parent / args.next_checkpoint).is_file()):
        parser.error('--next-checkpoint must name an existing retail checkpoint file')
    if args.continue_tutorial and args.checkpoint != '04_00':
        parser.error('--continue-tutorial currently supports only the observed 04_00 prompt')
    checkpoint_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    checkpoint_states = {args.checkpoint: checkpoint_metadata(checkpoint)} if args.verify_checkpoint else {}
    if args.verify_checkpoint and args.next_checkpoint:
        checkpoint_states[args.next_checkpoint] = checkpoint_metadata(checkpoint.parent / args.next_checkpoint)
    checkpoint_hashes = {args.checkpoint: checkpoint_hash}
    if args.next_checkpoint:
        checkpoint_hashes[args.next_checkpoint] = hashlib.sha256(
            (checkpoint.parent / args.next_checkpoint).read_bytes()).hexdigest()
    output = args.output.resolve()
    profile = output.with_name(output.name + '-profile')
    source = args.profile.resolve()
    if output.exists() or profile.exists(): parser.error('Use new output/profile paths')
    if not source.is_dir() or source in profile.parents or profile in source.parents:
        parser.error('A separate existing test profile is required')
    hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    shutil.copytree(source, profile)
    env = dict(os.environ)
    for name in ('AOT_INPUT_STATE', 'AOT_OPEN_CONSOLE', 'AOT_PROFILE', 'AOT_TRACE_MOUSE'):
        env.pop(name, None)
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-graphics.script'), AOT_TEST_KBM='1',
               AOT_FRAME_LOG=str(output / 'frame-times.csv'),
               AOT_GAME_COMMANDS=str(output / 'game.commands'))
    if args.controller_only:
        env.pop('AOT_TEST_KBM', None)
        env['AOT_INPUT_STATE'] = str(output / 'controller.state')
    display_flags = []
    if args.window:
        display_flags = [f'--window_width={args.window[0]}', f'--window_height={args.window[1]}']
        if not args.fullscreen:
            display_flags.append('--fullscreen=false')
    command = [sys.executable, str(root / 'tools/probe.py'),
               *(['--fullscreen'] if args.fullscreen else []), '--output', str(output),
               '--user-data', str(profile), '--seconds', str(150 + args.observe_seconds),
               '--capture-interval', str(args.capture_interval),
               '--log-level', args.log_level,
               '--gpu-plugin', args.gpu_plugin,
               *(['--cache-root', str(args.cache_root.resolve())] if args.cache_root else []),
               *(['--renderdoc', str(args.renderdoc.resolve())] if args.renderdoc else []),
               *(['--use-saved-settings'] if args.window else []),
               '--', *display_flags,
               *(['--aot_keyboard_mouse=false'] if args.controller_only else []),
               *args.extra, '--input_backend=xinput', '--readback_resolve=' + args.readback,
               '--vsync=' + str(args.guest_vsync).lower(), '--aot_fps=60', '--resolution_scale=' + str(args.scale),
               *(['--swap_post_effect=' + args.post_effect] if args.post_effect != 'saved' else []),
               '--anisotropic_override=5',
               '--readback_resolve_half_pixel_offset=true']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    events = []
    survey_start = None
    survey_next = 0
    survey_count = 0
    try:
        def wait_until(target, allow_finish=False):
            nonlocal survey_next, survey_count
            while time.monotonic() - start < target:
                if process.poll() is not None: raise RuntimeError('Game exited before observation')
                now = time.monotonic() - start
                if allow_finish and args.finish_trigger and args.finish_trigger.is_file():
                    events.append({'seconds': now, 'finish_trigger': str(args.finish_trigger.resolve())})
                    break
                if (survey_start is not None and now < survey_start + args.survey_cinematics
                        and now >= survey_next):
                    # This is a read-only guest query. Captures are associated
                    # with request times, not guaranteed synchronous replies;
                    # the runtime log timestamps the actual returned states.
                    query = 'getall SeqAct_Interp bIsPlaying'
                    position_query = 'getall SeqAct_Interp Position'
                    with (output / 'game.commands').open('a') as commands:
                        commands.write(query + '\n' + position_query + '\n')
                    events.append({'seconds': now, 'cinematic_query': query,
                                   'cinematic_position_query': position_query})
                    snapshot(f'cinematic-{survey_count:03d}.png')
                    survey_count += 1
                    survey_next = time.monotonic() - start + 1
                time.sleep(.1)
        while not (output / 'running.json').exists():
            if process.poll() is not None or time.monotonic() - start > 30:
                raise RuntimeError('Probe did not start')
            time.sleep(.1)
        pid = json.loads((output / 'running.json').read_text())['pid']
        def snapshot(name):
            found = game_windows(pid)
            if len(found) != 1: raise RuntimeError('Expected one game window')
            hwnd, _, width, height = found[0]
            if not capture(hwnd, width, height, output / name): raise RuntimeError('Capture failed')
            events.append({'seconds': time.monotonic() - start, 'capture': name})
        def query_console(label):
            log = output / 'runtime.log'
            pattern = re.compile(r'Game \[getall AO2PlayerController Rotation\]:[^\n]*Rotation = \([^\n]+')
            count = len(pattern.findall(log.read_text(errors='replace')))
            with (output / 'game.commands').open('a') as commands:
                commands.write('getall AO2PlayerController Rotation\n')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                matches = pattern.findall(log.read_text(errors='replace'))
                if len(matches) > count:
                    events.append({'seconds': time.monotonic() - start,
                                   'console_query': label, 'response': matches[-1]})
                    return
                if process.poll() is not None: raise RuntimeError('Game exited during console query')
                time.sleep(.1)
            raise RuntimeError('Console did not return the player rotation after travel')
        def verify_checkpoint(name):
            query = 'getall AO2CheckpointManager CurrCheckpoint'
            log = output / 'runtime.log'
            offset = len(log.read_text(errors='replace'))
            with (output / 'game.commands').open('a') as commands:
                commands.write(query + '\n')
            deadline = time.monotonic() + 10
            expected = checkpoint_states[name]['actor_path']
            while time.monotonic() < deadline:
                tail = log.read_text(errors='replace')[offset:]
                references = re.findall(r"CurrCheckpoint = AO2Checkpoint'([^']+)'", tail)
                if references:
                    if set(references) != {expected}:
                        raise RuntimeError(f'Checkpoint manager reports {references}; expected {expected}')
                    events.append({'seconds': time.monotonic() - start,
                                   'checkpoint_reference_verified': name, 'actor_path': expected})
                    return
                if process.poll() is not None:
                    raise RuntimeError('Game exited during checkpoint verification')
                time.sleep(.1)
            raise RuntimeError('Checkpoint manager did not report the requested actor; inspect runtime.log')
        # The controller script starts at the first guest input poll, not at
        # process launch. Cold startup can consume 20-30 seconds; travelling at
        # launch+75 then interrupts the menu script before campaign bootstrap.
        script_offset = None
        running = json.loads((output / 'running.json').read_text())
        for _ in range(600):
            log = output / 'runtime.log'
            match = re.search(r'AOT scripted controller clock started: unix_ms=(\d+)',
                              log.read_text(errors='replace') if log.exists() else '')
            if match:
                script_offset = int(match[1]) / 1000 - running['start_unix_seconds']
                break
            if process.poll() is not None:
                raise RuntimeError('Game exited before controller script started')
            time.sleep(.1)
        if script_offset is None or not 0 <= script_offset <= 60:
            raise RuntimeError('Controller script startup clock was not verified')
        events.append({'script_clock_offset_seconds': script_offset})
        wait_until(script_offset + 75)
        snapshot('before-travel.png')
        url = 'Checkpoint?LoadSaveGame?CheckpointToLoad=' + args.checkpoint + '?Difficulty=1'
        (output / 'game.commands').write_text('open ' + url + '\n')
        events.append({'seconds': time.monotonic() - start, 'travel_url': url})
        travel_start = time.monotonic() - start
        if args.survey_cinematics and not args.remote_event:
            survey_start = travel_start
            survey_next = travel_start
        for delay in ((5, 15, 30) if args.check_window == 30 else (5, 15, 30, 60, 90)):
            wait_until(travel_start + delay)
            snapshot(f'after-travel-{delay:03d}.png')
            if args.verify_checkpoint and (delay == 30 or (args.next_checkpoint and delay == 90)):
                verify_checkpoint(args.next_checkpoint if delay == 90 else args.checkpoint)
            if delay == 30 and args.remote_event:
                query = '@test-remote-event ' + args.remote_event
                with (output / 'game.commands').open('a') as commands:
                    commands.write(query + '\n')
                events.append({'seconds': time.monotonic() - start, 'remote_event_request': query})
                if args.survey_cinematics:
                    survey_start = time.monotonic() - start
                    survey_next = survey_start
            if args.next_checkpoint:
                if delay in (30, 90): query_console('first destination' if delay == 30 else 'second destination')
                if delay == 60:
                    next_url = 'Checkpoint?LoadSaveGame?CheckpointToLoad=' + args.next_checkpoint + '?Difficulty=1'
                    with (output / 'game.commands').open('a') as commands:
                        commands.write('open ' + next_url + '\n')
                    events.append({'seconds': time.monotonic() - start, 'travel_url': next_url})
            if delay == 30 and args.continue_tutorial:
                if args.controller_only:
                    try:
                        (output / 'controller.state').write_text('1000 0 0 0 0 0 0\n')
                        time.sleep(.25)
                    finally:
                        (output / 'controller.state').write_text('0000 0 0 0 0 0 0\n')
                    events.append({'seconds': time.monotonic() - start, 'input': 'XInput A continue tutorial, 250ms then release'})
                else:
                    subprocess.run([sys.executable, str(root / 'tools/pc_input_probe.py'),
                                    str(pid), '--focus', '--key', 'E', '--seconds', '.15'],
                                   check=True, stdout=subprocess.DEVNULL)
                    events.append({'seconds': time.monotonic() - start, 'input': 'E continue tutorial'})
        if args.observe_seconds > args.check_window:
            wait_until(travel_start + args.observe_seconds, allow_finish=True)
            snapshot('observation-end.png')
        found = game_windows(pid)
        if len(found) == 1:
            windll.user32.PostMessageW(wintypes.HWND(found[0][0]), 0x0010, 0, 0)
    finally:
        if output.exists():
            (output / 'travel.json').write_text(json.dumps({'checkpoint': args.checkpoint,
                'checkpoint_sha256': checkpoint_hash,
                'checkpoint_unchanged': hashlib.sha256(checkpoint.read_bytes()).hexdigest() == checkpoint_hash,
                'retail_checkpoint_sha256': checkpoint_hashes,
                'checkpoint_metadata': checkpoint_states,
                'retail_checkpoints_unchanged': all(hashlib.sha256((checkpoint.parent / name).read_bytes()).hexdigest() == digest
                                                    for name, digest in checkpoint_hashes.items()),
                'source_profile_unchanged': hashes == {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                                       for p in source.rglob('*') if p.is_file()},
                'controller_only': args.controller_only, 'requested_window': args.window,
                'required_check_window_seconds': args.check_window,
                'source_profile_sha256': hashes, 'events': events,
                'interpretation': 'Inspect game state and captures; no automatic travel/parity pass.'}, indent=2) + '\n')
        process.wait(timeout=130 + args.observe_seconds)
    report = json.loads((output / 'probe.json').read_text())
    if process.returncode != 0 or report['timed_out'] or report['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Probe did not close normally; inspect probe.json')
    if not json.loads((output / 'travel.json').read_text())['source_profile_unchanged']:
        raise RuntimeError('Source profile changed during observation; inspect travel.json')
    print(output)


if __name__ == '__main__': main()
