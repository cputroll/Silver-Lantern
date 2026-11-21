import unittest

from hint_motion import glow


class HintMotionTests(unittest.TestCase):
    def test_output_stays_bounded_at_large_and_nonfinite_times(self):
        for t in (-1e308, -5.0, 0.0, 1e308, float('inf'), float('-inf'), float('nan')):
            with self.subTest(t=t):
                alpha, dy = glow(t)
                self.assertIsInstance(alpha, int)
                self.assertLessEqual(205, alpha)
                self.assertLessEqual(alpha, 255)
                self.assertLessEqual(-1.25, dy)
                self.assertLessEqual(dy, 1.25)

    def test_pulse_peaks_and_repeats_smoothly(self):
        self.assertEqual(glow(0.0), (230, 0.0))
        self.assertEqual(glow(1.25), (255, 1.25))
        self.assertEqual(glow(3.75), (205, -1.25))
        alpha, dy = glow(0.5)
        self.assertNotEqual(dy, round(dy))
        next_alpha, next_dy = glow(5.5)
        self.assertEqual(alpha, next_alpha)
        self.assertAlmostEqual(dy, next_dy)

    def test_reduced_motion_holds_still_and_keeps_full_brightness(self):
        for t in (0.0, 0.5, 1.25, 3.75, float('nan')):
            with self.subTest(t=t):
                self.assertEqual(glow(t, reduced_motion=True), (255, 0.0))
