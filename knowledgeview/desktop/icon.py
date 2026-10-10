"""App icon drawn with Pillow (tray image and the .ico for the exe)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def render_icon(size: int = 256) -> Image.Image:
    s = 4  # supersample, then downscale for smooth edges
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    grad = Image.new("RGBA", (big, big))
    top, bottom = (47, 111, 222), (15, 139, 141)
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        t = y / (big - 1)
        gd.line([(0, y), (big, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "malgunbd.ttf"):
        try:
            font = ImageFont.truetype(name, int(big * 0.68))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), "K", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((big - w) / 2 - box[0], (big - h) / 2 - box[1]), "K", font=font, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str) -> None:
    render_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
