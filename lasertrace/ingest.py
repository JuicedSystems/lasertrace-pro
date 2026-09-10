"""Image ingest: files, bytes, clipboard payloads, PDF first page.

Returns an RGB numpy array plus a SourceImage descriptor. EXIF orientation is
applied here so every downstream stage sees upright pixels.
"""
from __future__ import annotations

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
SUPPORTED_EXT = RASTER_EXT | PDF_EXT | SVG_EXT

PDF_RASTER_DPI = 600


class IngestError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pil_to_rgb_array(img: Image.Image) -> np.ndarray:
    """Flatten alpha onto white and return uint8 RGB HxWx3."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        img = bg.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img, dtype=np.uint8).copy()


def _from_pil(img: Image.Image, *, path: Optional[str], origin: str, raw: bytes) -> tuple[np.ndarray, SourceImage]:
    exif_orientation = 1
    try:
        exif = img.getexif()
        exif_orientation = int(exif.get(0x0112, 1)) if exif else 1
    except Exception:
        pass
    img = ImageOps.exif_transpose(img) or img
    dpi = None
    info_dpi = img.info.get("dpi")
    if info_dpi and info_dpi[0] and info_dpi[0] > 1:
        dpi = float(info_dpi[0])
    arr = _pil_to_rgb_array(img)
    src = SourceImage(
        path=path,
        sha256=_sha(raw),
        width_px=arr.shape[1],
        height_px=arr.shape[0],
        dpi=dpi,
        mode=img.mode,
        exif_orientation=exif_orientation,
        origin=origin,  # type: ignore[arg-type]
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
    """Rasterize SVG. v1: optional cairosvg; otherwise raise a clear error."""
    try:
        import cairosvg  # type: ignore
    except ImportError as e:
        raise IngestError("SVG input needs the optional cairosvg package (pip install cairosvg)") from e
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
