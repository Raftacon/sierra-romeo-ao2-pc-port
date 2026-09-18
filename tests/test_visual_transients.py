"""Checks that motion compensation removes translation but preserves a brief flash."""
import sys
from pathlib import Path
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from analyze_visual_transients import align, transient


class VisualTransients(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(17)
        image = rng.integers(20, 220, (180, 200, 3), dtype=np.uint8)
        self.scene = cv2.GaussianBlur(image, (7, 7), 1.4)

    def shifted(self, dx, dy):
        return cv2.warpAffine(self.scene, np.array([[1, 0, dx], [0, 1, dy]], np.float32),
                              (200, 180), borderMode=cv2.BORDER_REFLECT)

    def test_translation_is_removed_with_correct_warp_direction(self):
        shifted = self.shifted(2, -1)
        mapped, info = align(self.scene, shifted)
        raw_error = np.abs(self.scene.astype(float) - shifted)[12:-12, 12:-12].mean()
        aligned_error = np.abs(self.scene.astype(float) - mapped)[12:-12, 12:-12].mean()
        self.assertLess(aligned_error, raw_error * .1)
        self.assertGreater(info['correlation'], .99)

    def test_local_flash_survives_motion_compensation(self):
        before, after = self.shifted(-1, 0), self.shifted(1, 0)
        clean, *_ = transient(before, self.scene, after)
        flashed = self.scene.copy()
        flashed[75:95, 85:105] += 20
        report, *_ = transient(before, flashed, after)
        self.assertLess(clean['score_9x9_rgb'], 1)
        self.assertGreater(report['score_9x9_rgb'], 15)
        x, y = report['peak_roi_xy']
        self.assertTrue(85 <= x < 105 and 75 <= y < 95)

    def test_large_movement_is_rejected(self):
        with self.assertRaises((ValueError, cv2.error)):
            align(self.scene, self.shifted(12, 0))


if __name__ == '__main__':
    unittest.main()
