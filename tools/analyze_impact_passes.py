"""Measure matched wall crops across passes, using a shared baseline alignment.

Different color encodings are reported separately. Variation is not a fidelity
score; depth/alpha history must determine why individual pixels change.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--alignment', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = json.loads((args.source/'passes.json').read_text())
    alignment = json.loads(args.alignment.read_text())['pairs']
    if not report.get('complete') or report.get('error') or report['query']['crop'] != [550, 180, 160, 160]:
        raise ValueError('Require complete courtyard pass capture')
    frames = report['frames']
    if len(alignment) != len(frames)-1 or [a['event'] for a in alignment] != [f['stages'][0]['event'] for f in frames[1:]]:
        raise ValueError('Alignment refers to different frames')
    names = [s['stage'] for s in frames[0]['stages']]
    sequences = {name: [] for name in names}
    formats = {}
    for frame in frames:
        if [s['stage'] for s in frame['stages']] != names:
            raise ValueError('Inconsistent stage ordering')
        for s in frame['stages']:
            data = (args.source/s['file']).read_bytes()
            if hashlib.sha256(data).hexdigest() != s['sha256']:
                raise ValueError('Crop hash mismatch')
            if formats.setdefault(s['stage'], s['format']) != s['format']:
                raise ValueError('Stage color format changed')
            dtype = {'R16G16B16A16_FLOAT': '<f2', 'R8G8B8A8_UNORM': 'u1'}[s['format']]
            a = np.frombuffer(data, dtype).astype(np.float32).reshape(160, 160, 4)[:, :, :3]
            if dtype == 'u1':
                a /= 255
            if not np.isfinite(a).all():
                raise ValueError('Nonfinite RGB')
            sequences[s['stage']].append(a)
    result = {'capture': report['capture'], 'alignment': str(args.alignment),
              'note': __doc__, 'stages': {}, 'rgb_equal_to_copy0': {}}
    for name, sequence in sequences.items():
        rows = []
        for i in range(1, len(sequence)):
            warp = np.eye(2, 3, dtype=np.float32)
            warp[:, 2] = alignment[i-1]['translation']
            mapped = cv2.warpAffine(sequence[i], warp, (160, 160),
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            diff = np.abs(sequence[i-1] - mapped)
            rows.append({'frame': frames[i]['frame'], 'impact_mean': float(diff[55:155, 35:145].mean()),
                         'wall_mean': float(diff[5:45, 35:145].mean())})
        result['stages'][name] = {'format': formats[name], 'pairs': rows,
            'average_impact': float(np.mean([r['impact_mean'] for r in rows])),
            'average_wall': float(np.mean([r['wall_mean'] for r in rows])),
            'worst_impact': max(r['impact_mean'] for r in rows)}
        if name in ('copy1', 'copy2', 'hdr_final'):
            result['rgb_equal_to_copy0'][name] = all(np.array_equal(a, b) for a, b in zip(sequence, sequences['copy0']))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'analysis.json').write_text(json.dumps(result, indent=2)+'\n')
    i = 1 + max(range(len(alignment)), key=lambda i: result['stages']['impacts']['pairs'][i]['impact_mean'])
    sheet = Image.new('RGB', (len(names)*160, 3*180))
    draw = ImageDraw.Draw(sheet)
    for col, name in enumerate(names):
        a, b = sequences[name][i-1:i+1]
        for row, (label, data) in enumerate([(str(i-1), a), (str(i), b), ('difference x8', abs(a-b)*8)]):
            draw.text((col*160, row*180), '%s / %s' % (name, label))
            sheet.paste(Image.fromarray((np.clip(data, 0, 1)*255).astype(np.uint8)), (col*160, row*180+20))
    sheet.save(args.output/'pass-comparison.png')
    print(json.dumps({'stages': {k: {key: v[key] for key in ('average_impact', 'average_wall', 'worst_impact')} for k, v in result['stages'].items()},
                      'rgb_equal_to_copy0': result['rgb_equal_to_copy0']}, indent=2))


if __name__ == '__main__':
    main()
