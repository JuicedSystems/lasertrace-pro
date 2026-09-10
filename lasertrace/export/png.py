"""1-bit PNG preview / raster-engrave companion at a chosen DPI."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..geometry.polygons import rasterize, subpath_to_ring
from ..models import ExportProfile, Layer, PathGraph


def render_mask(graph: PathGraph, dpi: float, margin_mm: float = 0.0, tol_mm: float = 0.02) -> np.ndarray:
    """255 = ink. ENGRAVE_FILL filled; ENGRAVE_LINE/CUT/SCORE drawn as thin lines."""
    ppm = dpi / 25.4
    w = int(np.ceil((graph.width + 2 * margin_mm) * ppm)) + 1
    h = int(np.ceil((graph.height + 2 * margin_mm) * ppm)) + 1
    off = ((margin_mm - graph.bbox[0]) * ppm, (margin_mm - graph.bbox[1]) * ppm)
    img = rasterize(graph, ppm, (h, w), tol_mm, offset_px=off)
    for p in graph.paths:
        if p.layer == Layer.ENGRAVE_FILL:
            continue
        thickness = max(1, int(round((p.stroke_width_mm or 0.05) * ppm))) if p.layer == Layer.ENGRAVE_LINE else 1
        for sp in p.subpaths:
            pts = subpath_to_ring(sp, tol_mm)
            if len(pts) < 2:
                continue
            ipts = np.round((pts * ppm + np.array(off)) * 16).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [ipts], sp.closed, 255, thickness, lineType=cv2.LINE_8, shift=4)
    return img


def write_png(graph: PathGraph, profile: ExportProfile, out_path: str | Path, margin_mm: float = 1.0) -> None:
    mask = render_mask(graph, profile.png_dpi, margin_mm, profile.flatten_tol_mm)
    img = Image.fromarray(255 - mask).convert("1")  # ink black on white, 1-bit
    img.save(str(out_path), dpi=(profile.png_dpi, profile.png_dpi))
