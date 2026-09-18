"""Verify temporary retail shadow controls in one copied-profile alley session.

Captures and read-only engine observations distinguish command delivery from
actual state changes. This is a diagnostic comparison, not a flicker fix or
performance benchmark. Original runtime values are restored before closing.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from probe import capture, game_windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args()
    root, out = Path(__file__).resolve().parents[1], args.output.resolve()
    if out.exists() or out.with_name(out.name + '-profile').exists():
        parser.error('Use new output and copied-profile paths')
    child = subprocess.Popen([sys.executable, str(root / 'tools/checkpoint_probe.py'),
        '--output', str(out), '--profile', str(args.profile.resolve()),
        '--checkpoint', '01_04', '--verify-checkpoint', '--gpu-plugin', 'spatial',
        '--window', '1920', '1080', '--controller-only', '--capture-interval', '30'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    started, events, initial, pid = time.monotonic(), [], None, None
    report = {'scope': __doc__.strip(), 'events': events}

    def alive():
        if child.poll() is not None:
            raise RuntimeError('Owned checkpoint probe ended before control verification')

    def command(text):
        alive()
        log = out / 'runtime.log'
        offset = log.stat().st_size
        with (out / 'game.commands').open('a') as target:
            target.write(text + '\n')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive()
            with log.open(errors='replace') as source:
                source.seek(offset)
                tail = source.read()
            marker = f'Game [{text}]:'
            if marker in tail:
                events.append({'seconds': time.monotonic() - started, 'command': text,
                               'response': tail[tail.index(marker):].splitlines()[0]})
                return
            time.sleep(.1)
        raise RuntimeError('No acknowledgment for ' + text)

    def observe(label, record=True):
        alive()
        with (out / (label + '-inspect.log')).open('w') as log:
            subprocess.run([sys.executable, str(root / 'tools/inspect_hud_objects.py'),
                '--probe', str(out), '--output', str(out / (label + '.json')), '--engine'],
                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=25,
                creationflags=subprocess.CREATE_NO_WINDOW)
        raw = json.loads((out / (label + '.json')).read_text())
        engines = [o for o in raw['objects'] if o['class'] == 'GameEngine'
                   and not o['name'].startswith('Default__')]
        if len(engines) != 1 or raw['failed_reads']:
            raise RuntimeError('Require one live retail GameEngine and complete reads')
        engine = engines[0]
        state = {'engine': engine['object'],
                 'quality': int(engine['words'][0x3AC // 4], 16),
                 'dynamic_shadows': int(raw['native_system_settings']['words'][364 // 4], 16)}
        if state['quality'] not in (0, 1, 2) or state['dynamic_shadows'] not in (0, 1):
            raise RuntimeError('Unexpected retail shadow-control values')
        events.append({'seconds': time.monotonic() - started, 'phase': label, 'state': state})
        if record:
            windows = game_windows(pid)
            if len(windows) != 1 or not capture(windows[0][0], windows[0][2], windows[0][3], out / (label + '.png')):
                raise RuntimeError('Expected one capturable native game window')
            with (out / (label + '-video.log')).open('w') as log:
                subprocess.run([sys.executable, str(root / 'tools/window_video_probe.py'),
                    '--pid', str(pid), '--output', str(out / (label + '-video')), '--seconds', '3'],
                    stdout=log, stderr=subprocess.STDOUT, check=True, timeout=35,
                    creationflags=subprocess.CREATE_NO_WINDOW)
        return state

    try:
        expected = "CurrCheckpoint = AO2Checkpoint'01_map_back_alley_shell.TheWorld.PersistentLevel.AO2Checkpoint_2'"
        while time.monotonic() - started < 115:
            alive()
            log = out / 'runtime.log'
            if log.exists() and expected in log.read_text(errors='replace'):
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Checkpoint was not verified before the observation deadline')
        pid = json.loads((out / 'running.json').read_text())['pid']
        initial = observe('baseline')
        command('SHADOWQUALITY 2')
        quality = observe('quality-two')
        if quality != {**initial, 'quality': 2}:
            raise RuntimeError('Quality command did not produce the expected state')
        command(f"SHADOWQUALITY {initial['quality']}")
        if observe('quality-restored', False) != initial:
            raise RuntimeError('Quality did not restore')
        command('SCALE SET DYNAMICSHADOWS ' + ('FALSE' if initial['dynamic_shadows'] else 'TRUE'))
        toggled = observe('dynamic-opposite')
        if toggled != {**initial, 'dynamic_shadows': 1 - initial['dynamic_shadows']}:
            raise RuntimeError('Dynamic-shadow command did not produce the expected state')
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        try:
            if initial is not None and child.poll() is None:
                command(f"SHADOWQUALITY {initial['quality']}")
                command('SCALE SET DYNAMICSHADOWS ' + ('TRUE' if initial['dynamic_shadows'] else 'FALSE'))
                restored = observe('restored')
                report['restored'] = restored == initial
                if not report['restored']:
                    raise RuntimeError('Final retail control restoration failed')
        except BaseException as error:
            report['restoration_error'] = str(error)
            raise
        finally:
            if out.exists():
                (out / 'shadow-controls.json').write_text(json.dumps(report, indent=2) + '\n')
            # checkpoint_probe owns the native lifetime and profile/hash checks.
            child.wait(timeout=max(5, 195 - (time.monotonic() - started)))
    native = json.loads((out / 'probe.json').read_text())
    if child.returncode or native['timed_out'] or native['exit_code_before_cleanup'] != 0:
        raise RuntimeError('Native checkpoint probe did not complete normally')
    print(out)


if __name__ == '__main__':
    main()
