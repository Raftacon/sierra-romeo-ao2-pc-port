"""Compare the captured crate projection experiment, not campaign correctness."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw
from compare_impact_color import read_sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('variant', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--paired', action='store_true', help='Read the coordinated depth/color/modulation replay')
    args = parser.parse_args()
    a, baseline = read_sequence(args.baseline)
    if args.paired:
        replay = json.loads((args.variant/'replay.json').read_text())
        if not replay.get('complete') or replay.get('error') or not replay.get('restoration_exact'):
            raise ValueError('Require completed, restored paired replay')
        run = next(r for r in replay['runs'] if r['mode']=='precise')
        b = {'capture': replay['capture'], 'query': {'crop': replay['roi']},
             'restoration_exact': True, 'frames': run['readbacks']}
        variant = []
        for sample in run['readbacks']:
            raw = (args.variant/('precise-%d.bin' % sample['event'])).read_bytes()
            if len(raw)!=140*200*8 or hashlib.sha256(raw).hexdigest()!=sample['sha256']:
                raise ValueError('Changed paired readback')
            rgb = np.frombuffer(raw, '<f2').astype(np.float32).reshape(200,140,4)[:,:,:3]
            if not np.isfinite(rgb).all(): raise ValueError('Nonfinite paired RGB')
            variant.append(rgb)
    else:
        b, variant = read_sequence(args.variant)
    if (a['capture'] != b['capture'] or a['query']['crop'] != [1140, 360, 140, 200] or
            b['query']['crop'] != a['query']['crop'] or len(baseline) != 24 or
            [s['event'] for s in a['frames']] != [s['event'] for s in b['frames']] or
            not b.get('restoration_exact')):
        raise ValueError('Require matched, restored 24-frame crate experiment')
    mask = np.zeros((200, 140), np.uint8)
    mask[35:160, 5:100] = 255
    crate, wall = np.s_[85:170, 110:138], np.s_[40:150, 10:95]
    rows, changes = [], []
    for i, (base, test) in enumerate(zip(baseline, variant)):
        delta = np.abs(base-test)
        changes.append({'frame': i, 'crate_mean': float(delta[crate].mean()),
                        'wall_mean': float(delta[wall].mean())})
        if i == 0:
            continue
        correlation, warp = cv2.findTransformECC(
            cv2.cvtColor(baseline[i-1], cv2.COLOR_RGB2GRAY),
            cv2.cvtColor(base, cv2.COLOR_RGB2GRAY), np.eye(2, 3, dtype=np.float32),
            cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-6), mask)
        if correlation < .99 or np.abs(warp[:, 2]).max() > 2:
            raise ValueError('Poor wall alignment')
        row = {'frame': i, 'correlation': correlation, 'translation': warp[:, 2].tolist()}
        for name, sequence in [('baseline', baseline), ('variant', variant)]:
            mapped = cv2.warpAffine(sequence[i], warp, (140, 200),
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            delta = np.abs(sequence[i-1]-mapped)
            row[name] = {'crate_mean': float(delta[crate].mean()), 'wall_mean': float(delta[wall].mean())}
        rows.append(row)
    summary = {name: {'mean_crate_variation': float(np.mean([r[name]['crate_mean'] for r in rows])),
                      'worst_crate_variation': max(r[name]['crate_mean'] for r in rows)}
               for name in ('baseline', 'variant')}
    summary['max_wall_change'] = max(r['wall_mean'] for r in changes)
    report = {'scope': 'Shared wall alignment, linear HDR RGB, fixed screen-edge ROI. Stability is not correct occlusion or campaign parity.',
              'baseline': str(args.baseline), 'variant': str(args.variant),
              'summary': summary, 'pairs': rows, 'changes': changes}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'comparison.json').write_text(json.dumps(report, indent=2)+'\n')
    sheet = Image.new('RGB', (6*290, 4*222))
    draw = ImageDraw.Draw(sheet)
    for i in range(24):
        for j, sequence in enumerate((baseline, variant)):
            x, y = i % 6*290+j*145, i//6*222
            # Fixed Reinhard mapping keeps bright HDR detail visible; metrics use raw values.
            rgb = np.maximum(sequence[i], 0)
            rgb = ((rgb/(1+rgb))**(1/2.2)*255).astype(np.uint8)
            sheet.paste(Image.fromarray(rgb), (x, y+20))
            draw.text((x, y), '%d %s' % (i, 'original' if j == 0 else 'precise'), fill='white')
    sheet.save(args.output/'contact.png')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
