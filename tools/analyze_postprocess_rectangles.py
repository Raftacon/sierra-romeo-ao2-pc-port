"""Match observed retail rectangle arguments to captured CPU-side GPU quads."""
import argparse
import csv
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rectangles', type=Path)
    parser.add_argument('draw_analysis', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = list(csv.DictReader(args.rectangles.open()))
    if not rows:
        parser.error('Empty rectangle capture')
    groups = {}
    keys = ['caller', *[f'f{i}' for i in range(1, 9)], 'target_w', 'target_h', 'source_w', 'source_h',
            'scene_w', 'scene_h', 'downsample', 'filter_w', 'filter_h']
    for row in rows:
        key = tuple(row[k] for k in keys)
        if key not in groups:
            groups[key] = {'caller': row['caller'], 'count': 0,
                           'rectangle': [float(row[f'f{i}']) for i in range(1, 9)],
                           'dimensions': {k: int(row[k]) for k in keys[9:]}}
        groups[key]['count'] += 1
    variants = list(groups.values())
    for entry in variants:
        x, y, width, height, u, v, uw, vh = entry['rectangle']
        d = entry['dimensions']
        if not all(math.isfinite(n) for n in entry['rectangle']) or any(d[k] <= 0 for k in ('target_w', 'target_h', 'source_w', 'source_h')):
            parser.error('Invalid rectangle dimensions/coordinates')
        entry['predicted_quad'] = [[2 * (px - .5) / d['target_w'] - 1,
                                    1 - 2 * (py - .5) / d['target_h'],
                                    pu / d['source_w'], pv / d['source_h']]
                                   for px, py, pu, pv in ((x, y, u, v), (x + width, y, u + uw, v),
                                                          (x, y + height, u, v + vh), (x + width, y + height, u + uw, v + vh))]
    draw_data = json.loads(args.draw_analysis.read_text())
    matches = []
    for draw in draw_data['postprocess_draws']:
        quad = draw.get('quad')
        if not quad or not quad['cpu_read_ok']:
            continue
        actual = [[vertex[j] for j in (0, 1, 4, 5)] for vertex in quad['vertices']]
        if len(actual) != 4 or any(v is None or not math.isfinite(v) for row in actual for v in row):
            parser.error('Invalid GPU quad')
        candidates = []
        for index, variant in enumerate(variants):
            error = max(abs(a - b) for row_a, row_b in zip(actual, variant['predicted_quad']) for a, b in zip(row_a, row_b))
            if error <= 0.000002:
                candidates.append({'variant': index, 'caller': variant['caller'], 'max_error': error})
        matches.append({'draw': draw['draw'], 'vs_hash': draw['vs_hash'], 'ps_hash': draw['ps_hash'], 'candidates': candidates})
    result = {'rectangle_records': len(rows), 'variants': variants, 'matches': matches,
              'interpretation': 'Coordinate agreement between separate bounded captures. A unique candidate supports the caller mapping; ambiguous/no matches do not identify a caller. This is not pixel parity.'}
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'rectangle_records': len(rows), 'variant_count': len(variants), 'matches': matches}, indent=2))


if __name__ == '__main__':
    main()
