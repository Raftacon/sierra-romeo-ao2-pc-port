"""Inspect the equipment patch in the first three seconds of two native videos.

These are independently running scenes, not matched simulation frames. Fixed
window-region statistics support visual inspection, not a general flicker score.
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw
from compare_projection_native import inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--corrected', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scope': __doc__.strip(), 'runs': []}
    sheet = Image.new('RGB', (800, 340))
    draw = ImageDraw.Draw(sheet)
    for row, path in enumerate([args.baseline, args.corrected]):
        verified = inspect(path, row == 1)
        metadata = json.loads((path / 'motion/video.json').read_text())
        if [metadata['width'], metadata['height']] != [1936, 1119] or metadata.get('crop_xywh'):
            raise ValueError('Require the inspected full-window coordinate mapping')
        if metadata['executable_sha256'] != verified['executable_sha256']:
            raise ValueError('Video/native executable mismatch')
        stamps = [json.loads(s) for s in (path / 'motion/frames.jsonl').read_text().splitlines()]
        cap = cv2.VideoCapture(str(path / 'motion/game-window.mkv'))
        samples, selected = [], []
        try:
            for item in stamps:
                if item['seconds'] > 3: break
                if item['index'] != len(samples): raise ValueError('Unexpected sample ordering')
                ok, bgr = cap.read()
                if not ok or list(bgr.shape[:2]) != [1119, 1936]: raise ValueError('Missing full video frame')
                # Native HDR patch (306,427) mapped through 1.5x presentation
                # and the observed client origin (8,31), rounded outwards.
                patch = bgr[671:682, 467:477]
                samples.append({'index': item['index'], 'seconds': item['seconds'],
                                'mean_rgb_255': float(patch.mean())})
                if len(selected) < 4 and item['seconds'] >= len(selected)*.7:
                    selected.append((item, bgr[610:750, 390:590].copy()))
        finally:
            cap.release()
        if len(samples) < 30 or samples[-1]['seconds'] < 2.8 or len(selected) != 4:
            raise ValueError('Incomplete sampled interval')
        means = np.array([s['mean_rgb_255'] for s in samples])
        report['runs'].append({'probe': str(path.resolve()), 'patch_window_xywh': [467,671,10,11],
                              'samples': samples, 'min': float(means.min()), 'max': float(means.max()),
                              'mean_abs_adjacent': float(np.abs(np.diff(means)).mean())})
        for col, (item, crop) in enumerate(selected):
            sheet.paste(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), (col*200,row*170+25))
            draw.text((col*200+3,row*170+3), f"{'off' if row==0 else 'on'} {item['seconds']:.2f}s", fill='white')
    sheet.save(args.output / 'equipment-first-three-seconds.png')
    (args.output / 'samples.json').write_text(json.dumps(report, indent=2)+'\n')
    print(args.output)


if __name__ == '__main__':
    main()
