"""Inspect one completed optional-GPU draw capture; metrics do not prove pixel correctness."""
import argparse
import csv
from collections import Counter
import json
import math
from pathlib import Path
import struct


def texture(row):
    w = [int(row[f'word{i}']) for i in range(6)]
    dimension = (w[5] >> 9) & 3
    return {'slot': int(row['slot']), 'words': w, 'type': w[0] & 3,
            'dimension': dimension, 'format': w[1] & 63,
            'base_address': f'{w[1] & 0xFFFFF000:08X}',
            'pitch_pixels': ((w[0] >> 22) & 511) * 32,
            'width': (w[2] & 8191) + 1 if dimension == 1 else None,
            'height': ((w[2] >> 13) & 8191) + 1 if dimension == 1 else None,
            'clamp_x': (w[0] >> 10) & 7, 'clamp_y': (w[0] >> 13) & 7}


def finite(values):
    return [x if math.isfinite(x) else None for x in values]


def analyze(path):
    rows = list(csv.DictReader(path.open()))
    if not rows or len({r['gpu_frame'] for r in rows}) != 1:
        raise ValueError('Expected exactly one nonempty GPU frame')
    if [int(r['draw']) for r in rows] != list(range(len(rows))):
        raise ValueError('Draw indices must be contiguous from zero')
    textures = [[] for _ in rows]
    pairs = set()
    for row in csv.DictReader(Path(str(path) + '.fetch.csv').open()):
        index, slot = int(row['draw']), int(row['slot'])
        if not 0 <= index < len(rows) or not 0 <= slot < 32 or row['gpu_frame'] != rows[0]['gpu_frame']:
            raise ValueError('Fetch refers to an invalid draw/frame/slot')
        if (index, slot) in pairs or not (int(rows[index]['texture_mask']) & (1 << slot)):
            raise ValueError('Duplicate or unexpected fetch slot')
        pairs.add((index, slot))
        textures[index].append(texture(row))
    for i, row in enumerate(rows):
        if len(textures[i]) != int(row['texture_mask']).bit_count():
            raise ValueError('Missing used texture fetch')
    constant_path = Path(str(path) + '.constants.bin')
    constants = constant_path.read_bytes() if constant_path.exists() else None
    if constants is not None and len(constants) != len(rows) * 8192:
        raise ValueError('Incomplete float-constant capture')
    quads = {}
    quad_path = Path(str(path) + '.quads.csv')
    if quad_path.exists():
        for row in csv.DictReader(quad_path.open()):
            index = int(row['draw'])
            if not 0 <= index < len(rows) or index in quads or row['gpu_frame'] != rows[0]['gpu_frame']:
                raise ValueError('Invalid or duplicate quad row')
            raw = bytes.fromhex(row['bytes_hex'])
            ok = int(row['cpu_read_ok'])
            if len(raw) != (128 if ok else 0):
                raise ValueError('Invalid quad snapshot size')
            endian = int(row['fetch95_word1']) & 3
            order = [(0, 1, 2, 3), (1, 0, 3, 2), (3, 2, 1, 0), (2, 3, 0, 1)][endian]
            swapped = bytes(raw[b + j] for b in range(0, len(raw), 4) for j in order)
            quads[index] = {'fetch95_word0': int(row['fetch95_word0']),
                            'fetch95_word1': int(row['fetch95_word1']), 'cpu_read_ok': bool(ok),
                            'vertices': [finite(struct.unpack_from('<8f', swapped, offset))
                                         for offset in range(0, len(swapped), 32)]}
    result = {'gpu_frame': int(rows[0]['gpu_frame']), 'draw_count': len(rows),
              'texture_fetch_count': len(pairs), 'has_float_constants': constants is not None,
              'viewport_cuts_scissor': [], 'postprocess_draws': [],
              'interpretation': 'Rasterizer state and CPU-side snapshots only; no proof of GPU texture contents or pixel parity.'}
    if 'depth_control' in rows[0]:
        alpha = Counter()
        depth = Counter()
        biased = []
        for row in rows:
            cc = int(row['color_control'])
            dc = int(row['normalized_depth_control'])
            alpha['disabled' if not cc & 8 else str(cc & 7)] += 1
            depth['disabled' if not dc & 2 else str((dc >> 4) & 7)] += 1
            if int(row['su_mode']) & (7 << 11):
                biased.append({key: row[key] for key in (
                    'draw', 'vs_hash', 'ps_hash', 'su_mode', 'normalized_depth_control',
                    'depth_info', 'front_scale', 'front_offset', 'back_scale', 'back_offset',
                    'viewport_z_min', 'viewport_z_max', 'viewport_w', 'viewport_h')})
        result['raster_summary'] = {
            'alpha_compare_counts': dict(alpha), 'depth_compare_counts': dict(depth),
            'comparison_encoding': '0 never, 1 less, 2 equal, 3 less_equal, 4 greater, 5 not_equal, 6 greater_equal, 7 always',
            'polygon_offset_enabled_draws': biased,
            'interpretation': 'Counts describe captured draws, not pixel contribution. Offset enable does not prove effective nonzero host bias.'}
    for i, row in enumerate(rows):
        vp = [int(row[k]) for k in ('viewport_x', 'viewport_y', 'viewport_w', 'viewport_h')]
        sc = [int(row[k]) for k in ('scissor_x', 'scissor_y', 'scissor_w', 'scissor_h')]
        cuts = [axis for axis in range(2) if sc[axis + 2] and
                (vp[axis] > sc[axis] or vp[axis] + vp[axis + 2] < sc[axis] + sc[axis + 2])]
        if cuts:
            result['viewport_cuts_scissor'].append({'draw': i, 'axes': cuts, 'viewport': vp, 'scissor': sc})
        if row['vs_hash'].lower() not in ('da84b19697871cb2', '267a109391bf21ba', '124fe38d12a00254'):
            continue
        entry = {'draw': i, 'vs_hash': row['vs_hash'], 'ps_hash': row['ps_hash'], 'viewport': vp, 'scissor': sc,
                 'textures': textures[i], 'quad': quads.get(i)}
        if constants is not None:
            def vector(register):
                return finite(struct.unpack_from('<4f', constants, i * 8192 + register * 16))
            entry['vs_c0'] = vector(0)
            entry['vs_constants'] = {str(n): vector(n) for n in range(6)}
            entry['ps_constants'] = {str(n): vector(256 + n) for n in [*range(16), 255]}
        result['postprocess_draws'].append(entry)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.capture)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('postprocess_draws', 'viewport_cuts_scissor', 'raster_summary')}, indent=2))
    print(f"Viewport/scissor conflicts: {len(result['viewport_cuts_scissor'])}; post-process draws: {len(result['postprocess_draws'])}")


if __name__ == '__main__':
    main()
