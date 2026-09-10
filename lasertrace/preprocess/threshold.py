"""Binarization. Every function takes uint8 gray (HxW) and returns a binary
uint8 mask where 255 = INK (will be marked) and 0 = background.

Convention: dark pixels are ink unless `invert` is applied later.
"""
from __future__ import annotations

import cv2
import numpy as np


def _ink_from_dark(gray: np.ndarray, thresh: np.ndarray | int) -> np.ndarray:
    return ((gray.astype(np.int16) <= thresh) * 255).astype(np.uint8)


def manual(gray: np.ndarray, value: int = 128) -> np.ndarray:
    value = int(max(0, min(255, value)))
    return _ink_from_dark(gray, value)


def otsu(gray: np.ndarray, bias: int = 0) -> np.ndarray:
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return manual(gray, int(t) + int(bias))


def otsu_value(gray: np.ndarray) -> int:
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return int(t)


def adaptive(gray: np.ndarray, block: int = 51, c: float = 10.0, method: str = "gaussian", guard: float = 0.35) -> np.ndarray:
    block = int(block)
    if block % 2 == 0:
        block += 1
    block = max(3, block)
    m = cv2.ADAPTIVE_THRESH_GAUSSIAN_C if method == "gaussian" else cv2.ADAPTIVE_THRESH_MEAN_C
    # THRESH_BINARY_INV: dark -> 255
    local = cv2.adaptiveThreshold(gray, 255, m, cv2.THRESH_BINARY_INV, block, float(c))
    return _global_guard(gray, local, guard)


def _global_guard(gray: np.ndarray, local_mask: np.ndarray, guard: float) -> np.ndarray:
    """Local thresholds fragment large flat regions (a black logo interior gets a
    threshold below its own noise). Guard with Otsu: anything clearly darker
    than the global cut is ink, anything clearly lighter is paper."""
    if guard <= 0:
        return local_mask
    t = otsu_value(gray)
    sure_ink = gray <= t * (1.0 - guard)
    sure_bg = gray > t + (255 - t) * guard
    out = local_mask.copy()
    out[sure_ink] = 255
    out[sure_bg] = 0
    return out


def sauvola(gray: np.ndarray, window: int = 25, k: float = 0.2, r: float = 128.0, guard: float = 0.35) -> np.ndarray:
    """Sauvola local threshold via integral images (deterministic, no skimage
    dependency) with a global Otsu guard (see _global_guard)."""
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = max(3, window)
    g = gray.astype(np.float64)
    mean = cv2.boxFilter(g, -1, (window, window), normalize=True, borderType=cv2.BORDER_REFLECT)
    sq = cv2.boxFilter(g * g, -1, (window, window), normalize=True, borderType=cv2.BORDER_REFLECT)
    std = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    t = mean * (1.0 + k * (std / r - 1.0))
    return _global_guard(gray, ((g <= t) * 255).astype(np.uint8), guard)


def niblack(gray: np.ndarray, window: int = 25, k: float = -0.2, guard: float = 0.35) -> np.ndarray:
    window = int(window)
    if window % 2 == 0:
        window += 1
    g = gray.astype(np.float64)
    mean = cv2.boxFilter(g, -1, (window, window), normalize=True, borderType=cv2.BORDER_REFLECT)
    sq = cv2.boxFilter(g * g, -1, (window, window), normalize=True, borderType=cv2.BORDER_REFLECT)
    std = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    t = mean + k * std
    return _global_guard(gray, ((g <= t) * 255).astype(np.uint8), guard)


def hysteresis(gray: np.ndarray, low: int = 100, high: int = 160) -> np.ndarray:
    """Pixels darker than `low` are ink; pixels between low..high are ink only if
    connected to a sure-ink pixel. Great for anti-aliased edges and faint strokes."""
    low, high = int(min(low, high)), int(max(low, high))
    strong = (gray <= low).astype(np.uint8)
    weak = (gray <= high).astype(np.uint8)
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    if n <= 1:
        return np.zeros_like(gray)
    keep = np.zeros(n, dtype=bool)
    keep[np.unique(labels[strong.astype(bool)])] = True
    keep[0] = False
    return (keep[labels] * 255).astype(np.uint8)


def histogram(gray: np.ndarray) -> np.ndarray:
    return cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel().astype(np.int64)


METHODS = {
    "manual": manual,
    "otsu": otsu,
    "adaptive": adaptive,
    "sauvola": sauvola,
    "niblack": niblack,
    "hysteresis": hysteresis,
}


def apply(gray: np.ndarray, method: str = "otsu", **params) -> np.ndarray:
    fn = METHODS.get(method)
    if fn is None:
        raise ValueError(f"unknown threshold method {method!r}")
    return fn(gray, **params)
