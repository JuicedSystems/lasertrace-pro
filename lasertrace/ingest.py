"""Image ingest: files, bytes, clipboard payloads, PDF first page.

Returns an RGB numpy array plus a SourceImage descriptor. EXIF orientation is
applied here so every downstream stage sees upright pixels. High-bit-depth
images are scaled to 8 bits by one fixed scale per mode and transparency is
flattened by the rule in `_flatten_alpha`; both decisions are recorded on the
SourceImage.
"""
from __future__ import annotations

import functools
import hashlib
import io
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from .models import SourceImage

RASTER_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
SVG_EXT = {".svg"}

PDF_RASTER_DPI = 600

HIGH_BIT_MODES = {"I;16", "I;16L", "I;16B", "I;16N", "I", "F"}
ALPHA_MODES = {"RGBA", "LA", "PA", "La", "RGBa"}
UNIFORM_LUMA_STD = 12.0        # visible pixels this uniform may be one ink colour -> shape from alpha
DETAIL_LUMA_DIFF = 64          # a pixel this far from the art's median luma is a second colour
STRAY_PX = 16                  # opaque pixels breaking a rule: this many are strays, more is real detail
FLAT_ART_MIN_COVER = 0.9       # flat-colour art: its 8 commonest colours cover this share; photos far less


@functools.lru_cache(maxsize=None)
def svg_backend_available() -> bool:
    """True when cairosvg imports and finds the Cairo library.

    Checking that the package is installed is not enough: without the Cairo
    library (common on Windows) importing cairosvg raises OSError.
    """
    try:
        import cairosvg  # type: ignore  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def supported_ext() -> set[str]:
    """Extensions batch mode picks up: SVG only when it can actually be read."""
    return RASTER_EXT | PDF_EXT | (SVG_EXT if svg_backend_available() else set())


class IngestError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bit_depth(img: Image.Image) -> int:
    """Bits per sample in the file.

    Call it on the image as opened: transposing or copying drops `format` and
    the TIFF tags it reads.
    """
    mode = img.mode
    if mode.startswith("I;16"):
        return 16
    if mode == "I":
        if img.format == "PNG":  # PNG samples stop at 16 bits; Pillow 10.0 opens them as "I"
            return 16
        bits = getattr(img, "tag_v2", {}).get(258)  # TIFF BitsPerSample
        if isinstance(bits, tuple):
            bits = bits[0] if bits else None
        return int(bits) if bits else 32
    if mode == "F":
        return 32
    return 1 if mode == "1" else 8


def _high_bit_to_l(img: Image.Image) -> Image.Image:
    """16/32-bit grayscale and float images -> 8-bit L, one fixed scale per mode.

    PIL's own convert() clips everything above 255 to white, which wipes out a
    16-bit height map. The scale never depends on the pixel values, so the same
    file always gives the same pixels and one stray pixel cannot flip the whole
    image between two readings:

    * integer ("I;16*", "I") -> 0..65535 is black..white. Pillow also opens
      16-bit PNGs as "I" in older releases, and 32-bit integer files nearly
      always hold 16-bit data. Values outside the range clip.
    * float ("F") -> 0..1 is black..white, the usual convention for float
      images. Values outside it (HDR highlights, rounding overshoot) clip.
    """
    a = np.asarray(img)
    if img.mode == "F":
        out = np.clip(np.round(np.nan_to_num(a.astype(np.float64)) * 255.0), 0, 255)
    else:
        out = (np.clip(a.astype(np.int64), 0, 65535) + 128) // 257
    return Image.fromarray(out.astype(np.uint8), "L")


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ALPHA_MODES or (img.mode in ("P", "L", "RGB") and "transparency" in img.info)


def _is_one_colour(luma: np.ndarray, opaque: np.ndarray) -> bool:
    """All of the art is one ink colour, whatever the colour.

    `luma` holds the visible pixels and `opaque` marks the fully opaque ones.
    A low spread alone is not enough: a white label with a little black text
    has a low spread too, and taking its shape from alpha would turn it into a
    solid black box. Only opaque pixels count as detail, because exporters may
    tint anti-aliased edges.
    """
    if luma.std() > UNIFORM_LUMA_STD:
        return False
    detail = np.abs(luma - np.median(luma)) > DETAIL_LUMA_DIFF
    return np.count_nonzero(detail & opaque) <= STRAY_PX


def _is_light_flat_art(rgb: np.ndarray, luma: np.ndarray, opaque: np.ndarray) -> bool:
    """Light flat-colour art with no dark detail, e.g. a logo made for dark stock.

    Arguments are the visible pixels, as for `_is_one_colour`. Composited on
    white such art leaves nothing to trace, and inverting it cannot lose dark
    detail because there is none. Any dark ink (outlines, text, strokes) or
    continuous tone (photos) returns False.
    """
    if np.count_nonzero((luma < 128) & opaque) > STRAY_PX:
        return False
    q = (rgb >> 2).astype(np.int32)                    # 64 levels per channel
    counts = np.bincount((q[:, 0] << 12) | (q[:, 1] << 6) | q[:, 2], minlength=1 << 18)
    return np.partition(counts, -8)[-8:].sum() >= FLAT_ART_MIN_COVER * len(luma)


def _flatten_alpha(img: Image.Image) -> tuple[np.ndarray, str]:
    """Flatten transparency so the art is always dark ink on white.

    Compositing on white loses white (or light) art on a transparent
    background, which is common for logos cut out for dark merchandise. When
    transparency is the background (most of the border is transparent):

    * one-colour art        -> ink is the alpha channel itself, whatever the colour
    * light flat-colour art -> composite on black, then invert, but only when
      the art has no dark detail at all (`_is_light_flat_art`)
    * anything else         -> composite on white

    So art with dark ink (outlined stickers, labels with text, SVGs with
    strokes) and photos keep the plain composite on white. So does an opaque
    page with a few transparent pixels, or its white page would be inverted
    into a black box.
    """
    rgba_img = img.convert("RGBA")
    rgba = np.asarray(rgba_img)
    alpha = rgba[..., 3]
    if alpha.min() == 255:
        return rgba[..., :3].copy(), "none"
    visible = alpha >= 128
    if not visible.any():
        raise IngestError("Image is fully transparent - there is nothing to trace")
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    if (border < 128).mean() >= 0.5:
        luma = to_gray(rgba[..., :3])[visible].astype(np.float64)
        opaque = alpha[visible] == 255
        if _is_one_colour(luma, opaque):
            ink = 255 - alpha
            return np.repeat(ink[..., None], 3, axis=2), "alpha_ink"
        if _is_light_flat_art(rgba[..., :3][visible], luma, opaque):
            rgb = rgba[..., :3].astype(np.uint16)
            on_black = (rgb * alpha[..., None].astype(np.uint16) + 127) // 255
            return (255 - on_black).astype(np.uint8), "light_inverted"
    bg = Image.new("RGBA", rgba_img.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba_img)
    return np.asarray(bg.convert("RGB"), dtype=np.uint8).copy(), "on_white"


def _pil_to_rgb_array(img: Image.Image) -> tuple[np.ndarray, str]:
    """uint8 RGB HxWx3 plus the alpha policy that was applied."""
    if img.mode in HIGH_BIT_MODES:
        img = _high_bit_to_l(img)
    if _has_alpha(img):
        return _flatten_alpha(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img, dtype=np.uint8).copy(), "none"


def _from_pil(img: Image.Image, *, path: Optional[str], origin: str, raw: bytes) -> tuple[np.ndarray, SourceImage]:
    exif_orientation = 1
    try:
        exif = img.getexif()
        exif_orientation = int(exif.get(0x0112, 1)) if exif else 1
    except Exception:
        pass
    bit_depth = _bit_depth(img)
    img = ImageOps.exif_transpose(img) or img
    dpi = None
    info_dpi = img.info.get("dpi")
    if info_dpi and info_dpi[0] and info_dpi[0] > 1:
        dpi = float(info_dpi[0])
    arr, alpha_policy = _pil_to_rgb_array(img)
    src = SourceImage(
        path=path,
        sha256=_sha(raw),
        width_px=arr.shape[1],
        height_px=arr.shape[0],
        dpi=dpi,
        mode=img.mode,
        exif_orientation=exif_orientation,
        origin=origin,  # type: ignore[arg-type]
        bit_depth=bit_depth,
        alpha_policy=alpha_policy,  # type: ignore[arg-type]
    )
    return arr, src


def load_pdf_first_page(data: bytes, dpi: int = PDF_RASTER_DPI) -> Image.Image:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # pragma: no cover
        raise IngestError("PDF support requires pypdfium2") from e
    pdf = pdfium.PdfDocument(data)
    if len(pdf) == 0:
        raise IngestError("PDF has no pages")
    page = pdf[0]
    bitmap = page.render(scale=dpi / 72.0)
    img = bitmap.to_pil()
    img.info["dpi"] = (dpi, dpi)
    return img


def load_svg(data: bytes, dpi: int = PDF_RASTER_DPI) -> Image.Image:
    """Rasterize SVG with the optional cairosvg package."""
    try:
        import cairosvg  # type: ignore
    except (ImportError, OSError) as e:  # OSError: cairosvg present but the Cairo library is not
        raise IngestError(
            "SVG input needs the optional cairosvg package and the Cairo library "
            "(pip install cairosvg). Or export the artwork as PNG or PDF and open that."
        ) from e
    png = cairosvg.svg2png(bytestring=data, dpi=dpi)
    img = Image.open(io.BytesIO(png))
    img.load()
    img.info["dpi"] = (dpi, dpi)
    return img


def load_bytes(data: bytes, *, ext: str = "", path: Optional[str] = None, origin: str = "bytes") -> tuple[np.ndarray, SourceImage]:
    ext = ext.lower()
    if ext in PDF_EXT or data[:5] == b"%PDF-":
        img = load_pdf_first_page(data)
    elif ext in SVG_EXT or data.lstrip()[:5].lower() in (b"<svg ", b"<?xml"):
        img = load_svg(data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as e:
            raise IngestError(f"Unsupported or corrupt image ({ext or 'unknown'})") from e
    return _from_pil(img, path=path, origin=origin, raw=data)


def load_file(path: str | Path) -> tuple[np.ndarray, SourceImage]:
    p = Path(path)
    if not p.exists():
        raise IngestError(f"File not found: {p}")
    data = p.read_bytes()
    return load_bytes(data, ext=p.suffix, path=str(p), origin="file")


def load_pil(img: Image.Image, origin: str = "clipboard") -> tuple[np.ndarray, SourceImage]:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return _from_pil(img, path=None, origin=origin, raw=buf.getvalue())


def load_array(arr: np.ndarray, origin: str = "bytes") -> tuple[np.ndarray, SourceImage]:
    img = Image.fromarray(arr)
    return load_pil(img, origin=origin)


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """Luma conversion (Rec.601), uint8 HxW."""
    if rgb.ndim == 2:
        return rgb
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    return np.clip(0.299 * r + 0.587 * g + 0.114 * b + 0.5, 0, 255).astype(np.uint8)
