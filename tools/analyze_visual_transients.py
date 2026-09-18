"""Rank brief ROI changes in lossless window recordings; scores are not defect proof.

Requires numpy and opencv-python-headless. Uses frames.jsonl wall-clock timestamps,
not the video's nominal playback rate. Never opens or changes the game.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def align(reference, source):
    """ECC returns reference-to-source coordinates; inverse-map the source."""
    ref = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    src = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    warp = np.eye(2, 3, dtype=np.float32)
    correlation, warp = cv2.findTransformECC(
        ref, src, warp, cv2.MOTION_AFFINE,
        (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 80, 1e-5), None, 5)
    h, w = ref.shape
    corners = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]], np.float32)
    displacement = float(np.max(np.linalg.norm(
        corners @ warp[:, :2].T + warp[:, 2] - corners, axis=1)))
    if correlation < .9 or displacement > 6:
        raise ValueError(f'Poor alignment: correlation={correlation}, displacement={displacement}')
    mapped = cv2.warpAffine(source.astype(np.float32), warp, (w, h),
                            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    return mapped, {'correlation': correlation, 'max_displacement_px': displacement,
                    'warp_reference_to_source': warp.tolist()}


def transient(before, current, after):
    """Keep changes of the same sign against BOTH aligned neighbors."""
    left, left_info = align(current, before)
    right, right_info = align(current, after)
    center = current.astype(np.float32)
    dl, dr = center - left, center - right
    strength = np.mean(np.where(dl * dr > 0, np.minimum(abs(dl), abs(dr)), 0), axis=2)
    # Coherent patches rank ahead of isolated raster edge changes. An affine
    # model still cannot explain parallax, animation, specular lighting or AA.
    patches = cv2.boxFilter(strength, -1, (9, 9), normalize=True)[12:-12, 12:-12]
    _, peak, _, location = cv2.minMaxLoc(patches)
    return {'score_9x9_rgb': peak,
            'peak_roi_xy': [location[0] + 12, location[1] + 12],
            'mean_transient_rgb': float(strength[12:-12, 12:-12].mean()),
            'before_alignment': left_info, 'after_alignment': right_info}, strength, left, right


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('motion', type=Path, help='Directory containing game-window.mkv and frames.jsonl')
    p.add_argument('--output', type=Path, required=True, help='New output directory')
    p.add_argument('--roi', type=int, nargs=4, required=True, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'))
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--end', type=float, default=9.8)
    args = p.parse_args()
    x, y, w, h = args.roi
    if min(x, y) < 0 or min(w, h) < 32 or w * h > 1_000_000 or not 0 <= args.start < args.end <= 60:
        p.error('ROI must have nonnegative origin, at least 32 pixels per side, at most 1M pixels; require 0 <= start < end <= 60')
    stamps_path = args.motion / 'frames.jsonl'
    stamps = [json.loads(line) for line in stamps_path.read_text().splitlines()]
    if any(row['index'] != i or not np.isfinite(row['seconds']) or row['seconds'] < 0 or
           (i and row['seconds'] < stamps[i - 1]['seconds']) for i, row in enumerate(stamps)):
        raise ValueError('Noncontiguous frame indices or invalid timestamps')
    selected = [row for row in stamps if row['seconds'] <= args.end]
    if sum(row['seconds'] >= args.start for row in selected) < 3:
        raise ValueError('Need at least three frames in the analysis window')
    args.output.mkdir(parents=True, exist_ok=False)
    cap = cv2.VideoCapture(str(args.motion / 'game-window.mkv'))
    if not cap.isOpened():
        raise RuntimeError('Could not open video')
    rows, failures, best, unique, duplicates = [], [], [], [], 0
    try:
        for stamp in selected:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Video ended before timestamp frame {stamp["index"]}')
            if y + h > frame.shape[0] or x + w > frame.shape[1]:
                raise ValueError('ROI extends beyond decoded video')
            if stamp['seconds'] < args.start:
                continue
            crop = frame[y:y + h, x:x + w].copy()
            if unique and np.array_equal(crop, unique[-1][1]):
                duplicates += 1
                continue
            unique.append((stamp, crop))
            if len(unique) < 3:
                continue
            before, current, after = unique
            try:
                row, strength, left, right = transient(before[1], current[1], after[1])
                row.update(frame=current[0]['index'], seconds=current[0]['seconds'],
                           neighbor_frames=[before[0]['index'], after[0]['index']],
                           neighbor_seconds=[before[0]['seconds'], after[0]['seconds']])
                rows.append(row)
                candidate = (row, before[1], current[1], after[1], strength, left, right)
                best.append(candidate)
                best.sort(key=lambda item: item[0]['score_9x9_rgb'], reverse=True)
                del best[12:]
            except (cv2.error, ValueError) as exc:
                failures.append({'frame': current[0]['index'], 'error': str(exc)})
            unique.pop(0)
    finally:
        cap.release()
    for row, before, current, after, strength, left, right in best:
        heat = cv2.applyColorMap(np.clip(strength * 12, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        # Raw triplet first row; aligned neighbors and amplified residual second.
        sheet = np.zeros((h * 2 + 60, w * 3, 3), np.uint8)
        for col, tile in enumerate((before, current, after)):
            sheet[30:30 + h, col * w:(col + 1) * w] = tile
        for col, tile in enumerate((left, heat, right)):
            sheet[h + 60:, col * w:(col + 1) * w] = np.clip(tile, 0, 255).astype(np.uint8)
        cv2.putText(sheet, f'Frame {row["frame"]} at {row["seconds"]:.3f}s; score {row["score_9x9_rgb"]:.3f}',
                    (4, 20), cv2.FONT_HERSHEY_SIMPLEX, .42, (255, 255, 255), 1)
        cv2.putText(sheet, 'Aligned before / residual x12 / aligned after',
                    (4, h + 50), cv2.FONT_HERSHEY_SIMPLEX, .42, (255, 255, 255), 1)
        if not cv2.imwrite(str(args.output / f'candidate-{row["frame"]:04d}.png'), sheet):
            raise RuntimeError('Failed writing candidate sheet')
    result = {'source': str(args.motion.resolve()), 'roi_xywh': args.roi,
              'start_wall_seconds': args.start, 'end_wall_seconds': args.end,
              'decoded_frames': len(selected),
              'selected_frames': sum(row['seconds'] >= args.start for row in selected),
              'identical_consecutive_roi_frames': duplicates,
              'timestamp_file_sha256': hashlib.sha256(stamps_path.read_bytes()).hexdigest(),
              'opencv_version': cv2.__version__, 'numpy_version': np.__version__,
              'method': 'Affine ECC to each unique neighbor; minimum same-sign RGB change; peak 9x9 mean; excludes 12px border.',
              'limitations': 'Candidate ranking only. Motion, parallax, animation, lighting, rasterization and capture can cause residuals. Duplicate ROI frames are collapsed; see neighbor timestamps for gaps. No FPS or defect verdict.',
              'failures': failures, 'ranked_candidates': [item[0] for item in best], 'frames': rows}
    (args.output / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('frames', 'ranked_candidates', 'failures')}))
    print(json.dumps({'alignment_failures': len(failures), 'top_candidates': result['ranked_candidates'][:3]}))


if __name__ == '__main__':
    main()
