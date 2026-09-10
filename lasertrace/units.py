"""Units and coordinate transforms.

Pixel space: y-down, origin top-left of the image.
Working space: millimetres, y-down, origin top-left of the traced bbox.
DXF space: millimetres, y-up, origin bottom-left  (y' = H - y).
"""
from __future__ import annotations

from dataclasses import dataclass

MM_PER_INCH = 25.4


@dataclass(frozen=True)
class Transform:
    """Affine transform: out = (x * sx + tx, y * sy + ty)."""
    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] * self.sx + self.tx, p[1] * self.sy + self.ty)

    def scale_len(self, v: float) -> float:
        return v * abs(self.sx)

    @staticmethod
    def px_to_mm(px_per_mm: float, bbox_px: tuple[float, float, float, float]) -> "Transform":
        """Map pixel coordinates to mm with the bbox top-left at (0,0)."""
        s = 1.0 / px_per_mm
        return Transform(s, s, -bbox_px[0] * s, -bbox_px[1] * s)

    @staticmethod
    def y_flip(height: float) -> "Transform":
        return Transform(1.0, -1.0, 0.0, height)


def px_per_mm_for_width(bbox_w_px: float, target_w_mm: float) -> float:
    if target_w_mm <= 0 or bbox_w_px <= 0:
        raise ValueError("width must be positive")
    return bbox_w_px / target_w_mm


def px_per_mm_from_dpi(dpi: float) -> float:
    return dpi / MM_PER_INCH


def mm_to_px(mm: float, px_per_mm: float) -> float:
    return mm * px_per_mm


def px_to_mm(px: float, px_per_mm: float) -> float:
    return px / px_per_mm


def mm2_to_px2(mm2: float, px_per_mm: float) -> float:
    return mm2 * px_per_mm * px_per_mm
