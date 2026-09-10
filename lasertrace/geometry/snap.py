"""Geometry snap (Phase 5, roadmap). Recognise primitives in a fitted PathGraph
and replace organic Bezier chains with exact geometry.

Planned passes (all gated by `TraceSettings.snap`):

* `snap_lines_hv45(graph, angle_tol_deg)`: rotate Line segments whose direction
  is within tolerance of 0/45/90 degrees, keeping endpoints joined.
* `snap_circles(graph, radius_tol_mm)`: least-squares circle fit (Kasa/Pratt)
  on closed subpaths made only of cubics; if the residual is below tolerance
  emit a single `Arc` ring (DXF CIRCLE/ARC, SVG A commands).
* `snap_arcs(graph, ...)`: same per chain of consecutive cubics with consistent
  curvature (rounded-rectangle corners -> true arcs).
* `snap_rounded_rects(graph)`: detect the 4-line/4-arc pattern with equal radii
  and axis-aligned lines; regularise radii and edge lengths.
* `snap_parallel(graph)`: make near-parallel long lines exactly parallel.
* `dedupe_shapes(graph)`: hash normalised rings (translate to centroid, scale
  by sqrt(area)); replace repeats with copies of the best-fitted instance so
  identical icons/letters are byte-identical.

The current release ships without snapping; `apply_snap` is a no-op so the
pipeline and presets can already reference the settings.
"""
from __future__ import annotations

from ..models import PathGraph, SnapSettings


def apply_snap(graph: PathGraph, settings: SnapSettings) -> int:
    """Return the number of segments changed (0 until implemented)."""
    if not settings.enabled:
        return 0
    return 0
