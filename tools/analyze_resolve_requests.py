"""Decode bounded copy-request observations from a normally closed draw capture.

Register layouts follow pinned ReXGlue registers.h / xenos.h. This reports guest
requests, not completed GPU copies. Rectangle vertices are CPU observations;
scissor, rounding and pitch still affect the renderer's final copy extent.
"""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import struct


def vertex_floats(raw, endian):
    order = ((0, 1, 2, 3), (1, 0, 3, 2), (3, 2, 1, 0), (2, 3, 0, 1))[endian]
    swapped = bytes(raw[i + j] for i in range(0, 24, 4) for j in order)
    values = struct.unpack('<6f', swapped)
    return list(values) if all(math.isfinite(v) for v in values) else None


def packet_observations(path, frame, draw_count):
    with path.open() as f: rows = list(csv.DictReader(f))
    if not 0 < len(rows) < 8192: raise ValueError('Empty or truncated copy-packet observations')
    result = []; previous = 0
    for i, raw in enumerate(rows):
        r = {k: int(v) for k, v in raw.items()}
        after = r['after_draw_count']
        if r['capture_frame'] != frame or r['observation'] != i or not previous <= after <= draw_count:
            raise ValueError('Invalid copy-packet observation sequence')
        previous = after
        opcode = (r['packet'] >> 8) & 127
        is_copy = opcode in (0x22, 0x36) and r['mode'] == 6
        bin_skipped = bool(r['packet'] & 1) and not bool(r['bin_select'] & r['bin_mask'])
        if not (is_copy or opcode in (0x37, 0x3F) and bin_skipped):
            raise ValueError('Unexpected packet observation')
        result.append(dict(r, opcode=hex(opcode), destination=hex(r['destination'] & 0x1FFFFFFF),
            copy_draw=is_copy, bin_skipped=bin_skipped,
            visibility_kill=bool(r['viz_query'] & 1 and r['viz_query'] & 128),
            at_end_boundary=after == draw_count))
    return {'observations': result,
        'copy_draws': sum(r['copy_draw'] for r in result),
        'bin_skipped_copies': sum(r['copy_draw'] and r['bin_skipped'] for r in result),
        'visibility_killed_copies': sum(r['copy_draw'] and not r['bin_skipped'] and r['visibility_kill'] for r in result),
        'skipped_indirect_buffers': sum(not r['copy_draw'] for r in result),
        'limits': 'Before bin predication; visibility state is observed before draw dispatch. '
                  'The window ends at the next recorded Draw, so end-boundary packets may belong to the next GPU frame. '
                  'Skipped indirect buffers are not traversed. Successful packet dispatch is not proof of a successful GPU copy.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args(); root = args.probe
    native = json.loads((root / 'probe.json').read_text())
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native capture')
    with (root / 'draws.csv').open() as f: draws = list(csv.DictReader(f))
    with (root / 'draws.csv.resolves.csv').open() as f: rows = list(csv.DictReader(f))
    if not 0 < len(draws) < 8192 or not 0 < len(rows) < 8192:
        raise ValueError('Empty or potentially truncated capture')
    frame = int(draws[0]['gpu_frame'])
    if any(int(r['gpu_frame']) != frame or int(r['draw']) != i for i, r in enumerate(draws)):
        raise ValueError('Invalid draw sequence')
    requests = []; previous = 0
    for i, r in enumerate(rows):
        after = int(r['after_draw_count'])
        if int(r['gpu_frame']) != frame or int(r['resolve']) != i or not previous <= after <= len(draws):
            raise ValueError('Invalid resolve sequence')
        previous = after
        control, info, pitch = (int(r[k]) for k in ('copy_control', 'copy_dest_info', 'copy_dest_pitch'))
        vertices = None
        if int(r['cpu_read_ok']):
            raw = bytes.fromhex(r['vertices_hex'])
            if len(raw) != 24: raise ValueError('Invalid CPU vertex snapshot')
            vertices = vertex_floats(raw, int(r['fetch0_word1']) & 3)
        bias = (info >> 16) & 63
        requests.append({'resolve': i, 'after_draw_count': after,
            'destination': hex(int(r['copy_dest_base']) & 0x1FFFFFFF),
            'source_select': control & 7, 'sample_select': (control >> 4) & 7,
            'command': (control >> 20) & 3, 'clear_color': bool(control & 256),
            'clear_depth': bool(control & 512), 'destination_format': (info >> 7) & 63,
            'destination_endian': info & 7, 'destination_exponent_bias': bias - 64 if bias & 32 else bias,
            'destination_pitch': pitch & 16383, 'destination_height': (pitch >> 16) & 16383,
            'surface_pitch': int(r['surface_info']) & 16383,
            'cpu_vertices': vertices, 'raw': r})
    counts = Counter((r['destination'], r['source_select']) for r in requests)
    report = {'complete': True, 'gpu_frame': frame, 'draws': len(draws), 'requests': requests,
        'destinations': [{'address': k[0], 'source_select': k[1], 'requests': n} for k, n in counts.items()],
        'cpu_vertex_read_failures': sum(not int(r['cpu_read_ok']) for r in rows),
        'nonfinite_vertex_snapshots': sum(bool(int(r['cpu_read_ok'])) and q['cpu_vertices'] is None for r, q in zip(rows, requests)),
        'executable_sha256': native['executable_sha256'], 'gpu_plugin': native['gpu_plugin'],
        'limits': __doc__}
    packet_path = root / 'draws.csv.copy-packets.csv'
    if packet_path.exists():
        report['packets'] = packet_observations(packet_path, frame, len(draws))
    (root / 'resolve-requests.json').write_text(json.dumps(report, indent=2) + '\n')
    summary = {k: v for k, v in report.items() if k not in ('requests', 'packets')}
    if 'packets' in report:
        summary['packets'] = {k: v for k, v in report['packets'].items() if k != 'observations'}
    print(json.dumps(summary, indent=2))


if __name__ == '__main__': main()
