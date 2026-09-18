"""Pulse the opt-in test controller; no desktop input injection."""
import argparse
from pathlib import Path
import time
import json
import os
import tempfile


def publish(path, state):
    # Readers must see a whole state, never the truncated file between writes.
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(state)
        for attempt in range(100):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(.005)
    finally:
        temporary.unlink(missing_ok=True)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('state_file', type=Path)
parser.add_argument('--seconds', type=float, default=.2)
parser.add_argument('--buttons', type=lambda x: int(x, 16), default=0)
parser.add_argument('--lt', type=int, default=0)
parser.add_argument('--rt', type=int, default=0)
parser.add_argument('--lx', type=int, default=0)
parser.add_argument('--ly', type=int, default=0)
parser.add_argument('--rx', type=int, default=0)
parser.add_argument('--ry', type=int, default=0)
parser.add_argument('--capture-pid', type=int, help='Capture this test process while input is held')
parser.add_argument('--capture-output', type=Path)
parser.add_argument('--settle', type=float, default=0, help='Hold buttons/triggers with neutral sticks before capture')
parser.add_argument('--fire', type=float, default=0, help='Then hold both triggers for this duration')
args = parser.parse_args()
if bool(args.capture_pid) != bool(args.capture_output):
    parser.error('Capture requires both --capture-pid and --capture-output')
if not 0 <= args.settle <= 10 or not 0 <= args.fire <= 10:
    parser.error('Settle/fire durations must be 0 to 10 seconds')
if not 0 < args.seconds <= 30 or not 0 <= args.buttons <= 65535 or not all(0 <= x <= 255 for x in (args.lt,args.rt)) or not all(-32768 <= x <= 32767 for x in (args.lx,args.ly,args.rx,args.ry)):
    parser.error('Invalid pulse duration or controller value')
state = f'{args.buttons:04x} {args.lt} {args.rt} {args.lx} {args.ly} {args.rx} {args.ry}\n'
args.state_file.parent.mkdir(parents=True, exist_ok=True)
with args.state_file.with_suffix('.events.jsonl').open('a') as record:
    record.write(json.dumps({'unix_time': time.time(), 'seconds': args.seconds, 'state': state.strip(), 'settle': args.settle, 'fire': args.fire}) + '\n')
try:
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        publish(args.state_file, state)
        time.sleep(min(.1, max(0, end - time.monotonic())))
    for duration, tail_state in [
        (args.settle, f'{args.buttons:04x} {args.lt} {args.rt} 0 0 0 0\n'),
        (args.fire, f'{args.buttons:04x} 255 255 0 0 0 0\n')]:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            publish(args.state_file, tail_state)
            time.sleep(min(.1, max(0, end - time.monotonic())))
    if args.capture_pid:
        from probe import windows, capture
        matches = windows(args.capture_pid)
        if len(matches) != 1:
            raise RuntimeError('Expected one test process window')
        hwnd, title, width, height = matches[0]
        if not capture(hwnd, width, height, args.capture_output):
            raise RuntimeError('Window capture failed')
finally:
    publish(args.state_file, '0 0 0 0 0 0 0\n')
