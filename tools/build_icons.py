"""Draw the studio's icons: the logo mark (a gold film frame round a lens).

    .venv/bin/python tools/build_icons.py

Writes studio/static/icons/: favicon.svg, favicon.ico (16/32/48), and the PNGs
phones and browsers ask for (180 apple-touch, 192/512 for the web manifest,
and a maskable 512 with safe padding). Drawn at 8x and scaled down, so the
small sizes stay crisp instead of being shrunk from a big blurry one.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "studio", "static", "icons")
BG, GOLD, INK = (11, 11, 13, 255), (242, 193, 78, 255), (236, 235, 232, 255)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#0b0b0d"/>
  <rect x="7" y="15" width="50" height="34" rx="6" fill="none" stroke="#f2c14e" stroke-width="4.5"/>
  <circle cx="32" cy="32" r="10" fill="none" stroke="#ecebe8" stroke-width="4.5"/>
  <circle cx="32" cy="32" r="3.6" fill="#f2c14e"/>
  <rect x="12.5" y="20" width="6" height="3.6" rx="1.2" fill="#ecebe8"/>
</svg>
"""


def mark(size: int, pad: float = 0.0, small: bool = False) -> Image.Image:
    """The mark at `size` px. `pad` (0..1) shrinks it inside the square, for
    maskable icons; `small` drops the viewfinder notch, which is mud at 16px."""
    S = size * 8
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 14 / 64), fill=BG)
    k = S * (1 - pad) / 64           # 64-unit design grid, inset by the padding
    o = S * pad / 2
    u = lambda v: o + v * k          # noqa: E731
    w = max(1, round((5.5 if small else 4.5) * k))
    d.rounded_rectangle([u(7), u(15), u(57), u(49)], radius=round(6 * k), outline=GOLD, width=w)
    r = 10 * k
    d.ellipse([u(32) - r, u(32) - r, u(32) + r, u(32) + r], outline=INK, width=w)
    r = (4.4 if small else 3.6) * k
    d.ellipse([u(32) - r, u(32) - r, u(32) + r, u(32) + r], fill=GOLD)
    if not small:
        d.rounded_rectangle([u(12.5), u(20), u(18.5), u(23.6)], radius=round(1.2 * k), fill=INK)
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "favicon.svg"), "w") as f:
        f.write(SVG)
    ico = [mark(s, small=s <= 32) for s in (16, 32, 48)]
    ico[2].save(os.path.join(OUT, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)],
                append_images=ico[:2])
    for name, size, pad in (("icon-180.png", 180, 0), ("icon-192.png", 192, 0),
                            ("icon-512.png", 512, 0), ("icon-maskable-512.png", 512, 0.22)):
        mark(size, pad).save(os.path.join(OUT, name), optimize=True)
    with open(os.path.join(OUT, "site.webmanifest"), "w") as f:
        f.write('{\n  "name": "Gobo",\n  "short_name": "Gobo",\n  "start_url": "/",\n'
                '  "display": "standalone",\n  "background_color": "#0b0b0d",\n  "theme_color": "#0b0b0d",\n'
                '  "icons": [\n'
                '    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},\n'
                '    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},\n'
                '    {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}\n'
                '  ]\n}\n')
    print("icons →", os.path.relpath(OUT, HERE), sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
