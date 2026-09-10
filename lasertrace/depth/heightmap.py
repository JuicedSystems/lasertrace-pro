"""Height-map conditioning for depth engraving.

Input: uint8 gray. Output: float32 height in [0, 1] where 0 = untouched
surface and 1 = full engraving depth. Every step is a pure function so the
UI can show intermediates and tests can pin each one.

Why each step exists (see docs/DEPTH_ENGRAVING.md for the research behind it):

* **orientation** - LightBurn / EZCAD "black = deepest" vs bump/height maps
  "white = high". We normalise to "1 = deep" internally and re-orient on export.
* **smoothing** - 8-bit source steps (1/255) and JPEG blocks become visible
  terraces once sliced into 0.03 mm layers; an edge-preserving blur removes
  them without rounding off the walls of the design.
* **zero plane** - the untouched surface must be *exactly* 0, otherwise the
  first slice covers the whole plate and every pass wastes time re-marking
  the background (and burns it).
* **floor** - noise a few gray levels above the surface is clamped so slice 1
  does not turn into confetti.
* **normalise + gamma** - the deepest pixel should reach `total_depth_mm`; the
  gamma is the "depth curve" operators use to keep more of the image shallow.
* **photo_to_relief** - luminance is not height. Compressing the large
  gradients and re-integrating (Poisson) gives a plausible bas-relief from a
  photo instead of a bumpy pit where the shadows are.
* **feather** - a silhouette edge with a vertical cliff chips and shadows; a
  short ramp at the outline reads better and hatches cleaner.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from ..models import DepthSettings


def orient(gray: np.ndarray, dark_is_deep: bool, black_point: int = 0, white_point: int = 255) -> np.ndarray:
    """uint8 gray -> float32 height 0..1 with 1 = deep, after a levels clamp."""
    g = gray.astype(np.float32)
    bp, wp = float(black_point), float(max(white_point, black_point + 1))
    g = np.clip((g - bp) / (wp - bp), 0.0, 1.0)
    return (1.0 - g) if dark_is_deep else g


def smooth(h: np.ndarray, sigma_px: float, edge_preserve: bool = True) -> np.ndarray:
    """Edge-preserving (bilateral) or gaussian smoothing of a float height field."""
    if sigma_px < 0.3:
        return h
    if edge_preserve:
        d = int(2 * math.ceil(2.0 * sigma_px) + 1)
        # sigmaColor in height units: 0.08 keeps a wall (>0.08 step) crisp while
        # flattening 8-bit terraces (1/255 = 0.004) and JPEG ringing.
        return cv2.bilateralFilter(h.astype(np.float32), d, 0.08, float(sigma_px))
    return cv2.GaussianBlur(h.astype(np.float32), (0, 0), float(sigma_px))


def zero_plane_level(h: np.ndarray, mode: str) -> float:
    """Height value that counts as the untouched surface."""
    if mode == "min" or h.size == 0:
        return float(h.min()) if h.size else 0.0
    if mode == "border":
        b = np.concatenate([h[0, :], h[-1, :], h[:, 0], h[:, -1]])
        return float(np.median(b))
    return 0.0


def photo_to_relief(h: np.ndarray, strength: float = 0.6, pre_blur_px: float = 1.0) -> np.ndarray:
    """Turn a luminance-derived field into a bas-relief style height field by
    compressing large gradients and re-integrating (gradient-domain, Poisson
    solve with Neumann boundaries via DCT). `strength` 0 = unchanged.

    Large gradients (shadows, hard edges) are attenuated logarithmically while
    small ones (surface shading) are kept, which is the classic bas-relief
    trick (Weyrich et al. 2007) reduced to one knob."""
    strength = float(max(0.0, min(1.0, strength)))
    if strength <= 0 or h.size < 16:
        return h
    from scipy.fft import dctn, idctn
    f = h.astype(np.float64)
    if pre_blur_px > 0:
        f = cv2.GaussianBlur(f, (0, 0), float(pre_blur_px))
    gy, gx = np.gradient(f)
    mag = np.hypot(gx, gy)
    alpha = 400.0 * strength          # 0..400: gradient of 0.01/px is scaled by log1p(4)/4 ~ 0.4 at full strength
    att = np.ones_like(mag)
    nz = mag > 1e-12
    att[nz] = np.log1p(alpha * mag[nz]) / (alpha * mag[nz])
    gx, gy = gx * att, gy * att
    # divergence with the same central differences
    div = np.gradient(gx, axis=1) + np.gradient(gy, axis=0)
    H, W = f.shape
    d = dctn(div, type=2, norm="ortho")
    yy = np.arange(H)[:, None]
    xx = np.arange(W)[None, :]
    lam = (2.0 * np.cos(np.pi * yy / H) - 2.0) + (2.0 * np.cos(np.pi * xx / W) - 2.0)
    lam[0, 0] = 1.0
    d = d / lam
    d[0, 0] = 0.0
    out = idctn(d, type=2, norm="ortho")
    lo, hi = float(out.min()), float(out.max())
    if hi - lo < 1e-9:
        return h
    return ((out - lo) / (hi - lo)).astype(np.float32)


def feather(h: np.ndarray, feather_px: float) -> np.ndarray:
    """Ramp the height down to 0 over `feather_px` from the silhouette edge."""
    if feather_px < 0.5:
        return h
    mask = (h > 0).astype(np.uint8)
    if mask.max() == 0:
        return h
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    w = np.clip(dist / float(feather_px), 0.0, 1.0)
    return (h * w).astype(np.float32)


def conditioned_height(gray: np.ndarray, s: DepthSettings, px_per_mm: float) -> tuple[np.ndarray, dict]:
    """Full conditioning chain. Returns (height float32 0..1, info dict)."""
    info: dict = {}
    h = orient(gray, s.dark_is_deep, s.black_point, s.white_point)
    if s.photo_to_relief:
        h = photo_to_relief(h, s.relief_strength)
        info["photo_to_relief"] = s.relief_strength
    sigma = s.smoothing_mm * px_per_mm
    h = smooth(h, sigma, s.edge_preserve)
    info["smoothing_px"] = round(float(sigma), 3)
    z0 = zero_plane_level(h, s.zero_plane)
    info["zero_plane"] = round(z0, 4)
    h = np.clip(h - z0, 0.0, 1.0).astype(np.float32)
    if s.floor > 0:
        h[h < s.floor] = 0.0
    peak = float(h.max()) if h.size else 0.0
    info["peak_before_normalize"] = round(peak, 4)
    if s.normalize and peak > 1e-6:
        h = h / peak
    if s.equalize > 0:
        h = equalize(h, s.equalize)
    if s.depth_gamma and abs(s.depth_gamma - 1.0) > 1e-6:
        h = np.power(h, float(s.depth_gamma), dtype=np.float32)
    if s.feather_mm > 0:
        h = feather(h, s.feather_mm * px_per_mm)
    return np.clip(h, 0.0, 1.0).astype(np.float32), info


def equalize(h: np.ndarray, blend: float) -> np.ndarray:
    """Blend towards the histogram-equalised height inside the footprint, so
    the slices remove similar areas and gray levels that are actually present
    get the resolution (empty histogram gaps stop wasting passes)."""
    blend = float(max(0.0, min(1.0, blend)))
    fp = h > 0
    if blend <= 0 or fp.sum() < 16:
        return h
    vals = h[fp]
    # tied heights must share a rank: assigning them consecutive ranks in
    # argsort order carves a raster-scan ramp into what should stay a flat
    # floor. The average of the first and last rank of each tie group keeps a
    # constant region constant (and at its own level).
    sv = np.sort(vals, kind="stable")
    lo = np.searchsorted(sv, vals, side="left").astype(np.float32)
    hi = np.searchsorted(sv, vals, side="right").astype(np.float32)
    eq = h.copy()
    eq[fp] = (lo + hi) / (2.0 * float(sv.size))
    return ((1.0 - blend) * h + blend * eq).astype(np.float32)


def quantize_height(h: np.ndarray, levels: int) -> np.ndarray:
    """Round to `levels` equal steps (the same rounding the slicer uses)."""
    levels = max(1, int(levels))
    return (np.round(h * levels) / levels).astype(np.float32)


def height_to_image(h: np.ndarray, bits: int = 8, black_is_deep: bool = True) -> np.ndarray:
    """Height (1 = deep) -> uint8 / uint16 image in the target software's convention."""
    v = (1.0 - h) if black_is_deep else h
    if bits == 16:
        return np.clip(np.round(v * 65535.0), 0, 65535).astype(np.uint16)
    return np.clip(np.round(v * 255.0), 0, 255).astype(np.uint8)


def height_stats(h: np.ndarray) -> dict:
    """Diagnostics used by AUTO depth and warnings."""
    fp = h > 0
    n = int(fp.sum())
    if n == 0:
        return {"footprint_px": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "distinct": 0, "terrace_frac": 0.0}
    vals = h[fp]
    distinct = int(np.unique(np.round(vals * 255)).size)
    # fraction of footprint pixels whose 3x3 neighbourhood is perfectly flat: high on
    # posterised / 8-bit stepped inputs, low on smooth relief
    k = np.ones((3, 3), np.float32) / 9.0
    local_mean = cv2.filter2D(h, -1, k)
    local_sq = cv2.filter2D(h * h, -1, k)
    var = np.maximum(local_sq - local_mean * local_mean, 0.0)
    terrace = float(((var < 1e-8) & fp).sum() / n)
    return {"footprint_px": n, "min": float(vals.min()), "max": float(vals.max()), "mean": float(vals.mean()), "distinct": distinct, "terrace_frac": terrace}
