"""Generate the synthetic fixture set (30+ images) used by tests and tools/regress.py.

Deterministic (fixed seeds). Uses a Windows TTF when present, falls back to
PIL's default bitmap font. Run:  python tools/make_fixtures.py
"""
from __future__ import annotations

import io
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
SCRIPT_CANDIDATES = [r"C:\Windows\Fonts\BRUSHSCI.TTF", r"C:\Windows\Fonts\segoesc.ttf", r"C:\Windows\Fonts\ITCKRIST.TTF", r"C:\Windows\Fonts\comic.ttf"]
SERIF_CANDIDATES = [r"C:\Windows\Fonts\timesbd.ttf", r"C:\Windows\Fonts\georgiab.ttf", r"C:\Windows\Fonts\times.ttf"]


def font(size: int, candidates=FONT_CANDIDATES):
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def canvas(w=800, h=600, bg=255):
    return Image.new("L", (w, h), bg)


def save(img: Image.Image, name: str, fmt="PNG", **kw):
    OUT.mkdir(exist_ok=True)
    ext = {"JPEG": "jpg"}.get(fmt, fmt.lower())
    p = OUT / f"{name}.{ext}"
    img.save(p, fmt, **kw)
    return p


def jpeg_roundtrip(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("L")


def add_noise(img: Image.Image, sigma: float, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.asarray(img, dtype=np.float32)
    a = a + rng.normal(0, sigma, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------- #
def logo_geometric(aa: bool) -> Image.Image:
    scale = 4 if aa else 1
    im = canvas(800 * scale, 600 * scale)
    d = ImageDraw.Draw(im)
    s = scale
    d.ellipse((60 * s, 60 * s, 360 * s, 360 * s), fill=0)
    d.ellipse((140 * s, 140 * s, 280 * s, 280 * s), fill=255)  # ring
    d.rounded_rectangle((420 * s, 80 * s, 740 * s, 300 * s), radius=40 * s, fill=0)
    d.rounded_rectangle((470 * s, 130 * s, 690 * s, 250 * s), radius=20 * s, fill=255)
    d.polygon([(100 * s, 560 * s), (250 * s, 400 * s), (400 * s, 560 * s)], fill=0)
    d.rectangle((470 * s, 380 * s, 740 * s, 560 * s), fill=0)
    d.rectangle((520 * s, 420 * s, 690 * s, 520 * s), fill=255)
    if aa:
        im = im.resize((800, 600), Image.LANCZOS)
    return im


def text_image(text: str, size: int, fnt, w=900, h=320, aa=True) -> Image.Image:
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    bbox = d.textbbox((0, 0), text, font=fnt)
    x = (w - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]
    d.text((x, y), text, fill=0, font=fnt)
    if not aa:
        im = im.point(lambda v: 0 if v < 128 else 255)
    return im


def qr_like(modules: int, module_px: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    q = 4
    size = (modules + 2 * q) * module_px
    im = canvas(size, size)
    d = ImageDraw.Draw(im)
    grid = [[rng.random() < 0.45 for _ in range(modules)] for _ in range(modules)]
    # finder patterns
    def finder(r0, c0):
        for r in range(7):
            for c in range(7):
                on = r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
                grid[r0 + r][c0 + c] = on
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < modules and 0 <= cc < modules and (r in (-1, 7) or c in (-1, 7)):
                    grid[rr][cc] = False
    finder(0, 0); finder(0, modules - 7); finder(modules - 7, 0)
    for r in range(modules):
        for c in range(modules):
            if grid[r][c]:
                x, y = (c + q) * module_px, (r + q) * module_px
                d.rectangle((x, y, x + module_px - 1, y + module_px - 1), fill=0)
    return im


def line_art(seed: int, width: int) -> Image.Image:
    rng = random.Random(seed)
    im = canvas(800, 600)
    d = ImageDraw.Draw(im)
    # a house-like sketch: lines + a circle + a curve
    d.line([(150, 450), (150, 250), (400, 100), (650, 250), (650, 450), (150, 450)], fill=0, width=width, joint="curve")
    d.line([(300, 450), (300, 320), (380, 320), (380, 450)], fill=0, width=width)
    d.ellipse((520 - 40, 320 - 40, 520 + 40, 320 + 40), outline=0, width=width)
    pts = [(80 + i * 6, 540 + 30 * math.sin(i / 8.0)) for i in range(110)]
    d.line(pts, fill=0, width=width)
    d.line([(400, 100), (400, 450)], fill=0, width=width)  # crossing junctions
    return im


def double_outline_clipart() -> Image.Image:
    im = canvas(800, 600)
    d = ImageDraw.Draw(im)
    d.ellipse((100, 100, 500, 500), outline=0, width=14)
    d.ellipse((130, 130, 470, 470), outline=0, width=6)
    d.rectangle((540, 120, 760, 480), outline=0, width=12)
    d.rectangle((570, 150, 730, 450), outline=0, width=5)
    return im


def sign_photo(seed: int) -> Image.Image:
    fnt = font(120)
    base = text_image("LASER CO", 120, fnt, 1000, 500)
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((60, 60, 940, 440), radius=30, outline=0, width=8)
    # uneven lighting gradient + vignette
    a = np.asarray(base, dtype=np.float32)
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    light = 0.55 + 0.45 * (xx / w) * (0.6 + 0.4 * (yy / h))
    a = a * light + 20
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    img = img.rotate(-4.0, resample=Image.BICUBIC, expand=True, fillcolor=180)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    img = add_noise(img, 12, seed)
    return jpeg_roundtrip(img, 55)


def sketch(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    im = canvas(800, 600, 235)
    d = ImageDraw.Draw(im)
    # pencil-like strokes: several slightly offset gray lines
    for k in range(3):
        off = rng.normal(0, 1.5, 2)
        pts = [(120 + i * 5 + off[0], 300 + 120 * math.sin(i / 20.0) + off[1]) for i in range(110)]
        d.line(pts, fill=60 + 30 * k, width=3)
    d.ellipse((450, 120, 700, 370), outline=70, width=4)
    d.line([(450, 400), (700, 400), (575, 520), (450, 400)], fill=80, width=3)
    im = im.filter(ImageFilter.GaussianBlur(0.8))
    return add_noise(im, 6, seed)


def stencil_text() -> Image.Image:
    fnt = font(190)
    im = text_image("BOLT 8", 190, fnt, 1000, 360)
    d = ImageDraw.Draw(im)
    # stencil bridges
    for x in (120, 320, 560, 800):
        d.rectangle((x, 150, x + 14, 210), fill=255)
    return im


# --------------------------------------------------------------------------- #
# depth (3D relief) fixtures: gray height maps, white = untouched surface, black = deepest
def relief_dome_pyramid() -> Image.Image:
    H, W = 500, 800
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    h = np.zeros((H, W), np.float32)
    r = np.hypot(xx - 200, yy - 250) / 150.0
    h = np.maximum(h, np.sqrt(np.clip(1 - r * r, 0, 1)))
    h = np.maximum(h, np.clip(1 - np.maximum(np.abs(xx - 520) / 120.0, np.abs(yy - 140) / 90.0), 0, 1))
    h = np.maximum(h, np.where((xx > 420) & (xx < 720) & (yy > 300) & (yy < 440), (xx - 420) / 300.0, 0))
    return Image.fromarray((255 * (1 - h)).astype(np.uint8))


def relief_coin() -> Image.Image:
    """Coin: rim ring at mid depth, raised (shallow) letters over a deep field, dished centre."""
    S = 700
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    r = np.hypot(xx - S / 2, yy - S / 2) / (S / 2 - 20)
    field = np.where(r < 1.0, 0.8, 0.0)                     # deep field inside the coin
    rim = np.where((r > 0.86) & (r < 1.0), 0.35, field)     # rim stands proud (shallower)
    dish = np.where(r < 0.86, 0.55 + 0.35 * np.clip(r / 0.86, 0, 1) ** 2, rim)  # dished: deeper at the edge of the field
    h = np.where(r < 1.0, dish, 0.0)
    im = Image.fromarray((255 * (1 - h)).astype(np.uint8))
    d = ImageDraw.Draw(im)
    fnt = font(150)
    bbox = d.textbbox((0, 0), "42", font=fnt)
    d.text(((S - (bbox[2] - bbox[0])) // 2 - bbox[0], (S - (bbox[3] - bbox[1])) // 2 - bbox[1]), "42", fill=int(255 * (1 - 0.15)), font=fnt)  # letters near the surface
    return im.filter(ImageFilter.GaussianBlur(1.0))


def relief_photo_like(seed: int) -> Image.Image:
    """A 'photo': soft blob with texture, shadows and a hard edge, to exercise photo_to_relief."""
    rng = np.random.default_rng(seed)
    H, W = 480, 640
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    blob = np.exp(-(((xx - 320) / 160) ** 2 + ((yy - 240) / 120) ** 2))
    shade = 0.5 + 0.5 * np.clip((xx - 200) / 300, 0, 1)      # lighting gradient (not height)
    tex = rng.normal(0, 0.05, (H, W)).astype(np.float32)
    lum = np.clip(0.15 + 0.7 * blob * shade + tex, 0, 1)
    lum[(xx > 80) & (xx < 130) & (yy > 60) & (yy < 420)] = 0.05  # a hard dark bar (edge)
    return Image.fromarray((255 * lum).astype(np.uint8))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    targets: dict[str, dict] = {}
    rec = lambda name, preset, nodes, **kw: targets.__setitem__(name, {"preset": preset, "target_nodes": nodes, **kw})  # noqa: E731

    # 1-3 sharp / anti-aliased / JPEG geometric logo
    g = logo_geometric(aa=False)
    save(g, "logo_geometric_sharp"); rec("logo_geometric_sharp.png", "logo-fill", 60, holes=3)
    ga = logo_geometric(aa=True)
    save(ga, "logo_geometric_aa"); rec("logo_geometric_aa.png", "logo-fill", 60, holes=3)
    save(jpeg_roundtrip(ga, 35).convert("RGB"), "logo_geometric_jpeg", "JPEG", quality=35); rec("logo_geometric_jpeg.jpg", "logo-fill", 70, holes=3)
    save(add_noise(ga, 18, 1), "logo_geometric_noisy"); rec("logo_geometric_noisy.png", "logo-fill", 70, holes=3)

    # 5-6 pure circle and rounded rect (geometry snap targets)
    im = canvas(600, 600); ImageDraw.Draw(im).ellipse((50, 50, 550, 550), fill=0)
    save(im, "circle_sharp"); rec("circle_sharp.png", "logo-fill", 12, holes=0)
    im = canvas(800, 500); ImageDraw.Draw(im).rounded_rectangle((60, 60, 740, 440), radius=80, fill=0)
    save(im, "rounded_rect"); rec("rounded_rect.png", "logo-fill", 16, holes=0)
    im = canvas(600, 600); d = ImageDraw.Draw(im); d.ellipse((50, 50, 550, 550), fill=0); d.ellipse((150, 150, 450, 450), fill=255)
    save(im, "ring"); rec("ring.png", "logo-fill", 24, holes=1)

    # 8-12 text
    bold = font(160)
    save(text_image("ABOB80", 160, bold), "text_bold_counters"); rec("text_bold_counters.png", "small-text", 260, holes=9)
    save(text_image("ABOB80", 160, bold, aa=False), "text_bold_counters_1bit"); rec("text_bold_counters_1bit.png", "small-text", 260, holes=9)
    small = font(42)
    save(text_image("Serial No. 0018-AB / Batch 42", 42, small, 900, 140), "text_small_serial"); rec("text_small_serial.png", "small-text", 900, holes=14)
    serif = font(150, SERIF_CANDIDATES)
    save(text_image("Rebate", 150, serif), "text_serif"); rec("text_serif.png", "small-text", 300, holes=5)
    script = font(150, SCRIPT_CANDIDATES)
    save(text_image("Signature", 150, script), "text_script_thin"); rec("text_script_thin.png", "thin-line-art", 400)
    save(stencil_text(), "stencil_text"); rec("stencil_text.png", "stamp-stencil", 200, holes=4)

    # 14-16 QR-like
    save(qr_like(25, 12, 7), "qr_25_clean"); rec("qr_25_clean.png", "qr-datamatrix", 2200)
    save(qr_like(21, 6, 3), "qr_21_small"); rec("qr_21_small.png", "qr-datamatrix", 1600)
    save(jpeg_roundtrip(qr_like(25, 10, 11), 45).convert("RGB"), "qr_25_jpeg", "JPEG", quality=45); rec("qr_25_jpeg.jpg", "qr-datamatrix", 2400)

    # 17-20 line art
    save(line_art(1, 3), "lineart_thin_3px"); rec("lineart_thin_3px.png", "thin-line-art", 120)
    save(line_art(2, 7), "lineart_medium_7px"); rec("lineart_medium_7px.png", "thin-line-art", 120)
    la = line_art(3, 2)
    a = np.asarray(la).copy(); rng = np.random.default_rng(5)
    # random breaks
    for _ in range(25):
        y, x = rng.integers(0, 600), rng.integers(0, 800)
        a[max(0, y - 2): y + 2, max(0, x - 2): x + 2] = 255
    save(Image.fromarray(a), "lineart_broken"); rec("lineart_broken.png", "thin-line-art", 140)
    save(double_outline_clipart(), "double_outline_clipart"); rec("double_outline_clipart.png", "logo-fill", 60, holes=4)

    # 21-24 photos / scans / sketches
    save(sign_photo(9).convert("RGB"), "photo_sign_skewed", "JPEG", quality=70); rec("photo_sign_skewed.jpg", "dirty-phone-photo", 900)
    save(sketch(4), "sketch_pencil"); rec("sketch_pencil.png", "thin-line-art", 200)
    scan = text_image("SCAN", 200, bold).filter(ImageFilter.GaussianBlur(2.0))
    save(add_noise(scan, 10, 8), "scan_soft_text"); rec("scan_soft_text.png", "logo-fill", 120, holes=1)
    ph = logo_geometric(True).rotate(2.5, resample=Image.BICUBIC, expand=True, fillcolor=200)
    save(jpeg_roundtrip(add_noise(ph, 15, 2), 40).convert("RGB"), "photo_logo_dirty", "JPEG", quality=40); rec("photo_logo_dirty.jpg", "dirty-phone-photo", 200, holes=3)

    # 25-28 tiny / screenshot / color
    tiny = logo_geometric(True).resize((64, 48), Image.LANCZOS)
    save(tiny, "tiny_logo_64px"); rec("tiny_logo_64px.png", "tiny-logo", 80)
    fav = text_image("Lz", 40, bold, 48, 48)
    save(fav, "favicon_48px"); rec("favicon_48px.png", "tiny-logo", 60)
    color = Image.new("RGB", (800, 600), (240, 240, 250))
    d = ImageDraw.Draw(color)
    d.ellipse((100, 100, 500, 500), fill=(200, 30, 30))
    d.rectangle((520, 150, 760, 450), fill=(20, 60, 180))
    d.text((150, 260), "COLOR", fill=(255, 255, 255), font=font(90))
    save(color, "color_logo"); rec("color_logo.png", "logo-fill", 120)
    shot = Image.new("RGB", (900, 500), (255, 255, 255))
    d = ImageDraw.Draw(shot)
    d.rectangle((0, 0, 900, 60), fill=(230, 230, 230))
    d.text((20, 15), "Inbox - logo.png", fill=(80, 80, 80), font=font(28))
    d.text((120, 160), "ACME TOOLS", fill=(0, 0, 0), font=font(110))
    d.line((120, 300, 780, 300), fill=(0, 0, 0), width=6)
    save(shot, "screenshot_email"); rec("screenshot_email.png", "logo-fill", 300)

    # 29-33 misc: halo, inverted, gradient logo, thin hairlines, overlapping shapes
    halo = logo_geometric(True).filter(ImageFilter.GaussianBlur(1.5))
    save(halo, "logo_gray_halo"); rec("logo_gray_halo.png", "logo-fill", 70, holes=3)
    inv = Image.fromarray(255 - np.asarray(logo_geometric(False)))
    save(inv, "logo_inverted_white_on_black"); rec("logo_inverted_white_on_black.png", "logo-fill", 70)
    grad = np.asarray(logo_geometric(True), dtype=np.float32)
    yy = np.linspace(0, 1, grad.shape[0])[:, None]
    grad = np.where(grad < 128, 40 + 120 * yy, grad)
    save(Image.fromarray(grad.astype(np.uint8)), "logo_gradient_fill"); rec("logo_gradient_fill.png", "logo-fill", 70, holes=3)
    im = canvas(800, 400); d = ImageDraw.Draw(im)
    for i in range(6):
        d.line((50, 40 + i * 60, 750, 40 + i * 60), fill=0, width=1 + i)
    save(im, "hairlines_1_to_6px"); rec("hairlines_1_to_6px.png", "thin-line-art", 24)
    im = canvas(800, 600); d = ImageDraw.Draw(im)
    d.ellipse((100, 100, 450, 450), fill=0); d.rectangle((300, 250, 700, 550), fill=0); d.ellipse((550, 80, 780, 310), fill=0)
    save(im, "overlapping_shapes_union"); rec("overlapping_shapes_union.png", "logo-fill", 40, holes=0)
    # stroke + fill hybrid
    im = canvas(800, 600); d = ImageDraw.Draw(im)
    d.ellipse((80, 80, 380, 380), fill=0)
    d.line([(420, 100), (760, 100), (760, 500), (420, 500), (420, 100)], fill=0, width=3)
    d.line([(420, 300), (760, 300)], fill=0, width=3)
    save(im, "hybrid_fill_plus_lines"); rec("hybrid_fill_plus_lines.png", "logo-fill", 60)
    # multi-tone quantize candidate
    im = Image.new("RGB", (800, 600), (255, 255, 255)); d = ImageDraw.Draw(im)
    d.ellipse((80, 80, 520, 520), fill=(255, 200, 0)); d.ellipse((160, 160, 440, 440), fill=(0, 0, 0)); d.rectangle((560, 100, 760, 500), fill=(120, 120, 120))
    save(im, "multitone_logo"); rec("multitone_logo.png", "logo-fill", 40)

    # 36-38 depth (3D relief) height maps
    save(relief_dome_pyramid(), "relief_dome_pyramid"); rec("relief_dome_pyramid.png", "depth-relief", 900, depth=True)
    save(relief_coin(), "relief_coin"); rec("relief_coin.png", "depth-coin", 1500, depth=True)
    save(relief_photo_like(21), "relief_photo_like"); rec("relief_photo_like.png", "depth-photo-relief", 1400, depth=True)

    (OUT / "targets.json").write_text(json.dumps(targets, indent=2), encoding="utf-8")
    print(f"wrote {len(list(OUT.glob('*.png'))) + len(list(OUT.glob('*.jpg')))} fixtures to {OUT}")


if __name__ == "__main__":
    main()
