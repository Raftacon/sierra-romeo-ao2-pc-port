"""Compare matched HDR impact replays using one shared background alignment.

Reports temporal variation, not a correctness verdict. RGB remains linear HDR;
alpha may encode guest depth and is deliberately excluded from color metrics.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def read_sequence(path):
    report = json.loads((path / 'color.json').read_text())
    if not report.get('complete') or report.get('error'):
        raise ValueError('Require complete color replay')
    _, _, w, h = report['query']['crop']
    frames = []
    for entry in report['frames']:
        data = (path / entry['file']).read_bytes()
        if len(data) != w * h * 8 or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Color data differs from report')
        frame = np.frombuffer(data, '<f2').astype(np.float32).reshape(h, w, 4)[:, :, :3]
        if not np.isfinite(frame).all():
            raise ValueError('Nonfinite color')
        frames.append(frame)
    return report, frames


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('variant', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    a, baseline = read_sequence(args.baseline)
    b, variant = read_sequence(args.variant)
    if (a['capture'] != b['capture'] or a['query']['crop'] != b['query']['crop'] or
            [f['event'] for f in a['frames']] != [f['event'] for f in b['frames']] or
            a['query']['crop'] != [550, 180, 160, 160] or len(baseline) < 2):
        raise ValueError('Require matched courtyard crops and frame events')
    mask = np.ones((160, 160), np.uint8) * 255
    mask[45:, 30:150] = 0
    impact = np.s_[55:155, 35:145]
    wall = np.s_[5:45, 35:145]
    rows, changes, panels = [], [], []
    for i, (base, test) in enumerate(zip(baseline, variant)):
        change = np.abs(base - test)
        changes.append(dict(event=a['frames'][i]['event'], impact_mean=float(change[impact].mean()),
                            wall_mean=float(change[wall].mean()), max=float(change.max()),
                            changed_pixels=int(np.count_nonzero(change.max(axis=2)))))
        if i == 0:
            continue
        warp = np.eye(2, 3, dtype=np.float32)
        correlation, warp = cv2.findTransformECC(
            cv2.cvtColor(baseline[i-1], cv2.COLOR_RGB2GRAY), cv2.cvtColor(base, cv2.COLOR_RGB2GRAY),
            warp, cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-5), mask)
        if correlation < .99 or np.max(np.abs(warp[:, 2])) > 2:
            raise ValueError('Poor background alignment')
        row = dict(frame=i, event=a['frames'][i]['event'], correlation=correlation,
                   translation=warp[:, 2].tolist())
        diffs = []
        for name, sequence in [('baseline', baseline), ('variant', variant)]:
            mapped = cv2.warpAffine(sequence[i], warp, (160, 160),
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            diff = np.abs(sequence[i-1] - mapped)
            row[name] = dict(impact_mean=float(diff[impact].mean()), wall_mean=float(diff[wall].mean()))
            diffs.append(diff)
        rows.append(row)
        panels.append([baseline[i-1], base, diffs[0]*8, variant[i-1], test, diffs[1]*8])
    result = dict(baseline=str(args.baseline), variant=str(args.variant),
                  note='Shared baseline wall translation; linear RGB; variation is not defect proof.',
                  pairs=rows, changes=changes)
    result['summary'] = {}
    for name in ('baseline', 'variant'):
        result['summary'][name] = {key: float(np.mean([r[name][key] for r in rows]))
                                   for key in ('impact_mean', 'wall_mean')}
        result['summary'][name]['worst_impact'] = max(r[name]['impact_mean'] for r in rows)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    chosen = sorted(range(len(rows)), key=lambda i: rows[i]['baseline']['impact_mean'], reverse=True)[:4]
    sheet = Image.new('RGB', (960, 180*len(chosen)))
    draw = ImageDraw.Draw(sheet)
    for row_index, index in enumerate(chosen):
        draw.text((0, row_index*180), 'frame %d: original prev/current/delta x8 | variant prev/current/delta x8' % rows[index]['frame'])
        for column, frame in enumerate(panels[index]):
            # Fixed linear-to-display transform for inspection only; metrics use original HDR.
            rgb = (np.clip(frame, 0, 1)**(1/2.2)*255).astype(np.uint8)
            sheet.paste(Image.fromarray(rgb), (column*160, row_index*180+20))
    sheet.save(args.output/'largest-pairs.png')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
