"""Exercise the real Private/Public Co-op menu with an isolated profile.

No direct engine travel or single-player bootstrap is used. The optional gate
diagnostic only exposes the original menu continuation for backend development;
it does not authenticate with Xbox Live/EA or establish a working PC session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


def summarize_flow(text, selection):
    """Record reached stages; a menu/transport observation never proves co-op."""
    commands = [guest or direct for guest, direct in re.findall(
        r'Guest ConsoleCommand:.*?text=([^\r\n]*)|Game \[([^\r\n]*)\]:', text)]
    selected = 'causeevent MissionSetup_Host' + selection.capitalize()
    alternate = 'causeevent MissionSetup_Host' + ('Public' if selection == 'private' else 'Private')
    forbidden = [s for s in commands if s == 'causeevent MissionSetup_SinglePlayer'
                 or s.lower().startswith(('open ', 'servertravel ', 'clienttravel '))]
    operations = re.findall(r'Campaign menu: (\w+) object=', text)
    backend = re.findall(r'Campaign backend: ([^\r\n]*)', text)
    requests = [dict(operation=operation, map=map_name, difficulty=int(difficulty), fixture_mode=int(mode))
                for operation, map_name, difficulty, mode in re.findall(
                    r'Campaign request: (\w+) map=([^\r\n]*) difficulty=(\d+) ui_fixture_mode=(-?\d+)', text)]
    return dict(expected_menu_command=selected, expected_menu_reached=selected in commands,
                ui_fixture_used='Campaign UI fixture:' in text,
                unexpected_menu_reached=alternate in commands, direct_travel_or_single_player=forbidden,
                menu_route_verified=selected in commands and alternate not in commands and not forbidden,
                plasma_connect_observed='PlasmaConnect' in operations,
                plasma_disconnect_observed='PlasmaDisconnect' in operations,
                host_action_observed='HostAO2CoopGame' in operations,
                match_start_observed='StartCoopMatch' in operations,
                pc_lobby_callbacks=re.findall(r'PC co-op native callback: (\w+) members=(\d+)', text)[:512],
                pc_lobby_states=re.findall(r'PC co-op lobby state: phase=(\d+), members=(\d+), revision=(\d+)', text)[:512],
                pc_lobby_listening='PC co-op lobby listening:' in text,
                pc_campaign_start_requested='PC co-op campaign preparation requested:' in text,
                pc_native_equipment=re.findall(r'PC co-op native equipment: ([^\r\n]*)', text)[:16],
                pc_preparation_completed='PC co-op native preparation complete:' in text,
                pc_native_import_verified=bool(re.search(r'PC co-op native import verified: id=\d+, accepted=true', text)),
                pc_native_checkpoints_exported=re.findall(r'PC co-op native checkpoint exported: ([^\r\n]*)', text)[:16],
                pc_native_checkpoints_imported=re.findall(r'PC co-op native checkpoint imported: ([^\r\n]*)', text)[:16],
                pc_viewport_descriptors=re.findall(r'PC co-op native viewport descriptor: ([^\r\n]*)', text)[:8],
                pc_native_travel_requested='PC co-op native campaign travel requested:' in text,
                pc_remote_registration_observed='PC co-op simulation trace: 8294D1A0 leave' in text,
                pc_native_barriers_sent=re.findall(r'PC co-op native barrier sent: ([^\r\n]*)', text)[:64],
                pc_native_barriers_received=re.findall(r'PC co-op native barrier received: ([^\r\n]*)', text)[:64],
                pc_native_synchronization_failed=('PC co-op native synchronization failed:' in text or
                    bool(re.search(r'PC co-op native leave: [^\r\n]*synchronization_failure=true', text))),
                pc_native_leaves=re.findall(r'PC co-op native leave: ([^\r\n]*)', text)[:32],
                pc_native_session_resets=re.findall(r'PC co-op native session reset complete[^\r\n]*', text)[:32],
                pc_native_player_releases=re.findall(r'PC co-op native player records released: ([^\r\n]*)', text)[:32],
                pc_native_transitions=re.findall(r'PC co-op native transition requested: ([^\r\n]*)', text)[:32],
                pc_midmission_travel_repairs=re.findall(r'PC co-op mid-mission travel option restored: ([^\r\n]*)', text)[:32],
                pc_native_loader_modes=re.findall(r'PC co-op native loader simulation applied: synchronized=(\d+)', text)[:64],
                pc_shopping_completions_sent=re.findall(r'PC co-op native shopping completion sent: accepted=(\w+)', text)[:32],
                pc_shopping_completions_received=text.count('PC co-op native shopping completion received'),
                pc_connection_setup_requests=re.findall(r'PC co-op connection setup requested: private=(\w+)', text)[:32],
                pc_connection_setup_accepts=re.findall(r'PC co-op connection setup accepted: ([^\r\n]*)', text)[:32],
                pc_connection_setup_cancels=text.count('PC co-op connection setup cancelled'),
                pc_lobby_kicks=re.findall(r'PC co-op lobby kick: index=(\d+), accepted=(\w+)', text)[:32],
                pc_invitation_dialogs=text.count('PC co-op invitation details requested'),
                pc_checkpoint_cash_sent=re.findall(r'PC co-op native checkpoint cash sent: total=(\d+), accepted=(\w+)', text)[:64],
                pc_checkpoint_cash_received=re.findall(r'PC co-op native checkpoint cash received: total=(\d+), sequence=(\d+)', text)[:64],
                pc_native_failure_origins=re.findall(r'PC co-op native failure origin: ([^\r\n]*)', text)[:16],
                pc_native_checksum_observations=re.findall(r'PC co-op checksum mismatch: ([^\r\n]*)', text)[:64],
                pc_native_inputs_sent=re.findall(r'PC co-op native input sent: ([^\r\n]*)', text)[:64],
                pc_native_inputs_received=re.findall(r'PC co-op native input received: ([^\r\n]*)', text)[:64],
                pc_native_input_decode_failed='PC co-op native input decode failed:' in text,
                pc_connection_timing=re.findall(r'PC co-op connection timing: sample=(\d+), round_trip_ms=(\d+)',text)[-16:],
                pc_native_simulation_resets=re.findall(r'PC co-op native simulation reset: ([^\r\n]*)', text)[:16],
                setup_requests=requests[:512],
                backend_observations=backend[:512], campaign_coop_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--selection', choices=('private', 'public'), required=True)
    parser.add_argument('--pc-gate-diagnostic', action='store_true')
    parser.add_argument('--pc-ui-diagnostic', action='store_true',
                        help='Inspect retail setup without a connected service; requires gate diagnostic')
    parser.add_argument('--directory-url', help='Directory endpoint for interactive Public Co-op tests')
    parser.add_argument('--relay-endpoint', help='TLS relay endpoint for interactive Public Co-op hosting')
    parser.add_argument('--relay-test-ca',type=Path,help='Isolated DER trust fixture, accepted only for a 127.0.0.1 relay')
    parser.add_argument('--pc-service', action='store_true', help='Use the experimental real PC lobby control channel')
    parser.add_argument('--join', help='IPv4 host address; join through the selected co-op menu')
    parser.add_argument('--port', type=int, default=37001)
    parser.add_argument('--name', default='PC Host')
    parser.add_argument('--invite-file', type=Path, help='Private host writes an invite here; joining peer reads it')
    parser.add_argument('--submit-setup', action='store_true',
                        help='Select checkpoint/difficulty (private) or submit filters (public) in the UI fixture')
    parser.add_argument('--start-campaign', action='store_true',
                        help='Press A in the host lobby at 100 seconds; requires an admitted peer to proceed')
    parser.add_argument('--native-load-probe', action='store_true',
                        help='Trace experimental native co-op load after both peers verify equipment import')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--timing-trace',action='store_true',help='Bounded native frame timing observations')
    parser.add_argument('--rng-trace',action='store_true',help='Bounded read-only RNG caller trace around the opening load; requires --timing-trace')
    parser.add_argument('--buffer-probe',action='store_true',help='Experimental host-agreed input buffering; not validated for normal play')
    parser.add_argument('--buffer-probe-frames',type=int,choices=range(3,17),help='Fix the host diagnostic lead for reproducibility; requires --buffer-probe')
    parser.add_argument('--interactive-buffer-frames',type=int,choices=range(3,17),help='Explicit fixed-buffer experiment through the normal connection panel')
    args = parser.parse_args()
    if args.interactive_buffer_frames is not None and (args.pc_service or args.pc_gate_diagnostic or args.pc_ui_diagnostic or args.buffer_probe):
        parser.error('--interactive-buffer-frames requires the normal interactive co-op flow')
    if args.buffer_probe_frames is not None and not args.buffer_probe:
        parser.error('--buffer-probe-frames requires --buffer-probe')
    if args.timing_trace and not args.native_load_probe:
        parser.error('--timing-trace requires --native-load-probe')
    if args.rng_trace and not args.timing_trace:
        parser.error('--rng-trace requires --timing-trace')
    if args.buffer_probe and (not args.native_load_probe or args.join):
        parser.error('--buffer-probe requires a native host load probe')
    if args.pc_ui_diagnostic and not args.pc_gate_diagnostic:
        parser.error('--pc-ui-diagnostic requires --pc-gate-diagnostic')
    if args.pc_service and args.pc_ui_diagnostic:
        parser.error('The offline UI fixture and real PC service are mutually exclusive')
    if args.submit_setup and not (args.pc_ui_diagnostic or args.pc_service):
        parser.error('--submit-setup requires --pc-ui-diagnostic or --pc-service')
    if args.join and (not args.pc_service or args.submit_setup):
        parser.error('--join requires --pc-service and does not use --submit-setup')
    if args.start_campaign and (not args.pc_service or not args.submit_setup or args.seconds < 120):
        parser.error('--start-campaign requires --pc-service, --submit-setup and at least 120 seconds')
    if args.native_load_probe and not args.pc_service:
        parser.error('--native-load-probe requires --pc-service')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1..65535')
    root = Path(__file__).resolve().parents[1]
    out, source = args.output.resolve(), args.profile.resolve()
    if out.exists() or not source.is_dir() or source in out.parents or out in source.parents:
        parser.error('Require a new output path separate from an existing source profile')
    if not 90 <= args.seconds <= 1800:
        parser.error('Duration must be 90..1800 seconds')
    before = hashes(source)
    out.mkdir(parents=True)
    shutil.copytree(source, out/'profile')
    # Start -> Campaign -> Private/Public Co-op. Never select Single Player.
    script = ['22000 200 0010', '30000 200 1000', '38000 150 0002', '40000 150 0002']
    if args.selection == 'public':
        script.append('42000 150 0002')
    script.append('44000 200 1000')
    if args.submit_setup:
        script.append('57000 200 1000')
        if args.selection == 'private':
            script.append('64000 200 1000')
    if args.start_campaign:
        script.append('100000 200 1000')
    (out/'menu.script').write_text(''.join(line+' 0 0 0 0 0 0\n' for line in script))
    native = out/'native'
    env = {k:v for k,v in os.environ.items() if not k.startswith('AOT_')}
    if args.directory_url:
        env['AOT_COOP_DIRECTORY_URL']=args.directory_url
    if args.relay_endpoint:
        env['AOT_COOP_RELAY_ENDPOINT']=args.relay_endpoint
    if args.relay_test_ca:
        env['AOT_COOP_RELAY_TEST_CA']=str(args.relay_test_ca.resolve())
    if args.interactive_buffer_frames is not None:
        env['AOT_PC_COOP_BUFFER_PROBE']='1'
        env['AOT_PC_COOP_BUFFER_FRAMES']=str(args.interactive_buffer_frames)
    env.update(AOT_TRACE_COMMANDS='1', AOT_INPUT_SCRIPT=str(out/'menu.script'), AOT_INPUT_STATE=str(native/'controller.state'),
               AOT_GAME_COMMANDS=str(native/'game.commands'))
    if args.pc_gate_diagnostic:
        env['AOT_PC_COOP_DIAGNOSTIC'] = '1'
    if args.pc_ui_diagnostic:
        env['AOT_PC_COOP_UI_DIAGNOSTIC'] = '1'
    if args.pc_service:
        env.update(AOT_PC_COOP_SERVICE='1', AOT_PC_COOP_PORT=str(args.port), AOT_PC_COOP_NAME=args.name)
        if args.join:
            env['AOT_PC_COOP_JOIN'] = args.join
        if args.invite_file:
            env['AOT_PC_COOP_INVITE_FILE'] = str(args.invite_file.resolve())
        if args.native_load_probe:
            env['AOT_PC_COOP_LOAD_PROBE'] = '1'
        if args.timing_trace:
            env['AOT_PC_COOP_TIMING_TRACE']='1'
        if args.rng_trace:
            env['AOT_PC_COOP_RNG_TRACE']='1'
        if args.buffer_probe:
            env['AOT_PC_COOP_BUFFER_PROBE']='1'
        if args.buffer_probe_frames is not None:
            env['AOT_PC_COOP_BUFFER_FRAMES']=str(args.buffer_probe_frames)
    cmd = [sys.executable, str(root/'tools/probe.py'), '--output', str(native),
           '--user-data', str(out/'profile'), '--cache-root', str(out/'cache'),
           '--gpu-plugin', 'spatial', '--network-trace', '--seconds', str(args.seconds),
           '--capture-interval', '0', '--log-level', 'debug', '--use-saved-settings', '--',
           '--fullscreen=false', '--window_width=1280', '--window_height=720',
           '--input_backend=xinput', '--mnk_mode=false', '--aot_keyboard_mouse=false',
           '--readback_resolve=fast', '--readback_resolve_half_pixel_offset=true',
           '--anisotropic_override=5', '--aot_fps=60', '--resolution_scale=1', '--swap_post_effect=fxaa']
    report = dict(selection=args.selection, pc_gate_diagnostic=args.pc_gate_diagnostic,
                  interactive_connection=not (args.pc_service or args.pc_gate_diagnostic or args.pc_ui_diagnostic),
                  pc_ui_diagnostic=args.pc_ui_diagnostic,
                  pc_service=args.pc_service, join=args.join, port=args.port,
                  start_campaign=args.start_campaign,
                  native_load_probe=args.native_load_probe,
                  timing_trace=args.timing_trace,
                  rng_trace=args.rng_trace,
                  buffer_probe=args.buffer_probe,
                  buffer_probe_frames=args.buffer_probe_frames,
                  interactive_buffer_frames=args.interactive_buffer_frames,
                  submit_setup=args.submit_setup,
                  command=cmd, source=str(source), source_hashes=before, campaign_coop_verified=False)
    (out/'scenario.json').write_text(json.dumps(report, indent=2)+'\n')
    try:
        with (out/'launcher.log').open('w') as log:
            result = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        report['probe_exit_code'] = result.returncode
    finally:
        report['source_profile_unchanged'] = hashes(source) == before
        log_path = native/'runtime.log'
        if log_path.exists():
            report['flow'] = summarize_flow(log_path.read_text(errors='replace'), args.selection)
        (out/'scenario.json').write_text(json.dumps(report, indent=2)+'\n')
    if not report['source_profile_unchanged'] or report['probe_exit_code']:
        raise RuntimeError('Probe failed; inspect scenario.json and launcher.log')


if __name__ == '__main__':
    main()
