import unittest

from quit_control import QuitButton, top_right


class QuitControlTests(unittest.TestCase):
    def setUp(self):
        self.button = QuitButton((10, 20, 30, 40))

    def test_visible_only_while_game_is_running(self):
        self.assertTrue(self.button.visible(True))
        self.assertFalse(self.button.visible(False))

    def test_hit_includes_left_and_top_but_excludes_right_and_bottom(self):
        for point in ((10, 20), (25, 45), (39, 59)):
            with self.subTest(point=point):
                self.assertTrue(self.button.hit(*point))
        for point in ((9, 20), (10, 19), (40, 59), (39, 60)):
            with self.subTest(point=point):
                self.assertFalse(self.button.hit(*point))

    def test_top_right_leaves_equal_top_and_right_margins(self):
        button = top_right(800, 12)
        self.assertIsInstance(button, QuitButton)
        x, y, w, h = button.rect
        self.assertEqual(x + w, 788)
        self.assertEqual(y, 12)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
