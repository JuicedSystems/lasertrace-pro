"""Binary morphology and cleanup. Inputs/outputs are uint8 masks (255 = ink)."""
from __future__ import annotations

import cv2
import numpy as np


def _kernel(radius_px: float, shape: str = "ellipse") -> np.ndarray:
    r = max(1, int(round(radius_px)))
    k = 2 * r + 1
    s = cv2.MORPH_ELLIPSE if shape == "ellipse" else cv2.MORPH_RECT
    return cv2.getStructuringElement(s, (k, k))


def _radius(params_radius_px: float | None, radius_mm: float | None, px_per_mm: float | None) -> float:
    if radius_mm is not None and px_per_mm:
        return radius_mm * px_per_mm
    return float(params_radius_px or 1.0)


def dilate(mask: np.ndarray, radius_px: float = 1, radius_mm: float | None = None, px_per_mm: float | None = None, shape: str = "ellipse") -> np.ndarray:
    return cv2.dilate(mask, _kernel(_radius(radius_px, radius_mm, px_per_mm), shape))


def erode(mask: np.ndarray, radius_px: float = 1, radius_mm: float | None = None, px_per_mm: float | None = None, shape: str = "ellipse") -> np.ndarray:
    return cv2.erode(mask, _kernel(_radius(radius_px, radius_mm, px_per_mm), shape))


def morph_open(mask: np.ndarray, radius_px: float = 1, radius_mm: float | None = None, px_per_mm: float | None = None, shape: str = "ellipse") -> np.ndarray:
    """Erode then dilate: removes thin protrusions and specks."""
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel(_radius(radius_px, radius_mm, px_per_mm), shape))


def morph_close(mask: np.ndarray, radius_px: float = 1, radius_mm: float | None = None, px_per_mm: float | None = None, shape: str = "ellipse") -> np.ndarray:
    """Dilate then erode: reconnects broken strokes, fills pinholes."""
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _kernel(_radius(radius_px, radius_mm, px_per_mm), shape))


def invert(mask: np.ndarray) -> np.ndarray:
    return (255 - mask).astype(np.uint8)


def despeckle(mask: np.ndarray, min_area_px: float = 4, min_area_mm2: float | None = None, px_per_mm: float | None = None, connectivity: int = 8) -> np.ndarray:
    """Remove ink islands smaller than an area threshold."""
    if min_area_mm2 is not None and px_per_mm:
        min_area_px = min_area_mm2 * px_per_mm * px_per_mm
    min_area_px = float(min_area_px)
    if min_area_px <= 1:
        return mask.copy()
    n, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=connectivity)
    if n <= 1:
        return mask.copy()
    areas = stats[:, cv2.CC_STAT_AREA]
    keep = areas >= min_area_px
    keep[0] = False
    return (keep[labels] * 255).astype(np.uint8)


def fill_holes(mask: np.ndarray, max_area_px: float = 4, max_area_mm2: float | None = None, px_per_mm: float | None = None) -> np.ndarray:
    """Fill background islands (holes in ink) smaller than a threshold. Larger
    holes (letter counters) are preserved."""
    if max_area_mm2 is not None and px_per_mm:
        max_area_px = max_area_mm2 * px_per_mm * px_per_mm
    max_area_px = float(max_area_px)
    if max_area_px <= 0:
        return mask.copy()
    bg = (mask == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bg, connectivity=4)
    if n <= 1:
        return mask.copy()
    h, w = mask.shape
    out = mask.copy()
    border_labels = set(np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])))
    for lbl in range(1, n):
        if lbl in border_labels:
            continue  # touches the image border: not a hole
        if stats[lbl, cv2.CC_STAT_AREA] <= max_area_px:
            out[labels == lbl] = 255
    return out


def knockout_halo(gray: np.ndarray, mask: np.ndarray, band_px: int = 2, halo_max_gray: int = 235) -> np.ndarray:
    """Remove anti-aliased gray fringes: ink pixels in the outer `band_px` ring
    of each region whose source gray is *lighter* than `halo_max_gray` are
    dropped. Dark cores are never touched, so thin strokes survive and 1-bit
    art passes through unchanged. Square kernels keep axis-aligned corners square."""
    band = cv2.subtract(mask, erode(mask, band_px, shape="rect"))
    light = ((gray > halo_max_gray) * 255).astype(np.uint8)
    fringe = cv2.bitwise_and(band, light)
    return cv2.subtract(mask, fringe)


def count_holes(mask: np.ndarray) -> int:
    """Number of enclosed background regions (letter counters etc.)."""
    bg = (mask == 0).astype(np.uint8)
    n, labels = cv2.connectedComponents(bg, connectivity=4)
    if n <= 1:
        return 0
    border = set(np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])))
    return sum(1 for lbl in range(1, n) if lbl not in border)


def count_components(mask: np.ndarray) -> int:
    n, _ = cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)
    return max(0, n - 1)


def stroke_width_stats(mask: np.ndarray) -> tuple[float, float, float]:
    """(median, p10, p90) stroke width in px using the distance transform on the skeleton ridge."""
    if mask.max() == 0:
        return (0.0, 0.0, 0.0)
    dt = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    # ridge: local maxima of distance transform approximated by dt >= dilated dt - eps
    dil = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt >= dil - 1e-3) & (dt > 0.5)
    vals = dt[ridge] * 2.0
    if vals.size == 0:
        return (0.0, 0.0, 0.0)
    return (float(np.median(vals)), float(np.percentile(vals, 10)), float(np.percentile(vals, 90)))
