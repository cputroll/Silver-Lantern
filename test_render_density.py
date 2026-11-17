import unittest
from unittest import mock

import launcher


class RenderDensityTests(unittest.TestCase):
    def test_screenshot_renderer_uses_software_without_accelerated_attempt(self):
        s = mock.MagicMock()
        s.SDL_RENDERER_SOFTWARE = 1
        s.SDL_RENDERER_ACCELERATED = 2
        s.SDL_RENDERER_PRESENTVSYNC = 4
        launcher.create_renderer(s, 'window', screenshot=True)
        s.SDL_CreateRenderer.assert_called_once_with('window', -1, 1)

    def test_output_density_tracks_letterboxed_drawable_size(self):
        self.assertEqual(launcher.render_density(1280, 720), 1)
        self.assertEqual(launcher.render_density(2560, 1440), 2)
        self.assertEqual(launcher.render_density(2560, 1080), 1.5)
        self.assertEqual(launcher.render_density(0, 0), 1)

    def test_fractional_shape_edges_share_pixel_boundaries(self):
        self.assertEqual(launcher.physical_span((0, 1, 10, 1), 1.5, 0, 0), (0, 2, 15, 1))
        self.assertEqual(launcher.physical_span((0, 2, 10, 1), 1.5, 0, 0), (0, 3, 15, 1))
        self.assertEqual(launcher.physical_span((0, 0, 10, 1), 1.5, 10, 20), (10, 20, 15, 2))

    def test_font_rebuild_only_when_density_changes(self):
        s, ttf = mock.MagicMock(), mock.MagicMock()
        ttf.TTF_OpenFont.side_effect = lambda path, size: (path, size)
        with mock.patch.object(launcher.Path, 'is_file', return_value=False):
            painter = launcher.Painter(s, ttf, mock.Mock())
        self.assertEqual(ttf.TTF_OpenFont.call_count, len(launcher.TYPE))
        painter.set_density(2)
        self.assertEqual(ttf.TTF_OpenFont.call_count, 2 * len(launcher.TYPE))
        self.assertEqual(ttf.TTF_OpenFont.call_args_list[-1].args[1], 28)
        painter.set_density(2)
        self.assertEqual(ttf.TTF_OpenFont.call_count, 2 * len(launcher.TYPE))
        self.assertEqual(ttf.TTF_CloseFont.call_count, len(launcher.TYPE))
        painter.close()


if __name__ == '__main__':
    unittest.main()
