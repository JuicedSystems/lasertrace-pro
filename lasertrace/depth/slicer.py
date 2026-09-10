"""Height field -> N cumulative masks -> traced slices with depth_index.

Cumulative slicing (the way LightBurn "3D Sliced" and EZCAD3 3D work): pass i
marks every pixel whose height is >= (i - 0.5) / N. A pixel at height h is
therefore hit round(h * N) times, and because each pass removes roughly one
slice thickness wherever it lands, the finished depth follows the height map.
Band slicing (only pixels *between* two levels) would leave the deep areas
under-engraved by exactly the number of passes they were skipped in.

Per-slice hygiene keeps the deep, noisy end of the stack usable:
* islands / pits below `min_island_mm2` are removed (the beam cannot resolve them anyway)
* optional taper insets each deeper slice so the walls form a chamfer
* nesting is enforced after cleanup so slice i+1 is always inside slice i
"""
from __future__ import annotations

import math

import numpy as np
import cv2

from ..engines import get_engine
from ..engines.base import EngineContext
from ..hygiene.simplify import transform_graph
from ..models import DepthSettings, Layer, Path, PathGraph
from ..preprocess import morphology
from ..units import Transform


def slice_thresholds(levels: int) -> list[float]:
    levels = max(1, int(levels))
    return [(i - 0.5) / levels for i in range(1, levels + 1)]


def _inset(mask: np.ndarray, radius_px: float) -> np.ndarray:
    """Erode by a *fractional* radius: keep pixels whose distance to the
    nearest background pixel exceeds `radius_px`. A boundary pixel has
    distance 1, so radius < 1 keeps everything and radius 1 strips one ring;
    this lets a small draft angle bite in gradually instead of in 1 px jumps."""
    if radius_px < 1.0 or mask.max() == 0:
        return mask
    dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    return ((dist > radius_px) * 255).astype(np.uint8)


def slice_masks(h: np.ndarray, s: DepthSettings, px_per_mm: float) -> list[np.ndarray]:
    """Return `levels` uint8 masks (255 = engrave in this pass), nested."""
    masks: list[np.ndarray] = []
    prev: np.ndarray | None = None
    min_area_px = s.min_island_mm2 * px_per_mm * px_per_mm
    for i, t in enumerate(slice_thresholds(s.levels), start=1):
        m = ((h >= t) * 255).astype(np.uint8)
        if m.max() and min_area_px > 1:
            m = morphology.despeckle(m, min_area_px=min_area_px)
            m = morphology.fill_holes(m, max_area_px=min_area_px)
        if s.draft_angle_deg > 0 and i > 1 and m.max():
            # wall of slice i starts at depth (i-1) * thickness; inset = depth * tan(draft)
            depth_mm = s.total_depth_mm * (i - 1) / max(1, s.levels)
            m = _inset(m, depth_mm * math.tan(math.radians(s.draft_angle_deg)) * px_per_mm)
        if prev is not None:
            m = cv2.bitwise_and(m, prev)
        masks.append(m)
        prev = m
    return masks


def footprint_bbox(masks: list[np.ndarray]) -> tuple[int, int, int, int] | None:
    for m in masks:
        ys, xs = np.nonzero(m)
        if xs.size:
            return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    return None


def trace_slices(masks: list[np.ndarray], ctx: EngineContext,
                 frame_bbox: tuple[int, int, int, int] | None = None) -> tuple[PathGraph, list[float]]:
    """Trace every non-empty slice with the contour engine into one PathGraph
    whose paths carry `depth_index`. All slices share one frame, placed at 0,0.

    `frame_bbox` (pixels, x1/y1 exclusive) is the relief footprint the caller
    derived the px/mm from - normally the bbox of `height > 0`. It must be
    passed whenever the height map is also exported, because the height PNG is
    cropped to that same footprint: framing on slice 1 instead (its bbox is
    inset, since slice 1 only holds what is deeper than half a slice) would
    scale the PNG and the DXF differently and misreport the finished size.
    Defaults to slice 1's bbox for callers that only want vectors.
    Returns (graph, raster area per slice in mm2)."""
    ppm = ctx.px_per_mm
    bb = frame_bbox or footprint_bbox(masks)
    if bb is None:
        return PathGraph(units="mm", bbox=(0, 0, 0, 0), px_per_mm=ppm), [0.0] * len(masks)
    ox, oy = float(bb[0]), float(bb[1])
    eng = get_engine("contour")
    paths: list[Path] = []
    areas: list[float] = []
    for i, m in enumerate(masks, start=1):
        n_ink = int(np.count_nonzero(m))
        areas.append(n_ink / (ppm * ppm))
        if n_ink == 0:
            continue
        g = eng.trace(m, ctx)
        ys, xs = np.nonzero(m)
        dx, dy = (float(xs.min()) - ox) / ppm, (float(ys.min()) - oy) / ppm
        g = transform_graph(g, Transform(1, 1, dx, dy))
        for p in g.paths:
            p.depth_index = i
            p.layer = Layer.ENGRAVE_FILL
            paths.append(p)
    bbox = (0.0, 0.0, (bb[2] - ox) / ppm, (bb[3] - oy) / ppm)
    return PathGraph(paths=paths, units="mm", bbox=bbox, px_per_mm=ppm), areas


def masks_to_height(masks: list[np.ndarray]) -> np.ndarray:
    """Re-stack the nested masks into a quantised height field (pass count / N).
    Useful for the preview and for checking the slicer against the source."""
    n = len(masks)
    if n == 0:
        return np.zeros((1, 1), np.float32)
    acc = np.zeros(masks[0].shape, np.float32)
    for m in masks:
        acc += (m > 0)
    return acc / float(n)
