"""Locate raw RGB changes between equal-count impact epochs for GPU inspection.

Candidate differences do not diagnose flicker: lighting, sampling and material
coverage can all change color. This deliberately retains actual pixel locations.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    args = parser.parse_args()
    p = args.probe
    color = json.loads((p / 'aligned-impact-color/color.json').read_text())
    births = json.loads((p / 'aligned-impact-births.json').read_text())
    geometry = json.loads((p / 'impact-creation-geometry.json').read_text())
    if not color.get('complete') or color.get('error') or color['capture'] != births['capture']:
        raise ValueError('Require matching complete color and geometry')
    epochs = {v['epoch']: v for v in births['epochs'] if v['events']}
    draws = {v['event']: v for v in geometry['draws']}
    x, y, w, h = color['query']['crop']
    frames = {}
    canvas = Image.new('RGB', (w * 6, (h + 20) * 9))
    text = ImageDraw.Draw(canvas)
    for i, (epoch, group) in enumerate(epochs.items()):
        record = color['frames'][i]
        if record['event'] != group['events'][-1]:
            raise ValueError('Unexpected color event ordering')
        raw = (p / 'aligned-impact-color' / record['file']).read_bytes()
        if len(raw) != w*h*8 or hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('Changed color readback')
        rgb = np.frombuffer(raw, '<f2').astype(np.float32).reshape(h, w, 4)[:, :, :3]
        if not np.isfinite(rgb).all():
            raise ValueError('Nonfinite color')
        frames[epoch] = rgb
        # Diagnostic tonemap, held constant for every tile.
        positive = np.maximum(rgb, 0)
        tile = np.clip((positive/(1+positive))**(1/2.2)*255, 0, 255).astype(np.uint8)
        u, v = i % 6 * w, i // 6 * (h + 20)
        canvas.paste(Image.fromarray(tile), (u, v + 20))
        text.text((u+4, v+4), f"Epoch {epoch}: {len(group['events'])} impacts", fill='white')
    candidates = []
    for pair in births['pairs']:
        a, b = pair['previous_epoch'], pair['epoch']
        if len(epochs[a]['events']) != len(epochs[b]['events']):
            continue
        mask = np.zeros((h, w), np.uint8)
        for event in epochs[a]['events']:
            vertices = np.array(list(draws[event]['positions'].values()), np.float32)[:, :2] - [x, y]
            cv2.fillConvexPoly(mask, cv2.convexHull(vertices.astype(np.int32)), 255)
        mask = cv2.erode(mask, np.ones((7, 7), np.uint8))
        delta = np.abs(frames[b]-frames[a]).mean(2)
        delta[mask == 0] = 0
        matrix = np.array(pair['homography'])
        for _ in range(3):
            iy, ix = np.unravel_index(delta.argmax(), delta.shape)
            if delta[iy, ix] < .05:
                break
            position = np.array([x+ix+.5, y+iy+.5, 1])
            mapped = matrix @ position
            movement = np.linalg.norm(mapped[:2]/mapped[2]-position[:2])
            candidates.append({'previous_epoch': a, 'epoch': b, 'pixel': [int(x+ix), int(y+iy)],
                'mean_abs_rgb': float(delta[iy, ix]), 'geometry_motion_px': float(movement),
                'previous_color': frames[a][iy, ix].tolist(), 'color': frames[b][iy, ix].tolist(),
                'first_event': epochs[a]['start_event'], 'event': epochs[b]['events'][-1]})
            delta[max(0, iy-7):iy+8, max(0, ix-7):ix+8] = 0
    candidates.sort(key=lambda c: c['mean_abs_rgb'], reverse=True)
    report = {'scope': __doc__, 'candidates': candidates,
              'low_motion_candidates': [c for c in candidates if c['geometry_motion_px'] < .25]}
    with (p / 'aligned-impact-color-changes.json').open('x') as f:
        json.dump(report, f, indent=2)
    canvas.save(p / 'aligned-impact-contact.png')
    print(json.dumps(report['low_motion_candidates'][:6], indent=2))


if __name__ == '__main__':
    main()
