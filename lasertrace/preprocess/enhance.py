"""Grayscale/color enhancement ops that run before thresholding.

All functions are pure: ndarray in, ndarray out. Gray images are uint8 HxW,
color images uint8 HxWx3.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _is_color(img: np.ndarray) -> bool:
    return img.ndim == 3


def crop(img: np.ndarray, x0: float = 0, y0: float = 0, x1: float = 1, y1: float = 1, relative: bool = True) -> np.ndarray:
    h, w = img.shape[:2]
    if relative:
        x0, x1 = int(x0 * w), int(x1 * w)
        y0, y1 = int(y0 * h), int(y1 * h)
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 <= x0 or y1 <= y0:
        return img
    return img[y0:y1, x0:x1].copy()


def rotate(img: np.ndarray, angle_deg: float = 0.0, expand: bool = True, fill: int = 255) -> np.ndarray:
    if abs(angle_deg) < 1e-9:
        return img
    if abs(angle_deg % 90) < 1e-9:
        k = int(round(angle_deg / 90)) % 4
        return np.ascontiguousarray(np.rot90(img, -k))
    h, w = img.shape[:2]
    c = (w / 2.0, h / 2.0)
    m = cv2.getRotationMatrix2D(c, -angle_deg, 1.0)
    if expand:
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
        m[0, 2] += nw / 2 - c[0]
        m[1, 2] += nh / 2 - c[1]
        w, h = nw, nh
    border = (fill, fill, fill) if _is_color(img) else fill
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def estimate_skew_deg(gray: np.ndarray, max_deg: float = 15.0) -> float:
    """Estimate dominant text/line skew using Hough lines on edges. Returns degrees."""
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=80, minLineLength=max(20, gray.shape[1] // 10), maxLineGap=10)
    if lines is None:
        return 0.0
    angles = []
    weights = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # fold to [-45, 45]
        while a > 45:
            a -= 90
        while a < -45:
            a += 90
        if abs(a) <= max_deg:
            angles.append(a)
            weights.append(math.hypot(x2 - x1, y2 - y1))
    if not angles:
        return 0.0
    angles_a = np.array(angles)
    weights_a = np.array(weights)
    # weighted median
    order = np.argsort(angles_a)
    cw = np.cumsum(weights_a[order])
    idx = np.searchsorted(cw, cw[-1] / 2)
    return float(angles_a[order][min(idx, len(order) - 1)])


def deskew(img: np.ndarray, max_deg: float = 15.0) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if _is_color(img) else img
    a = estimate_skew_deg(gray, max_deg)
    if abs(a) < 0.1:
        return img
    return rotate(img, a, expand=True)


def background_white(img: np.ndarray, tolerance: int = 40) -> np.ndarray:
    """Flatten near-white background to pure white (kills paper texture)."""
    if _is_color(img):
        near = np.all(img >= 255 - tolerance, axis=2)
        out = img.copy()
        out[near] = 255
        return out
    out = img.copy()
    out[out >= 255 - tolerance] = 255
    return out


def background_sample(img: np.ndarray, tolerance: int = 40, corner_px: int = 8) -> np.ndarray:
    """Sample the four corners for the background color and push pixels within
    `tolerance` (L2 in RGB) to white."""
    h, w = img.shape[:2]
    c = max(1, min(corner_px, h // 4, w // 4))
    if _is_color(img):
        patches = np.concatenate([
            img[:c, :c].reshape(-1, 3), img[:c, -c:].reshape(-1, 3),
            img[-c:, :c].reshape(-1, 3), img[-c:, -c:].reshape(-1, 3)])
        bg = np.median(patches, axis=0)
        d = np.sqrt(np.sum((img.astype(np.float32) - bg.astype(np.float32)) ** 2, axis=2))
        out = img.copy()
        out[d <= tolerance] = 255
        return out
    patches = np.concatenate([img[:c, :c].ravel(), img[:c, -c:].ravel(), img[-c:, :c].ravel(), img[-c:, -c:].ravel()])
    bg = float(np.median(patches))
    out = img.copy()
    out[np.abs(img.astype(np.float32) - bg) <= tolerance] = 255
    return out


def upscale(img: np.ndarray, factor: float = 2.0, method: str = "lanczos", max_px: int = 4000) -> np.ndarray:
    """Classical upscale for tiny logos. Never invents detail; just gives the
    tracer sub-pixel room. Capped so a huge input is not exploded."""
    h, w = img.shape[:2]
    factor = float(factor)
    if factor <= 1.0:
        return img
    if max(h, w) * factor > max_px:
        factor = max_px / max(h, w)
        if factor <= 1.0:
            return img
    interp = {"lanczos": cv2.INTER_LANCZOS4, "cubic": cv2.INTER_CUBIC, "nearest": cv2.INTER_NEAREST}.get(method, cv2.INTER_LANCZOS4)
    return cv2.resize(img, (int(round(w * factor)), int(round(h * factor))), interpolation=interp)


def denoise(img: np.ndarray, strength: float = 10.0) -> np.ndarray:
    """Non-local-means denoise. Works in grayscale (3x faster than the colour
    variant and the pipeline binarizes on luma anyway)."""
    strength = float(strength)
    if strength <= 0:
        return img
    if _is_color(img):
        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return cv2.fastNlMeansDenoising(img, None, strength, 7, 21)


def bilateral(img: np.ndarray, d: int = 7, sigma_color: float = 50.0, sigma_space: float = 5.0) -> np.ndarray:
    """Edge-preserving smoothing: kills JPEG blocks while keeping edges."""
    return cv2.bilateralFilter(img, int(d), float(sigma_color), float(sigma_space))


def median(img: np.ndarray, ksize: int = 3) -> np.ndarray:
    k = int(ksize)
    if k % 2 == 0:
        k += 1
    return cv2.medianBlur(img, max(3, k))


def unsharp(img: np.ndarray, amount: float = 1.0, radius: float = 1.5) -> np.ndarray:
    if amount <= 0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), float(radius))
    return cv2.addWeighted(img, 1.0 + float(amount), blur, -float(amount), 0)


def levels(img: np.ndarray, black: int = 0, white: int = 255, gamma: float = 1.0) -> np.ndarray:
    black, white = int(black), int(white)
    if white <= black:
        white = black + 1
    lut = np.arange(256, dtype=np.float32)
    lut = np.clip((lut - black) / (white - black), 0, 1)
    if gamma and gamma != 1.0:
        lut = lut ** (1.0 / float(gamma))
    lut = (lut * 255 + 0.5).astype(np.uint8)
    return cv2.LUT(img, lut)


def gamma(img: np.ndarray, value: float = 1.0) -> np.ndarray:
    return levels(img, 0, 255, value)


def flatten_lighting(gray: np.ndarray, sigma: float = 40.0) -> np.ndarray:
    """Divide by a heavy blur to remove uneven illumination (phone photos)."""
    g = gray.astype(np.float32) + 1.0
    bg = cv2.GaussianBlur(g, (0, 0), float(sigma)) + 1.0
    out = g / bg * 200.0
    return np.clip(out, 0, 255).astype(np.uint8)


def quantize(img: np.ndarray, colors: int = 4, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """K-means palette quantize. Returns (quantized RGB, labels HxW, palette Kx3).
    Deterministic via fixed seed and KMEANS_PP_CENTERS."""
    colors = max(2, min(8, int(colors)))
    data = img.reshape(-1, 3).astype(np.float32) if _is_color(img) else np.repeat(img.reshape(-1, 1), 3, axis=1).astype(np.float32)
    cv2.setRNGSeed(int(seed))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(data, colors, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    centers = np.clip(centers + 0.5, 0, 255).astype(np.uint8)
    labels = labels.ravel().reshape(img.shape[:2])
    q = centers[labels]
    return q, labels, centers
