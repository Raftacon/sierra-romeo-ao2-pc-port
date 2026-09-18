"""Compare bounded, read-only co-op RNG traces from one campaign preparation.

This diagnoses input-production alignment. Matching random calls alone do not
establish matching gameplay, and a mismatch is never permission to alter a seed.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re


CHECKSUM = re.compile(
    r'checksum RNG probe: produced=(-?\d+), consumed=(-?\d+), scoped=(\d+), '
    r'tls_before=([0-9A-F]+), tls_after=([0-9A-F]+), value=([0-9A-F]+)')
CALLER = re.compile(
    r'RNG caller probe: sequence=(\d+), caller=([0-9A-F]+), produced=(-?\d+), '
    r'consumed=(-?\d+), scoped=(\d+), tls_before=([0-9A-F]+), '
    r'tls_after=([0-9A-F]+), value=([0-9A-F]+)')


def read_trace(path):
    data = path.read_bytes()
    text = data.decode('utf-8', errors='replace')
    if text.count('PC co-op native campaign travel requested:') != 1:
        raise ValueError(f'{path}: require exactly one campaign preparation')
    checksums = defaultdict(list)
    callers = defaultdict(list)
    caller_counts = Counter()
    bad_lcg = []
    sequence = 0

    def record(values):
        produced, consumed, scoped, before, after, value = values
        result = dict(produced=int(produced), consumed=int(consumed),
                      scoped=int(scoped), tls_before=before, tls_after=after,
                      value=value)
        if not result['scoped']:
            expected = (int(before, 16) * 214013 + 2531011) & 0xffffffff
            if int(after, 16) != expected or int(value, 16) != (expected >> 16) & 0x7fff:
                bad_lcg.append(result)
        return result

    for match in CHECKSUM.finditer(text):
        item = record(match.groups())
        frame = item['produced']
        # Original loading can restart the counters within one preparation.
        # Preserve every observation rather than silently replacing frame zero.
        checksums[frame].append(item)
    for match in CALLER.finditer(text):
        seq, caller, *values = match.groups()
        if int(seq) != sequence + 1:
            raise ValueError(f'{path}: incomplete or repeated caller sequence {seq}')
        sequence = int(seq)
        item = record(values)
        item['caller'] = caller
        callers[item['produced']].append(item)
        caller_counts[caller] += 1
    if not checksums:
        raise ValueError(f'{path}: no checksum RNG trace')
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(data).hexdigest(),
                checksum_frames=len(checksums),
                checksum_events=sum(map(len, checksums.values())),
                repeated_checksum_frames={f: len(v) for f, v in checksums.items() if len(v) > 1},
                caller_events=sequence,
                caller_cap_reached=sequence >= 8192, callers=dict(caller_counts),
                invalid_unscoped_lcg_observations=bad_lcg), dict(checksums), dict(callers)


def compare(left, right):
    common = sorted(left.keys() & right.keys())
    different = [frame for frame in common if left[frame] != right[frame]]
    return dict(common_frames=common, host_only_frames=sorted(left.keys() - right.keys()),
                peer_only_frames=sorted(right.keys() - left.keys()), different_frames=different,
                first_difference=(dict(frame=different[0], host=left[different[0]],
                                       peer=right[different[0]]) if different else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True, help='Host runtime.log')
    parser.add_argument('--peer', type=Path, required=True, help='Peer runtime.log')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file to preserve previous evidence')
    host, hc, hr = read_trace(args.host)
    peer, pc, pr = read_trace(args.peer)
    result = dict(host=host, peer=peer, checksum=compare(hc, pc), callers=compare(hr, pr),
                  limitations=['This compares recorded calls, not complete world state.',
                               'Opt-in logging can affect scheduling.',
                               'Missing frames and capped traces are not evidence of agreement.'])
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f"Checksum: {len(result['checksum']['different_frames'])} differing common frames; "
          f"callers: {len(result['callers']['different_frames'])} differing common frames.")


if __name__ == '__main__':
    main()
