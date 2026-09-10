"""Generate the LaserTrace Pro app icon (PNG sizes + multi-size ICO).

Design: dark rounded plate, a bold black-and-white ring (the product is B/W
fills), orange vector nodes with tangent handles on the ring (the trace), and
an orange laser spot with a beam at the top-right.

  python tools/make_icon.py   -> lasertrace_ui/assets/icon.png, icon_*.png, icon.ico
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "lasertrace_ui" / "assets"

PLATE = (30, 31, 34, 255)
PLATE_EDGE = (68, 72, 80, 255)
INK = (18, 18, 20, 255)
PAPER = (244, 244, 244, 255)
ORANGE = (255, 138, 31, 255)
ORANGE_SOFT = (255, 138, 31, 110)


def render(size: int = 1024) -> Image.Image:
    s = size
    ss = 4  # supersample for smooth edges
    W = s * ss
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = W / 100.0  # 100-unit design grid

    # plate
    d.rounded_rectangle((0, 0, W - 1, W - 1), radius=int(22 * u), fill=PLATE, outline=PLATE_EDGE, width=int(1.2 * u))

    # paper disc + ink ring (B/W)
    cx, cy = 48 * u, 52 * u
    r_out, r_in = 30 * u, 16 * u
    d.ellipse((cx - r_out - 4 * u, cy - r_out - 4 * u, cx + r_out + 4 * u, cy + r_out + 4 * u), fill=PAPER)
    d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out), fill=INK)
    d.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill=PAPER)

    # vector nodes + tangent handles on the outer ring
    for k in range(4):
        a = math.radians(45 + 90 * k)
        px, py = cx + r_out * math.cos(a), cy + r_out * math.sin(a)
        tx, ty = -math.sin(a), math.cos(a)
        L = 9 * u
        d.line((px - tx * L, py - ty * L, px + tx * L, py + ty * L), fill=ORANGE, width=int(1.6 * u))
        for hx, hy in ((px - tx * L, py - ty * L), (px + tx * L, py + ty * L)):
            d.ellipse((hx - 1.6 * u, hy - 1.6 * u, hx + 1.6 * u, hy + 1.6 * u), fill=ORANGE)
        rn = 3.4 * u
        d.ellipse((px - rn, py - rn, px + rn, py + rn), fill=ORANGE, outline=PLATE, width=int(0.9 * u))

    # laser spot + beam (top-right)
    bx, by = 80 * u, 20 * u
    d.line((bx, by, bx - 10 * u, by + 14 * u), fill=ORANGE_SOFT, width=int(3.5 * u))
    d.line((bx, by, bx - 10 * u, by + 14 * u), fill=ORANGE, width=int(1.4 * u))
    for rr, col in ((7.5 * u, ORANGE_SOFT), (4.5 * u, ORANGE), (1.8 * u, (255, 240, 210, 255))):
        d.ellipse((bx - rr, by - rr, bx + rr, by + rr), fill=col)

    return im.resize((s, s), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    master = render(1024)
    master.save(OUT / "icon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    for n in sizes:
        master.resize((n, n), Image.LANCZOS).save(OUT / f"icon_{n}.png")
    master.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(n, n) for n in sizes])
    print(f"wrote icon.png, icon.ico and {len(sizes)} PNG sizes to {OUT}")


if __name__ == "__main__":
    main()
