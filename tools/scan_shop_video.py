"""Find magenta flash candidates in a completed shop transition recording.

This color heuristic selects frames for inspection; absence is not a parity pass.
Times come from actual capture samples, not the video's nominal frame rate.
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.recording.resolve()
    result = json.loads((root / 'result.json').read_text())
    if result.get('exit_code') != 0 or result.get('timed_out'):
        parser.error('Require a completed recording')
    samples = [json.loads(line) for line in (root / 'frames.jsonl').read_text().splitlines()]
    if not 1 <= len(samples) <= 3600 or any(s['index'] != i for i, s in enumerate(samples)):
        parser.error('Invalid or unbounded sample list')
    args.output.mkdir(parents=True, exist_ok=False)
    video = cv2.VideoCapture(str(root / 'game-window.mkv'))
    rows, best = [], []
    try:
        for index, sample in enumerate(samples):
            ok, frame = video.read()
            if not ok:
                raise RuntimeError('Video ended before its capture sample list')
            height, width = frame.shape[:2]
            # Exclude the title bar; cover the upper area reported by the player.
            roi = frame[max(40, int(height * .08)):int(height * .65)]
            blue, green, red = [roi[:, :, c].astype(np.int16) for c in range(3)]
            mask = (red > 175) & (blue > 140) & (green < 140) & (red - green > 60)
            row_counts = mask.sum(axis=1)
            row = {'index': index, 'seconds': sample['seconds'],
                   'magenta_pixels': int(mask.sum()),
                   'max_row_fraction': float(row_counts.max() / width),
                   'wide_rows': int((row_counts >= width * .3).sum())}
            rows.append(row)
            best.append((row['magenta_pixels'], index, frame))
            best.sort(key=lambda x: (x[0], x[1]), reverse=True)
            best = best[:3]
        if video.read()[0]:
            raise RuntimeError('Video contains frames absent from capture sample list')
        images = []
        for count, index, frame in best:
            name = 'candidate-%04d.png' % index
            if not cv2.imwrite(str(args.output / name), frame):
                raise RuntimeError('Candidate image write failed')
            images.append(dict(rows[index], image=name))
        report = {'complete': True, 'recording': str(root), 'samples': len(rows),
                  'duration_seconds': samples[-1]['seconds'], 'candidates': images,
                  'wide_flash_frames': [r for r in rows if r['wide_rows'] >= 5],
                  'frames': rows,
                  'interpretation': 'Heuristic candidates only; inspect images. No visual-parity or game-FPS claim.'}
        (args.output / 'scan.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'frames'}, indent=2))
    finally:
        video.release()


if __name__ == '__main__':
    main()
