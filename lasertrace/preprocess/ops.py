"""Preprocess op registry and stack runner.

Ops fall into three stages, and the runner keeps them in a legal order even if
the user reorders within a stage:

  color/gray stage : crop, rotate, deskew, background_*, upscale, denoise,
                     bilateral, unsharp, levels, gamma, quantize
  threshold stage  : threshold (gray -> mask 255=ink)
  binary stage     : invert, knockout_halo, morph_*, dilate, erode, despeckle,
                     fill_holes

Each op is `fn(image, ctx, **params) -> image`. The runner tracks whether the
current buffer is color, gray, or binary and converts where required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from ..ingest import to_gray
from ..models import PreprocessStack
from . import enhance, morphology, threshold


@dataclass
class OpContext:
    px_per_mm: Optional[float] = None
    gray_source: Optional[np.ndarray] = None   # gray image just before threshold (for halo knockout)
    quant_labels: Optional[np.ndarray] = None
    quant_palette: Optional[np.ndarray] = None
    notes: list[str] = field(default_factory=list)


def _gray(img: np.ndarray) -> np.ndarray:
    return to_gray(img) if img.ndim == 3 else img


# ----------------------------------------------------------------------------- #
# op implementations
# ----------------------------------------------------------------------------- #
def op_threshold(img: np.ndarray, ctx: OpContext, method: str = "otsu", **params) -> np.ndarray:
    g = _gray(img)
    ctx.gray_source = g
    return threshold.apply(g, method, **params)


def op_quantize(img: np.ndarray, ctx: OpContext, colors: int = 4, ink_indices: Optional[list[int]] = None, seed: int = 0) -> np.ndarray:
    q, labels, palette = enhance.quantize(img, colors, seed)
    ctx.quant_labels = labels
    ctx.quant_palette = palette
    if ink_indices:
        # user picked which palette entries are ink: produce a gray image where
        # ink = 0, else 255 so a following manual threshold works trivially
        ink = np.isin(labels, list(ink_indices))
        return np.where(ink, 0, 255).astype(np.uint8)
    return q


def op_invert(img: np.ndarray, ctx: OpContext) -> np.ndarray:
    return morphology.invert(img) if img.ndim == 2 else (255 - img)


def op_knockout_halo(img: np.ndarray, ctx: OpContext, band_px: int = 2, halo_max_gray: int = 235) -> np.ndarray:
    if ctx.gray_source is None or ctx.gray_source.shape != img.shape:
        return img
    return morphology.knockout_halo(ctx.gray_source, img, band_px, halo_max_gray)


def _mm_aware(fn):
    def wrapper(img: np.ndarray, ctx: OpContext, **params):
        return fn(img, px_per_mm=ctx.px_per_mm, **params)
    return wrapper


def _no_ctx(fn):
    def wrapper(img: np.ndarray, ctx: OpContext, **params):
        return fn(img, **params)
    return wrapper


OP_STAGE: dict[str, str] = {
    "crop": "color", "rotate": "color", "deskew": "color",
    "background_white": "color", "background_sample": "color",
    "upscale": "color", "denoise": "color", "bilateral": "color", "unsharp": "color",
    "levels": "color", "gamma": "color", "quantize": "color",
    "threshold": "threshold",
    "invert": "binary", "knockout_halo": "binary",
    "morph_open": "binary", "morph_close": "binary", "dilate": "binary", "erode": "binary",
    "despeckle": "binary", "fill_holes": "binary",
}

OP_REGISTRY: dict[str, Callable] = {
    "crop": _no_ctx(enhance.crop),
    "rotate": _no_ctx(enhance.rotate),
    "deskew": _no_ctx(enhance.deskew),
    "background_white": _no_ctx(enhance.background_white),
    "background_sample": _no_ctx(enhance.background_sample),
    "upscale": _no_ctx(enhance.upscale),
    "denoise": _no_ctx(enhance.denoise),
    "bilateral": _no_ctx(enhance.bilateral),
    "unsharp": _no_ctx(enhance.unsharp),
    "levels": _no_ctx(enhance.levels),
    "gamma": _no_ctx(enhance.gamma),
    "quantize": op_quantize,
    "threshold": op_threshold,
    "invert": op_invert,
    "knockout_halo": op_knockout_halo,
    "morph_open": _mm_aware(morphology.morph_open),
    "morph_close": _mm_aware(morphology.morph_close),
    "dilate": _mm_aware(morphology.dilate),
    "erode": _mm_aware(morphology.erode),
    "despeckle": _mm_aware(morphology.despeckle),
    "fill_holes": _mm_aware(morphology.fill_holes),
}

# Human names for the UI (shop language)
OP_LABELS: dict[str, str] = {
    "crop": "Crop",
    "rotate": "Rotate",
    "deskew": "Straighten (deskew)",
    "background_white": "Flatten white background",
    "background_sample": "Remove background color",
    "upscale": "Upscale tiny logo",
    "denoise": "Denoise",
    "bilateral": "Kill JPEG blocks",
    "unsharp": "Sharpen soft scan",
    "levels": "Levels / contrast",
    "gamma": "Gamma",
    "quantize": "Reduce to few colors",
    "threshold": "Black / white threshold",
    "invert": "Invert",
    "knockout_halo": "Knock out gray halo",
    "morph_open": "Remove thin fuzz (open)",
    "morph_close": "Reconnect breaks (close)",
    "dilate": "Bolden",
    "erode": "Thin",
    "despeckle": "Despeckle",
    "fill_holes": "Fill pinholes",
}

_STAGE_ORDER = {"color": 0, "threshold": 1, "binary": 2}


def _ordered(stack: PreprocessStack):
    ops = stack.enabled_ops()
    # stable sort by stage keeps user order inside a stage
    return sorted(ops, key=lambda o: _STAGE_ORDER[OP_STAGE[o.op]])


def run_stack_with_intermediates(rgb: np.ndarray, stack: PreprocessStack, px_per_mm: Optional[float] = None) -> tuple[np.ndarray, list[tuple[str, np.ndarray]], OpContext]:
    """Run the stack, returning (binary mask, [(op_name, image_after_op)], ctx).

    If the stack has no threshold op, Otsu is applied automatically at the end
    of the color stage so the output is always a binary mask (255 = ink).
    """
    ctx = OpContext(px_per_mm=px_per_mm)
    img = rgb
    steps: list[tuple[str, np.ndarray]] = []
    has_threshold = False
    for op in _ordered(stack):
        stage = OP_STAGE[op.op]
        if stage == "binary" and not has_threshold:
            img = op_threshold(img, ctx, method="otsu")
            has_threshold = True
            steps.append(("threshold(auto-otsu)", img))
        if stage == "threshold":
            has_threshold = True
        fn = OP_REGISTRY[op.op]
        img = fn(img, ctx, **op.params)
        steps.append((op.op, img))
    if not has_threshold:
        img = op_threshold(img, ctx, method="otsu")
        steps.append(("threshold(auto-otsu)", img))
    if img.ndim == 3:  # should not happen, but be safe
        img = threshold.otsu(_gray(img))
    return img, steps, ctx


def run_stack(rgb: np.ndarray, stack: PreprocessStack, px_per_mm: Optional[float] = None) -> np.ndarray:
    binary, _, _ = run_stack_with_intermediates(rgb, stack, px_per_mm)
    return binary


def gray_before_threshold(rgb: np.ndarray, stack: PreprocessStack, px_per_mm: Optional[float] = None) -> np.ndarray:
    """Return the gray image as it enters the threshold op (for histogram UI)."""
    ctx = OpContext(px_per_mm=px_per_mm)
    img = rgb
    for op in _ordered(stack):
        if OP_STAGE[op.op] != "color":
            break
        img = OP_REGISTRY[op.op](img, ctx, **op.params)
    return _gray(img)
