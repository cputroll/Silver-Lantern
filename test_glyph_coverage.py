import ast
import os
import unittest
from pathlib import Path
from unittest import mock

import launcher


class GlyphSubstitutionTests(unittest.TestCase):
    def _painter(self, provided):
        s, ttf = mock.MagicMock(), mock.MagicMock()
        ttf.TTF_OpenFont.side_effect = lambda path, size: (path, size)
        ttf.TTF_GlyphIsProvided.side_effect = lambda font, cp: provided(cp)
        with mock.patch.object(launcher.Path, 'is_file', return_value=False):
            painter = launcher.Painter(s, ttf, mock.Mock())
        painter.fonts = {'body': 'font'}
        return painter

    def test_substitutes_glyphs_the_font_lacks(self):
        painter = self._painter(lambda cp: cp != 0x2726)  # font lacks a decorative star
        out = painter._sanitize('install \u2726 click', 'body')
        self.assertEqual(out, 'install \u00b7 click')  # replaced with a covered middle dot
        self.assertNotIn('\u2726', out)

    def test_keeps_covered_glyphs_and_all_ascii(self):
        painter = self._painter(lambda cp: True)
        text = 'Ready \u00b7 \u2014 \u2026 \u203a'
        self.assertEqual(painter._sanitize(text, 'body'), text)

    def test_measure_uses_the_sanitized_text(self):
        painter = self._painter(lambda cp: cp != 0x2726)
        painter.measure('body', 'a\u2726b')
        measured = painter.ttf.TTF_SizeUTF8.call_args.args[1]
        self.assertEqual(measured, 'a\u00b7b'.encode('utf-8'))


class FontCoverageTests(unittest.TestCase):
    def test_ui_codepoints_are_covered_by_the_loaded_font(self):
        try:
            import sdl2 as s
            import sdl2.sdlttf as ttf
        except Exception:
            self.skipTest('sdl2 unavailable')
        codepoints = set()
        for name in ('launcher.py', 'game_settings.py'):
            tree = ast.parse(Path(name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    codepoints.update(ord(ch) for ch in node.value if ord(ch) > 127)
        if not codepoints:
            self.skipTest('no non-ascii UI strings')
        s.SDL_Init(0)
        ttf.TTF_Init()
        try:
            fonts_dir = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
            missing, loaded = [], 0
            for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'segoeuil.ttf'):
                face = ttf.TTF_OpenFont(str(fonts_dir / name).encode(), 32)
                if not face:
                    continue
                loaded += 1
                for cp in sorted(codepoints):
                    if not ttf.TTF_GlyphIsProvided(face, cp):
                        missing.append((name, hex(cp)))
                ttf.TTF_CloseFont(face)
            self.assertGreater(loaded, 0, 'no system font loaded')
            self.assertEqual(missing, [], 'codepoints missing from the loaded font render as tofu: %r' % missing)
        finally:
            ttf.TTF_Quit()
            s.SDL_Quit()


if __name__ == '__main__':
    unittest.main()
