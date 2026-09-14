"""Ingest: bit depth, transparency, PDF, EXIF and SVG handling."""
from __future__ import annotations

import io
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from lasertrace.ingest import IngestError, _from_pil, load_bytes, supported_ext, svg_backend_available, to_gray

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'


def _save(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _rgba_art(fill, bg=(0, 0, 0, 0), size=(120, 80)) -> Image.Image:
    """A filled ellipse in the middle of the canvas, no anti-aliasing."""
    w, h = size
    im = Image.new("RGBA", size, bg)
    ImageDraw.Draw(im).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=fill)
    return im


def _light_two_colour_art() -> Image.Image:
    """White and pale-yellow blocks on transparent: a logo made for dark stock."""
    im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 55, 60), fill=(255, 255, 255, 255))
    d.rectangle((65, 20, 100, 60), fill=(255, 220, 80, 255))
    return im


# --------------------------------------------------------------- bit depth
def test_16bit_png_keeps_its_tonal_range():
    ramp = np.linspace(0, 65535, 64 * 512).reshape(64, 512).round().astype(np.uint16)
    rgb, src = load_bytes(_save(Image.fromarray(ramp), "PNG"), ext=".png")
    assert src.mode in ("I;16", "I") and src.bit_depth == 16   # Pillow 10.0 opens it as "I"
    g = rgb[..., 0]
    assert g.min() == 0 and g.max() == 255
    assert len(np.unique(g)) == 256
    assert np.all(np.diff(g.ravel().astype(int)) >= 0)   # a ramp stays a ramp


def test_16bit_scaling_is_exact_at_the_codes():
    a = (np.arange(256, dtype=np.uint32) * 257).astype(np.uint16).reshape(16, 16)
    rgb, _ = load_bytes(_save(Image.fromarray(a), "PNG"), ext=".png")
    assert np.array_equal(rgb[..., 0], np.arange(256, dtype=np.uint8).reshape(16, 16))


def test_16bit_png_opened_as_mode_i_is_read_as_16bit():
    # what Pillow 10.0 hands over for a 16-bit PNG: mode "I", format PNG.
    # Built by hand because newer Pillow opens the file as "I;16".
    a = (np.arange(256, dtype=np.int32) * 257).reshape(16, 16)
    im = Image.fromarray(a)
    im.format = "PNG"
    rgb, src = _from_pil(im, path=None, origin="bytes", raw=b"")
    assert src.mode == "I" and src.bit_depth == 16
    assert np.array_equal(rgb[..., 0], np.arange(256, dtype=np.uint8).reshape(16, 16))


def test_32bit_int_tiff_with_16bit_values_is_scaled():
    a = (np.arange(256, dtype=np.int32) * 257).reshape(16, 16)
    rgb, src = load_bytes(_save(Image.fromarray(a), "TIFF"), ext=".tif")
    assert src.mode == "I" and src.bit_depth == 32
    assert np.array_equal(rgb[..., 0], np.arange(256, dtype=np.uint8).reshape(16, 16))


def test_32bit_int_scale_does_not_depend_on_the_values():
    # 0..65535 is black..white whatever the image holds: one pixel above 255
    # must not switch a dark map to a different reading
    a = np.arange(256, dtype=np.int32).reshape(16, 16)
    b = a.copy()
    b[0, 0] = 256
    ra, _ = load_bytes(_save(Image.fromarray(a), "TIFF"), ext=".tif")
    rb, _ = load_bytes(_save(Image.fromarray(b), "TIFF"), ext=".tif")
    assert np.array_equal(ra[..., 0], ((a + 128) // 257).astype(np.uint8))
    assert np.abs(ra.astype(int) - rb.astype(int)).max() <= 1


def test_float_tiff_in_unit_range_is_scaled():
    a = np.linspace(0.0, 1.0, 256, dtype=np.float32).reshape(16, 16)
    rgb, src = load_bytes(_save(Image.fromarray(a), "TIFF"), ext=".tif")
    assert src.mode == "F" and src.bit_depth == 32
    g = rgb[..., 0]
    assert g.min() == 0 and g.max() == 255 and len(np.unique(g)) == 256


def test_float_scale_does_not_depend_on_the_values():
    # 0..1 is black..white: rounding overshoot or an HDR highlight clips to
    # white instead of switching the whole map to a 0..255 reading
    a = np.linspace(0.0, 1.0, 256, dtype=np.float32).reshape(16, 16)
    b = a.copy()
    b[0, 0], b[0, 1] = 1.000001, 1.5
    ra, _ = load_bytes(_save(Image.fromarray(a), "TIFF"), ext=".tif")
    rb, _ = load_bytes(_save(Image.fromarray(b), "TIFF"), ext=".tif")
    assert rb[0, 0, 0] == 255 and rb[0, 1, 0] == 255
    assert np.array_equal(ra[..., 0].ravel()[2:], rb[..., 0].ravel()[2:])


def test_8bit_gray_is_unchanged():
    a = np.arange(256, dtype=np.uint8).reshape(16, 16)
    rgb, src = load_bytes(_save(Image.fromarray(a), "PNG"), ext=".png")
    assert src.bit_depth == 8 and src.alpha_policy == "none"
    assert np.array_equal(rgb[..., 0], a)


# ------------------------------------------------------------ transparency
def test_white_art_on_transparent_becomes_ink():
    rgb, src = load_bytes(_save(_rgba_art((255, 255, 255, 255)), "PNG"), ext=".png")
    assert src.alpha_policy == "alpha_ink"
    assert rgb[40, 60, 0] == 0      # inside the ellipse
    assert rgb[5, 5, 0] == 255      # transparent background


def test_black_art_on_transparent_matches_compositing_on_white():
    im = _rgba_art((0, 0, 0, 255))
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "alpha_ink"
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    assert np.array_equal(rgb, np.asarray(bg.convert("RGB")))


def test_light_flat_art_on_transparent_is_inverted():
    rgb, src = load_bytes(_save(_light_two_colour_art(), "PNG"), ext=".png")
    assert src.alpha_policy == "light_inverted"
    g = to_gray(rgb)
    assert g[40, 35] < 128 and g[40, 80] < 128   # both colours are ink
    assert g[5, 5] == 255                        # background is paper


def test_light_art_with_a_few_stray_dark_pixels_is_still_inverted():
    im = _light_two_colour_art()
    for x in range(30, 34):
        im.putpixel((x, 30), (0, 0, 0, 255))
    _, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "light_inverted"


def test_sticker_with_a_dark_outline_is_composited_on_white():
    im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((30, 20, 90, 60), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=3)
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "on_white"
    assert rgb[40, 31, 0] == 0      # the outline is ink
    assert rgb[40, 60, 0] == 255    # the white fill is paper


def test_white_label_with_a_little_dark_text_keeps_its_text():
    # the text is a sliver of the pixels, so the label is nearly uniform; it
    # must become neither a solid black box (alpha ink) nor a negative
    im = Image.new("RGBA", (340, 240), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 319, 219), fill=(255, 255, 255, 255))
    for x in range(150, 190, 10):
        d.rectangle((x, 115, x + 1, 120), fill=(0, 0, 0, 255))   # four thin "letters"
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "on_white"
    assert rgb[117, 150, 0] == 0    # the text is ink
    assert rgb[60, 60, 0] == 255    # the label is paper


def test_cut_out_photo_of_a_light_subject_is_not_inverted():
    # continuous tone and no dark detail: the tones must keep their direction
    h, w = 80, 120
    yy, xx = np.mgrid[0:h, 0:w]
    tone = 150 + 100 * xx / (w - 1) + np.random.default_rng(0).normal(0, 4, (h, w))
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = np.clip(tone, 0, 255).astype(np.uint8)[..., None]
    rgba[..., 3] = np.where((xx - 60) ** 2 / 45**2 + (yy - 40) ** 2 / 30**2 <= 1, 255, 0)
    rgb, src = load_bytes(_save(Image.fromarray(rgba), "PNG"), ext=".png")
    assert src.alpha_policy == "on_white"
    assert np.array_equal(rgb[40, 20:100], rgba[40, 20:100, :3])


def test_dark_multicolour_art_on_transparent_is_composited_on_white():
    im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 55, 60), fill=(200, 0, 0, 255))
    d.rectangle((65, 20, 100, 60), fill=(0, 0, 200, 255))
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "on_white"
    assert tuple(rgb[5, 5]) == (255, 255, 255)
    assert tuple(rgb[40, 35]) == (200, 0, 0)


def test_opaque_page_with_transparent_corners_is_not_inverted():
    im = _rgba_art((0, 0, 0, 255), bg=(255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    for x, y in ((0, 0), (110, 0), (0, 70), (110, 70)):
        d.rectangle((x, y, x + 9, y + 9), fill=(0, 0, 0, 0))
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "on_white"
    assert rgb[40, 60, 0] == 0 and rgb[40, 20, 0] == 255   # logo is ink, page is paper


def test_opaque_rgba_is_left_alone():
    im = _rgba_art((0, 0, 0, 255), bg=(255, 255, 255, 255))
    rgb, src = load_bytes(_save(im, "PNG"), ext=".png")
    assert src.alpha_policy == "none"
    assert np.array_equal(rgb, np.asarray(im.convert("RGB")))


def test_fully_transparent_image_is_rejected():
    with pytest.raises(IngestError, match="transparent"):
        load_bytes(_save(Image.new("RGBA", (10, 10), (0, 0, 0, 0)), "PNG"), ext=".png")


def test_palette_image_with_a_transparent_index():
    im = Image.new("P", (120, 80), 0)
    im.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    ImageDraw.Draw(im).ellipse((30, 20, 90, 60), fill=1)
    rgb, src = load_bytes(_save(im, "PNG", transparency=0), ext=".png")
    assert src.alpha_policy == "alpha_ink"
    assert rgb[40, 60, 0] == 0 and rgb[5, 5, 0] == 255


# ------------------------------------------------------- other containers
def test_pdf_first_page_is_rasterised_at_600_dpi():
    page1 = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(page1).rectangle((0, 0, 99, 99), fill="black")    # left half
    page2 = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(page2).rectangle((100, 0, 199, 99), fill="black")  # right half
    data = _save(page1, "PDF", resolution=100, save_all=True, append_images=[page2])
    rgb, src = load_bytes(data, ext=".pdf")
    assert src.dpi == 600
    assert rgb.shape[:2] == (600, 1200)
    g = to_gray(rgb)
    assert g[:, :500].mean() < 40 and g[:, 700:].mean() > 215   # page 1, not page 2


def test_exif_orientation_is_applied():
    im = Image.new("RGB", (40, 20), "white")
    ImageDraw.Draw(im).rectangle((0, 0, 9, 19), fill="black")   # band on the left edge
    exif = Image.Exif()
    exif[0x0112] = 6                                             # display rotated 90 deg CW
    rgb, src = load_bytes(_save(im, "JPEG", exif=exif.tobytes(), quality=95), ext=".jpg")
    assert src.exif_orientation == 6
    assert rgb.shape[:2] == (40, 20)
    g = to_gray(rgb)
    assert g[:8].mean() < 60 and g[-8:].mean() > 200             # the band is now on top


@pytest.fixture
def fake_cairosvg(tmp_path, monkeypatch):
    """Install a stand-in cairosvg module whose import runs `body`."""
    def install(body: str) -> None:
        (tmp_path / "cairosvg.py").write_text(body)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "cairosvg", raising=False)
        svg_backend_available.cache_clear()
    yield install
    sys.modules.pop("cairosvg", None)   # the stand-in, if it imported
    svg_backend_available.cache_clear()


# cairosvg installed but the Cairo library missing raises OSError on import
NO_CAIRO = 'raise OSError("no library called \\"cairo-2\\" was found")'


@pytest.mark.parametrize("body, readable", [("", True), (NO_CAIRO, False)])
def test_batch_picks_up_svg_only_when_it_can_be_read(fake_cairosvg, body, readable):
    fake_cairosvg(body)
    assert svg_backend_available() is readable
    assert (".svg" in supported_ext()) is readable


def test_svg_without_cairosvg_says_what_to_do(monkeypatch):
    monkeypatch.setitem(sys.modules, "cairosvg", None)
    with pytest.raises(IngestError, match="cairosvg"):
        load_bytes(SVG, ext=".svg")


def test_svg_without_the_cairo_library_says_what_to_do(fake_cairosvg):
    fake_cairosvg(NO_CAIRO)
    with pytest.raises(IngestError, match="Cairo library"):
        load_bytes(SVG, ext=".svg")


# ------------------------------------------------------------- AUTO notes
def test_auto_notes_explain_the_ingest_decisions():
    from lasertrace.auto import build_job

    rgb, src = load_bytes(_save(_rgba_art((255, 255, 255, 255), size=(400, 300)), "PNG"), ext=".png")
    assert "shape taken from transparency" in build_job(rgb, "auto", 30.0, src).notes

    ramp = np.tile(np.linspace(0, 65535, 300), (200, 1)).round().astype(np.uint16)
    rgb, src = load_bytes(_save(Image.fromarray(ramp), "PNG"), ext=".png")
    assert "16-bit source -> 0..65535 scaled to 8-bit" in build_job(rgb, "auto-depth", 30.0, src).notes

    ramp = np.tile(np.linspace(0.0, 1.0, 300, dtype=np.float32), (200, 1))
    rgb, src = load_bytes(_save(Image.fromarray(ramp), "TIFF"), ext=".tif")
    assert "32-bit source -> 0..1 scaled to 8-bit" in build_job(rgb, "auto-depth", 30.0, src).notes
