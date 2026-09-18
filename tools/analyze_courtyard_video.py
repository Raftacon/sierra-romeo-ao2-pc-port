"""Inspect the known courtyard crate in a closed, timestamped window recording.

Aligned temporal differences are descriptive, not a campaign-parity verdict.
This fixture requires the recorded 1936x1119 window and the existing route.
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
    p = args.probe / 'motion'
    if (p / 'crate-motion.json').exists():
        raise ValueError('Require new analysis output')
    native = json.loads((args.probe / 'probe.json').read_text())
    scenario = json.loads((args.probe / 'courtyard-decal-scenario.json').read_text())
    video = json.loads((p / 'result.json').read_text())
    if (native['timed_out'] or native['exit_code_before_cleanup'] != 0 or
            scenario.get('route_error') or len(scenario['events']) != 6 or
            video['timed_out'] or video['exit_code'] != 0):
        raise ValueError('Require completed route and closed recording')
    stamps = [json.loads(s) for s in (p / 'frames.jsonl').read_text().splitlines()]
    wanted = {min(stamps, key=lambda s: abs(s['seconds'] - t))['index']
              for t in np.arange(5.75, 10.51, .25)}
    cap = cv2.VideoCapture(str(p / 'game-window.mkv'))
    canvas = Image.new('RGB', (180 * 5, 270 * 4))
    draw = ImageDraw.Draw(canvas)
    mask = np.zeros((250, 280), np.uint8)
    mask[20:195, 80:170] = 255  # wall retained inside the crop through the sweep
    template = previous = None
    warp = np.eye(2, 3, dtype=np.float32)
    rows, selected = [], []
    try:
        for stamp in stamps:
            ok, bgr = cap.read()
            if not ok or bgr.shape != (1119, 1936, 3):
                raise ValueError('Missing frame or unexpected window layout')
            rgb = cv2.cvtColor(bgr[570:820, 1650:1930], cv2.COLOR_BGR2RGB)
            if stamp['index'] in wanted:
                i = len(selected)
                canvas.paste(Image.fromarray(rgb[:, 100:]), (i % 5 * 180, i // 5 * 270 + 20))
                draw.text((i % 5 * 180 + 4, i // 5 * 270 + 4),
                          f"{stamp['seconds']:.3f}s / {stamp['index']}", fill='white')
                selected.append({'index': stamp['index'], 'seconds': stamp['seconds']})
            if not 5.5 <= stamp['seconds'] <= 10.75:
                continue
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
            if template is None:
                template = gray.copy()
            correlation, warp = cv2.findTransformECC(template, gray,
                warp.copy(), cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-6), mask)
            if (correlation < .98 or np.abs(warp[:, 2]).max() > 100 or
                    np.abs(warp[:, :2] - np.eye(2)).max() > .1):
                raise ValueError(f'Unreliable wall alignment at {stamp["index"]}: '
                                 f'correlation={correlation}, translation={warp[:, 2]}')
            corners = np.array([[[80, 20], [170, 20], [80, 195], [170, 195],
                                 [243, 130], [263, 130], [243, 215], [263, 215]]], np.float32)
            mapped = cv2.transform(corners, warp)[0]
            if (mapped.min(0) < 2).any() or (mapped.max(0) > [277, 247]).any():
                raise ValueError('Analysis region left the captured image')
            aligned = cv2.warpAffine(rgb.astype(np.float32), warp, (280, 250),
                                    flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            if previous is not None:
                delta = np.abs(aligned - previous)
                rows.append({'index': stamp['index'], 'seconds': stamp['seconds'],
                    'correlation': correlation, 'affine': warp.tolist(),
                    'crate_mean_abs_rgb': float(delta[130:215, 243:263].mean()),
                    'wall_mean_abs_rgb': float(delta[35:185, 85:160].mean())})
            previous = aligned
        if cap.read()[0] or not rows or len(selected) != 20:
            raise ValueError('Recording/timestamp mismatch or missing samples')
    finally:
        cap.release()
    report = {'scope': __doc__, 'crop_xyxy': [1650, 570, 1930, 820],
              'crate_roi_xyxy': [243, 130, 263, 215], 'selected': selected, 'pairs': rows,
              'timestamps_sha256': hashlib.sha256((p / 'frames.jsonl').read_bytes()).hexdigest()}
    for name in ('crate', 'wall'):
        values = [r[name + '_mean_abs_rgb'] for r in rows]
        report[name] = {'mean': float(np.mean(values)), 'worst_pair': max(values)}
    canvas.save(p / 'crate-motion.png')
    (p / 'crate-motion.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('crate', 'wall')}, indent=2))


if __name__ == '__main__':
    main()
