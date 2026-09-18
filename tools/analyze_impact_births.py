"""Match sampled courtyard impact geometry across a captured firing burst.

This checks ordering in one identified material pass, not per-pixel visibility.
The oldest quad anchors a camera homography; ordered vertex positions identify
the other persistent impacts independently of their draw indices.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('probe', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    r = json.loads((args.probe/'geometry-births-verified.json').read_text())
    if r.get('error') or len(r['draws']) != len(r['query']['events']):
        raise ValueError('Require complete verified geometry')
    draws = {d['event']: d for d in r['draws']}
    groups = json.loads((args.probe/'birth-frame-groups.json').read_text())
    previous, rows, excluded, frames = None, [], [], []
    used = set()
    for group in groups:
        events = [e for e in group['events'] if e in draws]
        omitted = [e for e in group['events'] if e not in draws]
        if omitted != group['events'][:1]:
            raise ValueError('Unexpected unverified material candidates')
        excluded.extend(omitted)
        used.update(events)
        frames.append({'frame': group['frame'], 'impacts': len(events)})
        if not events:
            continue
        vertices = [np.array([draws[e]['positions'][k] for k in sorted(draws[e]['positions'], key=int)],
                             np.float32)[:, :2].copy() for e in events]
        if not all(np.isfinite(v).all() and len(v) in (4, 8) for v in vertices):
            raise ValueError('Unexpected impact geometry')
        if previous:
            old, old_frame = previous
            anchor = old[0]
            if len(anchor) != 4:
                raise ValueError('Require quad anchor')
            shape = anchor - anchor.mean(axis=0)
            costs = [(float(np.sqrt(np.mean(((v-v.mean(axis=0))-shape)**2))), i)
                     for i, v in enumerate(vertices) if v.shape == shape.shape]
            costs.sort()
            if not costs or costs[0][0] > 1 or (len(costs) > 1 and costs[1][0]-costs[0][0] < 1):
                raise ValueError('Ambiguous anchor shape')
            anchor_index = costs[0][1]
            matrix = cv2.getPerspectiveTransform(anchor, vertices[anchor_index])
            matches, residuals, margins = [], [], []
            for source in old:
                mapped = cv2.perspectiveTransform(source[None], matrix)[0]
                costs = [(float(np.sqrt(np.mean((mapped-target)**2))), i)
                         for i, target in enumerate(vertices) if target.shape == mapped.shape]
                costs.sort()
                if not costs or costs[0][0] > .05 or (len(costs) > 1 and costs[1][0]-costs[0][0] < 1):
                    raise ValueError('Ambiguous persistent impact match')
                residuals.append(costs[0][0])
                matches.append(costs[0][1])
                if len(costs) > 1:
                    margins.append(costs[1][0]-costs[0][0])
            if len(set(matches)) != len(matches):
                raise ValueError('Persistent impacts matched the same draw')
            rows.append(dict(previous_frame=old_frame, frame=group['frame'], anchor_index=anchor_index,
                persistent_draw_indices=matches, order_preserved=matches == sorted(matches),
                appended_only=matches == list(range(len(old))), max_vertex_rms_px=max(residuals),
                minimum_alternative_margin_px=min(margins) if margins else None,
                homography=matrix.tolist()))
        previous = vertices, group['frame']
    if used != set(draws) or not rows:
        raise ValueError('Geometry not fully accounted for')
    result = dict(scope=__doc__, capture=r['capture'], frames=frames, pairs=rows,
                  excluded_other_material_candidates=excluded,
                  all_appended_only=all(row['appended_only'] for row in rows),
                  max_vertex_rms_px=max(row['max_vertex_rms_px'] for row in rows))
    with args.output.open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    print(json.dumps({k: result[k] for k in ('frames', 'all_appended_only', 'max_vertex_rms_px')}, indent=2))


if __name__ == '__main__':
    main()
