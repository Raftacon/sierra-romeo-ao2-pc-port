"""Follow a planar surface through a recorded camera sweep; rank, not diagnose, changes.

Tracking uses surrounding texture, never the measured ROI. No game interaction.
The video frame rate is nominal: frames.jsonl supplies actual sample timestamps.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def corners(rect):
    x, y, w, h = rect
    return np.float32([[x, y], [x + w - 1, y], [x + w - 1, y + h - 1], [x, y + h - 1]])


def outside(points, rect, margin=12):
    x, y, w, h = rect
    return ((points[:, 0] < x - margin) | (points[:, 0] >= x + w + margin) |
            (points[:, 1] < y - margin) | (points[:, 1] >= y + h + margin))


class SurfaceTracker:
    def __init__(self, frame, plane, roi, overlays):
        self.roi, self.overlays = roi, overlays
        self.reference_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = np.zeros(self.reference_gray.shape, np.uint8)
        x, y, w, h = plane
        mask[y + 12:y + h - 12, x + 12:x + w - 12] = 255
        for rect in [roi, *overlays]:
            rx, ry, rw, rh = rect
            mask[max(0, ry - 12):ry + rh + 12, max(0, rx - 12):rx + rw + 12] = 0
        points = cv2.goodFeaturesToTrack(self.reference_gray, 240, .015, 5, mask=mask)
        if points is None or len(points) < 20:
            raise ValueError('Need at least 20 texture features outside measurement/overlay regions')
        self.reference = points.reshape(-1, 2)
        self.matrix = np.eye(3)
        self.initial_count = len(points)

    def advance(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # No pyramid: the 21x21 feature windows must not sample the excluded ROI.
        options = dict(winSize=(21, 21), maxLevel=0, flags=cv2.OPTFLOW_USE_INITIAL_FLOW,
                       criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, .001))
        # Every fit is anchored to the original texture, avoiding accumulated
        # frame-to-frame flow drift. The previous homography is only a seed.
        rectified = cv2.warpPerspective(gray, self.matrix, (gray.shape[1], gray.shape[0]),
                                       flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        forward, ok1, _ = cv2.calcOpticalFlowPyrLK(self.reference_gray, rectified, self.reference, self.reference.copy(), **options)
        if forward is None:
            raise ValueError('Optical flow returned no points')
        backward, ok2, _ = cv2.calcOpticalFlowPyrLK(rectified, self.reference_gray, forward, self.reference.copy(), **options)
        if backward is None:
            raise ValueError('Backward flow returned no points')
        fb = np.linalg.norm(backward - self.reference, axis=1)
        seed = cv2.perspectiveTransform(self.reference.reshape(-1, 1, 2), self.matrix).reshape(-1, 2)
        forward = cv2.perspectiveTransform(forward.reshape(-1, 1, 2), self.matrix).reshape(-1, 2)
        keep = (ok1.ravel() != 0) & (ok2.ravel() != 0) & np.isfinite(fb) & (fb < .5)
        # Exclude both old and new screen overlay locations, with patch margin.
        for rect in self.overlays:
            keep &= outside(seed, rect) & outside(forward, rect)
        keep &= ((forward[:, 0] >= 12) & (forward[:, 0] < gray.shape[1] - 12) &
                 (forward[:, 1] >= 12) & (forward[:, 1] < gray.shape[0] - 12))
        ref, dst = self.reference[keep], forward[keep]
        if len(ref) < 20:
            raise ValueError(f'Only {len(ref)} bidirectionally valid features remain')
        # Reserve every fifth original feature for an independent fit check.
        held = (np.flatnonzero(keep) % 5) == 0
        if held.sum() < 8 or (~held).sum() < 20:
            raise ValueError('Too few fitting or held-out features')
        matrix, inliers = cv2.findHomography(ref[~held], dst[~held], cv2.RANSAC, .8)
        if matrix is None or not np.isfinite(matrix).all():
            raise ValueError('No finite surface homography')
        inliers = inliers.ravel().astype(bool)
        if inliers.sum() < 20 or inliers.mean() < .5:
            raise ValueError(f'Insufficient geometric consensus: {inliers.sum()}/{len(inliers)} inliers')
        predicted = cv2.perspectiveTransform(ref.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        errors = np.linalg.norm(predicted - dst, axis=1)
        validation = np.percentile(errors[held], [50, 90])
        check_matrix, check_inliers = cv2.findHomography(ref[held], dst[held], cv2.RANSAC, .8)
        if check_matrix is None or check_inliers.sum() < 8 or check_inliers.mean() < .5:
            raise ValueError('Held-out features cannot independently fit the surface')
        roi_points = corners(self.roi).reshape(-1, 1, 2)
        disagreement = float(np.linalg.norm(cv2.perspectiveTransform(roi_points, matrix) -
                                           cv2.perspectiveTransform(roi_points, check_matrix), axis=2).max())
        # Two-pixel agreement supports inspecting whole-feature presence, not
        # attributing subpixel edge changes to the renderer.
        if validation[0] > .5 or disagreement > 2:
            raise ValueError(f'Independent surface fit failed: median={validation[0]}, ROI disagreement={disagreement}')
        accepted = np.zeros(len(ref), bool)
        accepted[~held] = inliers
        residual = errors[accepted]
        # Reject flow patches touching the moving measurement region, too.
        moving_roi = cv2.perspectiveTransform(corners(self.roi).reshape(-1, 1, 2), matrix).reshape(-1, 2)
        lo, hi = moving_roi.min(axis=0), moving_roi.max(axis=0)
        if not outside(dst, (*lo, *(hi - lo + 1))).all():
            raise ValueError('Tracking feature patch overlaps the moving measured ROI')
        span = np.ptp(ref[accepted], axis=0)
        if min(span) < 24 or np.percentile(residual, 95) > .8:
            raise ValueError('Poor feature spread or excessive geometric residual')
        self.matrix = matrix
        return {'features': int(inliers.sum()), 'held_out_features': int(held.sum()),
                'held_out_median_px': float(validation[0]), 'held_out_p90_px': float(validation[1]),
                'independent_roi_disagreement_px': disagreement,
                'forward_backward_max_px': float(fb[keep].max()),
                'reprojection_p95_px': float(np.percentile(residual, 95)),
                'reference_feature_span_xy': span.tolist()}

    def crop(self, frame):
        x, y, w, h = self.roi
        local_to_reference = np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)
        mapping = self.matrix @ local_to_reference
        mask = np.full(frame.shape[:2], 255, np.uint8)
        for ox, oy, ow, oh in self.overlays:
            mask[max(0, oy - 2):oy + oh + 2, max(0, ox - 2):ox + ow + 2] = 0
        flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
        crop = cv2.warpPerspective(frame.astype(np.float32), mapping, (w, h), flags=flags)
        valid = cv2.warpPerspective(mask, mapping, (w, h), flags=flags) == 255
        return crop, valid


def score_triplet(before, current, after):
    left, center, right = [item[0] for item in (before, current, after)]
    valid = before[1] & current[1] & after[1]
    dl, dr = center - left, center - right
    strength = np.where(dl * dr > 0, np.minimum(abs(dl), abs(dr)), 0).mean(axis=2)
    supported = cv2.erode(valid.astype(np.uint8), np.ones((3, 3), np.uint8),
                          borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    if not supported.any():
        return None
    patches = cv2.boxFilter(strength, -1, (3, 3), normalize=True)
    patches[~supported] = -1
    _, peak, _, location = cv2.minMaxLoc(patches)
    return {'score_3x3_rgb': peak, 'peak_roi_xy': list(location),
            'valid_fraction': float(valid.mean())}, strength


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('motion', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--plane', type=int, nargs=4, required=True, help='Reference wall texture rectangle')
    p.add_argument('--roi', type=int, nargs=4, required=True, help='Reference measurement rectangle')
    p.add_argument('--overlay', type=int, nargs=4, action='append', default=[], help='Fixed screen exclusion; repeatable')
    p.add_argument('--start', type=float, required=True)
    p.add_argument('--end', type=float, required=True)
    a = p.parse_args()
    if not 0 <= a.start < a.end <= 60:
        p.error('Require 0 <= start < end <= 60')
    for rect in [a.plane, a.roi, *a.overlay]:
        if min(rect[:2]) < 0 or min(rect[2:]) < 12:
            p.error('Rectangles require nonnegative origin and dimensions >= 12')
    stamps_path = a.motion / 'frames.jsonl'
    stamps = [json.loads(line) for line in stamps_path.read_text().splitlines()]
    if any(s['index'] != i or not np.isfinite(s['seconds']) or s['seconds'] < 0 or
           (i and s['seconds'] < stamps[i - 1]['seconds']) for i, s in enumerate(stamps)):
        raise ValueError('Invalid frame indices or sample times')
    if not stamps or stamps[-1]['seconds'] < a.end:
        raise ValueError('Requested time range is not covered')
    a.output.mkdir(parents=True, exist_ok=False)
    cv2.setRNGSeed(0)
    cap = cv2.VideoCapture(str(a.motion / 'game-window.mkv'))
    rows, ranked, pending, tiles, failures = [], [], [], [], []
    tracker, previous_hash, duplicates, last_good_time = None, None, 0, None
    try:
        for stamp in stamps:
            if stamp['seconds'] > a.end:
                break
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Video ended before frame {stamp["index"]}')
            if stamp['seconds'] < a.start:
                continue
            if tracker is None:
                for x, y, w, h in [a.plane, a.roi, *a.overlay]:
                    if x + w > frame.shape[1] or y + h > frame.shape[0]:
                        raise ValueError('Rectangle outside the captured frame')
                tracker = SurfaceTracker(frame, a.plane, a.roi, a.overlay)
                fit = {'features': tracker.initial_count, 'reference': True}
            else:
                try:
                    fit = tracker.advance(frame)
                except (ValueError, cv2.error) as exc:
                    failures.append({'frame': stamp['index'], 'seconds': stamp['seconds'], 'error': str(exc)})
                    pending.clear()  # Never score across a failed fit.
                    previous_hash = None
                    if stamp['seconds'] - last_good_time > .25:
                        break
                    continue  # Try the original reference again; retain the failed sample.
            last_good_time = stamp['seconds']
            crop, valid = tracker.crop(frame)
            row = {'frame': stamp['index'], 'seconds': stamp['seconds'], 'fit': fit,
                   'reference_to_frame': tracker.matrix.tolist(),
                   'roi_corners_xy': cv2.perspectiveTransform(corners(a.roi).reshape(-1, 1, 2), tracker.matrix).reshape(-1, 2).tolist(),
                   'valid_fraction': float(valid.mean())}
            rows.append(row)
            tiles.append((row, crop.copy(), valid.copy()))
            # Deduplicate the raw captured image, never the aligned measurement.
            digest = hashlib.sha256(frame.tobytes()).digest()
            if digest == previous_hash:
                duplicates += 1
                continue
            previous_hash = digest
            pending.append((crop, valid, row))
            if len(pending) == 3:
                scored = score_triplet(*pending)
                if scored is not None:
                    score, strength = scored
                    score.update(frame=pending[1][2]['frame'], seconds=pending[1][2]['seconds'],
                                 neighbor_frames=[pending[0][2]['frame'], pending[2][2]['frame']],
                                 neighbor_seconds=[pending[0][2]['seconds'], pending[2][2]['seconds']])
                    ranked.append((score, [item[0].copy() for item in pending], strength))
                    ranked.sort(key=lambda item: item[0]['score_3x3_rgb'], reverse=True)
                    del ranked[12:]
                pending.pop(0)
    finally:
        cap.release()
    for score, crops, strength in ranked:
        heat = cv2.applyColorMap(np.clip(strength * 12, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        strip = np.concatenate([np.clip(c, 0, 255).astype(np.uint8) for c in crops] + [heat], axis=1)
        if not cv2.imwrite(str(a.output / f'candidate-{score["frame"]:04d}.png'), cv2.resize(strip, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)):
            raise RuntimeError('Could not save candidate sheet')
    for page_start in range(0, len(tiles), 64):
        page = tiles[page_start:page_start + 64]
        w, h = a.roi[2] * 3, a.roi[3] * 3 + 24
        sheet = np.zeros((((len(page) + 7) // 8) * h, 8 * w, 3), np.uint8)
        for i, (row, crop, valid) in enumerate(page):
            crop = np.clip(crop, 0, 255).astype(np.uint8)
            crop[~valid] = [100, 0, 100]
            x, y = i % 8 * w, i // 8 * h
            sheet[y + 24:y + h, x:x + w] = cv2.resize(crop, (w, h - 24), interpolation=cv2.INTER_NEAREST)
            cv2.putText(sheet, f'{row["frame"]} {row["seconds"]:.2f}s', (x + 2, y + 17), cv2.FONT_HERSHEY_SIMPLEX, .36, (255, 255, 255), 1)
        if not cv2.imwrite(str(a.output / f'tracked-contact-{page_start // 64:03d}.png'), sheet):
            raise RuntimeError('Could not save contact sheet')
    result = {'source': str(a.motion.resolve()), 'plane_xywh': a.plane, 'roi_xywh': a.roi,
              'overlays_xywh': a.overlay, 'requested_seconds': [a.start, a.end],
              'timestamp_file_sha256': hashlib.sha256(stamps_path.read_bytes()).hexdigest(),
              'opencv_version': cv2.__version__, 'numpy_version': np.__version__,
              'max_independent_roi_disagreement_px': 2,
              'tracked_frames': len(rows), 'identical_consecutive_frames': duplicates,
              'requested_samples': sum(a.start <= s['seconds'] <= a.end for s in stamps),
              'failures': failures, 'ranked_candidates': [r[0] for r in ranked], 'frames': rows,
              'limitations': 'Candidate ranking, not defect proof or performance measurement. A single plane cannot model parallax. Masks exclude known overlays, not arbitrary occlusion. Short sampling gaps can miss flashes. Inspect alignment and crops; no photometric normalization or ROI fitting is performed.'}
    (a.output / 'tracking.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('frames', 'ranked_candidates')}, indent=2))
    if failures or len(rows) < 3:
        raise RuntimeError('Incomplete surface track; inspect saved failure details')


if __name__ == '__main__':
    main()
