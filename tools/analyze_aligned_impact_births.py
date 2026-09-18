"""Track verified impacts within the 60 main-HDR epochs of the aligned capture.

This proves sampled geometric correspondence/order, not depth or alpha coverage.
Epoch boundaries are the recurring nonindexed 12-vertex HDR initialization draws.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    p = args.probe
    output = p / 'aligned-impact-births.json'
    if output.exists():
        raise ValueError('Require new output')
    geometry_path = p / 'impact-creation-geometry.json'
    geometry = json.loads(geometry_path.read_text())
    inventory_path = p / 'gpu-frame_capture.rdc-inventory.json'
    inventory = json.loads(inventory_path.read_text())
    if (geometry.get('error') or not inventory.get('complete') or inventory.get('error') or
            geometry['capture'] != inventory['capture'] or len(geometry['draws']) != 309):
        raise ValueError('Require complete matching capture and geometry')
    resource = geometry['query']['resource']
    boundaries = [a['event'] for a in inventory['actions'] if a['outputs'][0] == resource and
                  'Drawcall' in a['flags'] and 'Indexed' not in a['flags'] and
                  a['indices'] == 12 and a['depth'] == 'ResourceId::0']
    if len(boundaries) != 60 or boundaries != sorted(set(boundaries)):
        raise ValueError('Unexpected main-HDR epoch structure')
    groups = [[] for _ in boundaries]
    for d in geometry['draws']:
        index = bisect.bisect_right(boundaries, d['event']) - 1
        if index < 0:
            raise ValueError('Impact precedes first HDR epoch')
        groups[index].append(d)
    previous, pairs = None, []
    for index, draws in enumerate(groups):
        if not draws:
            if previous is not None:
                raise ValueError('Impacts disappear for an entire epoch')
            continue
        vertices = [np.array([d['positions'][k] for k in sorted(d['positions'], key=int)],
                             np.float32)[:, :2].copy() for d in draws]
        if not all(np.isfinite(v).all() and v.shape in ((4, 2), (8, 2)) for v in vertices):
            raise ValueError('Require one or two quads per impact')
        if previous:
            old_index, old = previous
            if old[0].shape != (4, 2):
                raise ValueError('Require quad anchor')
            shape = old[0] - old[0].mean(0)
            costs = sorted((float(np.sqrt(np.mean(((v-v.mean(0))-shape)**2))), i)
                           for i, v in enumerate(vertices) if v.shape == shape.shape)
            if costs[0][0] > 1 or (len(costs) > 1 and costs[1][0]-costs[0][0] < 1):
                raise ValueError('Ambiguous anchor shape')
            matrix = cv2.getPerspectiveTransform(old[0], vertices[costs[0][1]])
            matches, residuals = [], []
            for source in old:
                mapped = cv2.perspectiveTransform(source[None], matrix)[0]
                scores = sorted((float(np.sqrt(np.mean((mapped-v)**2))), i)
                                for i, v in enumerate(vertices) if v.shape == mapped.shape)
                if scores[0][0] > .05 or (len(scores) > 1 and scores[1][0]-scores[0][0] < 1):
                    raise ValueError('Ambiguous persistent impact')
                residuals.append(scores[0][0])
                matches.append(scores[0][1])
            if len(set(matches)) != len(matches):
                raise ValueError('Duplicate persistent match')
            pairs.append({'previous_epoch': old_index, 'epoch': index,
                          'persistent_draw_indices': matches,
                          'appended_only': matches == list(range(len(old))),
                          'maximum_vertex_rms_px': max(residuals), 'homography': matrix.tolist()})
        previous = index, vertices
    if len(pairs) != 50:
        raise ValueError('Expected 51 impact-containing epochs')
    result = {'scope': __doc__, 'capture': geometry['capture'],
              'geometry_sha256': hashlib.sha256(geometry_path.read_bytes()).hexdigest(),
              'inventory_sha256': hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
              'epochs': [{'epoch': i, 'start_event': b, 'events': [d['event'] for d in ds]}
                         for i, (b, ds) in enumerate(zip(boundaries, groups))],
              'pairs': pairs, 'all_appended_only': all(v['appended_only'] for v in pairs),
              'maximum_vertex_rms_px': max(v['maximum_vertex_rms_px'] for v in pairs)}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('all_appended_only', 'maximum_vertex_rms_px')}))


if __name__ == '__main__':
    main()
