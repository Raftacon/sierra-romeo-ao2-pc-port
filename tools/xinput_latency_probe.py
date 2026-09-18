"""Measure real Windows XInput discovery latency without modifying game input."""
import argparse
import ctypes as c
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time


class Gamepad(c.Structure):
    _fields_ = [('buttons', c.c_uint16), ('lt', c.c_uint8), ('rt', c.c_uint8),
                ('lx', c.c_int16), ('ly', c.c_int16),
                ('rx', c.c_int16), ('ry', c.c_int16)]


class Vibration(c.Structure):
    _fields_ = [('left', c.c_uint16), ('right', c.c_uint16)]


class Capabilities(c.Structure):
    _fields_ = [('type', c.c_uint8), ('subtype', c.c_uint8),
                ('flags', c.c_uint16), ('gamepad', Gamepad),
                ('vibration', Vibration)]


class State(c.Structure):
    _fields_ = [('packet', c.c_uint32), ('gamepad', Gamepad)]


def summarize(values):
    ordered = sorted(values)
    return {'count': len(values), 'mean_ms': statistics.mean(values),
            'p95_ms': ordered[math.ceil(len(values) * .95) - 1],
            'p99_ms': ordered[math.ceil(len(values) * .99) - 1],
            'max_ms': max(values), 'over_1_ms': sum(v > 1 for v in values),
            'over_16_667_ms': sum(v > 1000 / 60 for v in values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60, choices=range(5, 301))
    parser.add_argument('--include-state', action='store_true', help='Also poll state after each successful discovery call')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows is required')
    output = args.output.resolve()
    if output.exists():
        parser.error('Use a new output directory')
    output.mkdir(parents=True)
    dll_path = Path(os.environ['SystemRoot']) / 'System32/xinput1_4.dll'
    dll = c.WinDLL(str(dll_path))
    get_caps = dll.XInputGetCapabilities
    get_caps.argtypes = [c.c_uint32, c.c_uint32, c.POINTER(Capabilities)]
    get_caps.restype = c.c_uint32
    get_state = dll.XInputGetState
    get_state.argtypes = [c.c_uint32, c.POINTER(State)]
    get_state.restype = c.c_uint32
    assert c.sizeof(Capabilities) == 20
    assert c.sizeof(State) == 16
    caps, state = Capabilities(), State()
    calls, batches = [], []
    polls = 0
    last_miss = [None] * 4
    begin = time.perf_counter()
    deadline = begin + args.seconds
    # Match the driver's 1100 ms per-slot disconnect backoff, targeting a
    # 60 Hz caller. Windows sleep granularity may reduce the observed rate.
    # The benchmark excludes DLL loading and Python file output.
    while time.perf_counter() < deadline:
        polls += 1
        batch_begin = time.perf_counter()
        count = 0
        for slot in range(4):
            now = time.perf_counter()
            if last_miss[slot] is not None and now - last_miss[slot] < 1.1:
                continue
            call_begin = time.perf_counter_ns()
            result = get_caps(slot, 0, c.byref(caps))
            elapsed_ms = (time.perf_counter_ns() - call_begin) / 1e6
            calls.append({'time_s': now - begin, 'slot': slot, 'api': 'capabilities',
                          'result': result, 'duration_ms': elapsed_ms})
            last_miss[slot] = time.perf_counter() if result == 1167 else None
            count += 1
            if args.include_state and result == 0:
                now = time.perf_counter()
                call_begin = time.perf_counter_ns()
                result = get_state(slot, c.byref(state))
                elapsed_ms = (time.perf_counter_ns() - call_begin) / 1e6
                calls.append({'time_s': now - begin, 'slot': slot, 'api': 'state',
                              'result': result, 'duration_ms': elapsed_ms})
                if result == 1167:
                    last_miss[slot] = time.perf_counter()
                count += 1
        if count:
            batches.append({'time_s': batch_begin - begin, 'calls': count,
                            'duration_ms': (time.perf_counter() - batch_begin) * 1000})
        time.sleep(max(0, 1 / 60 - (time.perf_counter() - batch_begin)))
    elapsed = time.perf_counter() - begin
    for name, rows in [('calls', calls), ('batches', batches)]:
        with (output / f'{name}.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    report = {'elapsed_seconds': elapsed,
              'dll': str(dll_path), 'dll_sha256': hashlib.sha256(dll_path.read_bytes()).hexdigest(),
              'backoff_ms': 1100, 'target_caller_hz': 60,
              'include_state': args.include_state,
              'polls': polls, 'observed_caller_hz': polls / elapsed,
              'connected_slots_observed': sorted({r['slot'] for r in calls if r['result'] == 0}),
              'result_counts': {str(code): sum(r['result'] == code for r in calls)
                                for code in sorted({r['result'] for r in calls})},
              'calls': summarize([r['duration_ms'] for r in calls]),
              'by_api': {api: summarize([r['duration_ms'] for r in calls if r['api'] == api])
                         for api in sorted({r['api'] for r in calls})},
              'discovery_batches': summarize([r['duration_ms'] for r in batches]),
              'scope': 'Host API only; no game, device reconnect, or physical button test.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
