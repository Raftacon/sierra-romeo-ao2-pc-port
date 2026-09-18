"""Controls: known camera motion, erased decal, and an obscuring screen overlay."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from track_surface_transients import SurfaceTracker, corners, score_triplet


class SurfaceTrackingControls(unittest.TestCase):
    def test_moving_decal_erasure_is_detected_without_changing_registration(self):
        rng = np.random.default_rng(27)
        wall = cv2.GaussianBlur(rng.integers(60, 210, (400, 600, 3), np.uint8), (5, 5), 1)
        marked = wall.copy()
        cv2.circle(marked, (250, 215), 6, (12, 12, 12), -1)
        roi, plane = (235, 200, 35, 35), (190, 55, 250, 310)
        traces, matrices = [], []
        for erase in (False, True):
            tracker, crops, transforms = None, [], []
            cv2.setRNGSeed(0)
            for i in range(101):
                shift = -100 * np.sin(i * np.pi / 100)
                truth = np.array([[1, 0, shift], [0, 1, 0], [0, 0, 1]], np.float64)
                frame = cv2.warpPerspective(wall if erase and i == 50 else marked, truth, (600, 400))
                if tracker is None:
                    tracker = SurfaceTracker(frame, plane, roi, [])
                else:
                    tracker.advance(frame)
                expected = cv2.perspectiveTransform(corners(roi).reshape(-1, 1, 2), truth)
                actual = cv2.perspectiveTransform(corners(roi).reshape(-1, 1, 2), tracker.matrix)
                self.assertLess(float(np.linalg.norm(expected - actual, axis=2).max()), .35)
                crops.append(tracker.crop(frame))
                transforms.append(tracker.matrix.copy())
            traces.append(crops)
            matrices.append(transforms)
        # A vanished measurement cannot move the fitted surface to conceal it.
        np.testing.assert_array_equal(matrices[0], matrices[1])
        ordinary = score_triplet(*traces[0][49:52])[0]['score_3x3_rgb']
        missing = score_triplet(*traces[1][49:52])[0]['score_3x3_rgb']
        self.assertLess(ordinary, 10)
        self.assertGreater(missing, 50)
        self.assertGreater(missing, ordinary * 10)

    def test_overlay_is_invalid_measurement_not_a_surface_flash(self):
        rng = np.random.default_rng(12)
        frame = rng.integers(30, 200, (180, 240, 3), np.uint8)
        tracker = SurfaceTracker(frame, (20, 20, 200, 140), (85, 65, 40, 35), [(80, 60, 50, 45)])
        crop = tracker.crop(frame)
        self.assertFalse(crop[1].any())
        self.assertIsNone(score_triplet(crop, crop, crop))


if __name__ == '__main__':
    unittest.main()
