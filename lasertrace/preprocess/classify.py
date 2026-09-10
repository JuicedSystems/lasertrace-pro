"""Cheap, deterministic classifier that suggests a preset for a pasted image.

Heuristics only (no ML). Signals:
  * gray fringe ratio   -> anti-aliased digital logo vs hard 1-bit art
  * background flatness -> photo/scan vs digital
  * stroke width stats  -> thin line art vs filled logo
  * module grid score   -> QR / data matrix
  * component count / size distribution -> text-heavy
  * resolution          -> tiny favicon
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..ingest import to_gray
from . import morphology, threshold


@dataclass
class Classification:
    suggested_preset: str
    kind: str                      # logo | line_art | text | qr | photo | tiny | stencil
    confidence: float
    signals: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _grid_score(mask: np.ndarray) -> float:
    """Detect QR-like module grids: strong periodicity in row/col ink projections."""
    if mask.size == 0:
        return 0.0
    ys, xs = np.where(mask > 0)
    if xs.size < 100:
        return 0.0
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    crop = mask[y0:y1, x0:x1]
    h, w = crop.shape
    if h < 21 or w < 21:
        return 0.0
    aspect = w / h
    if not (0.8 < aspect < 1.25):
        return 0.0
    # transitions per row/col; a QR has a stable, high count of runs
    row_trans = np.count_nonzero(np.diff(crop, axis=1), axis=1)
    col_trans = np.count_nonzero(np.diff(crop, axis=0), axis=0)
    rt, ct = np.median(row_trans), np.median(col_trans)
    if rt < 8 or ct < 8:
        return 0.0
    # run lengths should cluster at a single module size
    runs = []
    for r in crop[:: max(1, h // 40)]:
        d = np.diff(np.concatenate([[0], (r > 0).astype(np.int8), [0]]))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        runs.extend((ends - starts).tolist())
    if len(runs) < 20:
        return 0.0
    runs_a = np.array(runs, dtype=np.float64)
    module = np.median(runs_a[runs_a > 0])
    if module <= 0:
        return 0.0
    ratio = runs_a / module
    near_int = np.mean(np.abs(ratio - np.round(ratio)) < 0.25)
    # a 21..41 module symbol has ~10-25 transitions per row
    return float(near_int) * min(1.0, rt / 10.0)


def classify(rgb: np.ndarray) -> Classification:
    gray = to_gray(rgb)
    h, w = gray.shape
    signals: dict[str, float] = {}
    notes: list[str] = []

    hist = threshold.histogram(gray).astype(np.float64)
    total = hist.sum()
    dark = hist[:64].sum() / total
    light = hist[192:].sum() / total
    mid = 1.0 - dark - light
    signals["dark_frac"] = float(dark)
    signals["light_frac"] = float(light)
    signals["mid_frac"] = float(mid)

    # background flatness: std of the brightest 25% of pixels
    flat = gray[gray >= np.percentile(gray, 75)]
    bg_std = float(flat.std()) if flat.size else 0.0
    signals["bg_std"] = bg_std

    # color saturation
    if rgb.ndim == 3:
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        sat = float(hsv[..., 1].mean())
    else:
        sat = 0.0
    signals["saturation"] = sat

    mask = threshold.otsu(gray)
    ink_frac = float((mask > 0).mean())
    signals["ink_frac"] = ink_frac

    med_w, p10_w, p90_w = morphology.stroke_width_stats(mask)
    signals["stroke_w_med"] = med_w
    signals["stroke_w_p90"] = p90_w
    n_comp = morphology.count_components(mask)
    n_holes = morphology.count_holes(mask)
    signals["components"] = float(n_comp)
    signals["holes"] = float(n_holes)
    grid = _grid_score(mask)
    signals["grid_score"] = grid

    # ---------------- decision ladder ----------------
    if grid > 0.7:
        return Classification("qr-datamatrix", "qr", grid, signals, ["module grid detected"])

    if max(h, w) < 160:
        notes.append("very small source; upscale enabled")
        return Classification("tiny-logo", "tiny", 0.8, signals, notes)

    photo_like = bg_std > 18 or (mid > 0.35 and bg_std > 10)
    if photo_like:
        notes.append("uneven background / many midtones: treating as photo")
        return Classification("dirty-phone-photo", "photo", min(1.0, 0.5 + bg_std / 60), signals, notes)

    rel_w = med_w / max(1.0, min(h, w))
    if med_w > 0 and rel_w < 0.012 and ink_frac < 0.2:
        notes.append("thin, consistent strokes: line art")
        return Classification("thin-line-art", "line_art", 0.75, signals, notes)

    if n_comp >= 12 and n_holes >= 3 and ink_frac < 0.35:
        notes.append("many small components with counters: text")
        return Classification("small-text", "text", 0.7, signals, notes)

    if mid < 0.05 and bg_std < 3:
        notes.append("hard 1-bit art")
        return Classification("logo-fill", "logo", 0.9, signals, notes)

    return Classification("logo-fill", "logo", 0.6, signals, notes)
