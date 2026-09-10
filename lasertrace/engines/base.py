from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..models import PathGraph, TraceSettings, Warning


@dataclass
class EngineContext:
    px_per_mm: float
    settings: TraceSettings
    source_scale: float = 1.0   # binary px / source px (upscale ops make this > 1)
    warnings: list[Warning] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    # derived tolerances (mm) — computed once so every engine agrees
    @property
    def px_mm(self) -> float:
        """Size of one *source* pixel in mm (upscaling adds no information, so
        tolerances are floored at the original pixel size)."""
        return max(self.source_scale, 1.0) / self.px_per_mm

    @property
    def rdp_tol_mm(self) -> float:
        s = self.settings
        # detail 1.0 -> half the base tolerance, detail 0.0 -> 2x.
        # Floored at ~0.6 px: below that the tracer would chase the pixel staircase.
        # (0.75 px collapses a 45-degree pixel staircase, whose corners sit 0.71 px off the chord)
        return max(s.curve_tol_mm * (0.5 + (1.0 - s.detail) * 1.5), 0.75 * self.px_mm)

    @property
    def fit_tol_mm(self) -> float:
        s = self.settings
        return max(self.rdp_tol_mm * (0.75 + s.smoothness * 1.5), (0.8 + 0.7 * s.smoothness) * self.px_mm)

    @property
    def corner_angle_deg(self) -> float:
        # sharpness 1.0 -> 25 degrees (keep almost everything), 0.0 -> 100 degrees
        return 25.0 + (1.0 - self.settings.corner_sharpness) * 75.0

    @property
    def corner_window_mm(self) -> float:
        # must span several pixels so a diagonal staircase does not read as corners
        return max(self.settings.min_feature_mm * 2.0, self.rdp_tol_mm * 4.0, 6.0 * self.px_mm)

    def warn(self, code: str, message: str, severity: str = "warn", path_ids: list[str] | None = None) -> None:
        self.warnings.append(Warning(code=code, message=message, severity=severity, path_ids=path_ids or []))  # type: ignore[arg-type]


class Engine(Protocol):
    name: str

    def trace(self, mask: np.ndarray, ctx: EngineContext) -> PathGraph: ...

    def available(self) -> bool: ...
