"""Developer tool: bake launcher UI art from key-art.jpg.

The launcher ships only SDL2 + SDL2_ttf, so art is pre-baked into BMP files
that core SDL2 can load. Requires Pillow (developer machine only).
"""
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw

HERE = Path(__file__).resolve().parent
W, H = 1280, 720


def backdrop():
    art = Image.open(HERE / 'key-art.jpg').convert('RGB')
    # Blurred, dark cover layer for the whole window.
    scale = W / art.width
    cover = art.resize((W, round(art.height * scale)), Image.LANCZOS)
    top = round(cover.height * 0.18)
    cover = cover.crop((0, top, W, top + H)).filter(ImageFilter.GaussianBlur(36))
    cover = Image.blend(Image.new('RGB', (W, H), (8, 11, 14)), cover, 0.30)
    # Sharp art panel on the right, faded into the blur on its left edge.
    scale = 1000 / art.height
    panel = art.resize((round(art.width * scale), 1000), Image.LANCZOS).crop((0, 70, round(art.width * scale), 70 + H))
    x0 = W - panel.width
    mask = Image.new('L', panel.size, 255)
    draw = ImageDraw.Draw(mask)
    fade = 260
    for x in range(fade):
        t = x / fade
        draw.line([(x, 0), (x, H)], fill=round(255 * t * t * (3 - 2 * t)))
    cover.paste(panel, (x0, 0), mask)
    # Readability: darken toward the left and bottom.
    shade = Image.new('L', (W, H), 0)
    draw = ImageDraw.Draw(shade)
    for x in range(W):
        t = max(0.0, 1 - x / (W * 0.72))
        draw.line([(x, 0), (x, H)], fill=round(150 * t))
    black = Image.new('RGB', (W, H), (6, 8, 11))
    cover = Image.composite(black, cover, shade)
    shade = Image.new('L', (W, H), 0)
    draw = ImageDraw.Draw(shade)
    for y in range(H):
        t = max(0.0, (y - H * 0.6) / (H * 0.4))
        draw.line([(0, y), (W, y)], fill=round(170 * t * t))
    cover = Image.composite(black, cover, shade)
    cover.save(HERE / 'ui-backdrop.bmp')


if __name__ == '__main__':
    backdrop()
