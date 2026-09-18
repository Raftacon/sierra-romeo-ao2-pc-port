"""Capture this game's window during and after a bounded physical input."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from probe import capture, windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture-seconds', type=float, default=8)
    parser.add_argument('--input', nargs=argparse.REMAINDER, required=True,
                        help='Final argument: options for pc_input_probe.py')
    args = parser.parse_args()
    if not 1 <= args.capture_seconds <= 30:
        parser.error('--capture-seconds must be between 1 and 30')
    output = args.output.resolve()
    if output.exists(): parser.error('Use a new output directory')
    input_tool = Path(__file__).with_name('pc_input_probe.py')
    command = [sys.executable, str(input_tool), str(args.pid), '--focus']
    # Validate the executable and foreground window before any capture. This
    # invocation sends no key or mouse motion.
    subprocess.run([*command, '--seconds', '.01'], check=True, stdout=subprocess.DEVNULL)
    output.mkdir(parents=True)
    with (output / 'input.log').open('w') as log:
        process = subprocess.Popen([*command, *args.input], stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        start = time.monotonic()
        frames = []
        try:
            while time.monotonic() - start < args.capture_seconds:
                if process.poll() not in (None, 0): raise RuntimeError('Physical input failed')
                targets = windows(args.pid)
                if len(targets) != 1: raise RuntimeError('Game window disappeared')
                hwnd, _, width, height = targets[0]
                name = f'{len(frames):03d}.png'
                if not capture(hwnd, width, height, output / name):
                    raise RuntimeError('Window capture failed')
                frames.append({'file': name, 'seconds': time.monotonic() - start})
                time.sleep(.08)
        finally:
            process.wait(timeout=15)  # The input helper always releases held input.
            (output / 'sequence.json').write_text(json.dumps({
                'pid': args.pid, 'input': args.input, 'input_exit_code': process.returncode,
                'captures': frames, 'interpretation': 'Inspect the sequence; no automatic visual-parity pass.'
            }, indent=2) + '\n')
        if process.returncode != 0: raise RuntimeError('Physical input failed')
    print(f'Captured {len(frames)} images in {output}')


if __name__ == '__main__': main()
