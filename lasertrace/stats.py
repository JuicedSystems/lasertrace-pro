"""Stats and operator-facing warnings computed from a PathGraph."""
from __future__ import annotations

import math

import numpy as np

from .geometry.bezier import polyline_length, signed_area
from .geometry.polygons import iou, rasterize, subpath_to_ring
from .models import DepthReport, DepthSettings, Layer, PathGraph, Stats, TraceSettings, Warning
from .preprocess import morphology


def compute_stats(graph: PathGraph, binary: np.ndarray | None = None, px_per_mm: float | None = None, tol: float = 0.02) -> Stats:
    st = Stats()
    st.paths = len(graph.paths)
    st.nodes = graph.node_count()
    travel = 0.0
    prev_end = (0.0, 0.0)
    for p in graph.paths:
        for sp in p.subpaths:
            st.subpaths += 1
            if sp.closed:
                st.closed_paths += 1
            else:
                st.open_paths += 1
            if sp.is_hole:
                st.holes += 1
            ring = subpath_to_ring(sp, tol)
            if len(ring) >= 2:
                s = ring[0]
                travel += math.hypot(s[0] - prev_end[0], s[1] - prev_end[1])
                travel += polyline_length(ring, closed=sp.closed)
                prev_end = tuple(ring[0]) if sp.closed else tuple(ring[-1])
            if p.layer == Layer.ENGRAVE_FILL and sp.closed and len(ring) >= 3:
                a = abs(signed_area(ring))
                st.fill_area_mm2 += -a if sp.is_hole else a
    st.fill_area_mm2 = max(0.0, st.fill_area_mm2)
    st.bbox_mm = graph.bbox
    st.width_mm = graph.width
    st.height_mm = graph.height
    st.est_travel_mm = travel
    area_cm2 = max(0.01, graph.width * graph.height / 100.0)
    # ~200 nodes per cm2 == 100. A clean 50 mm logo lands around 5-15.
    st.complexity = float(min(100.0, st.nodes / area_cm2 * 0.5))
    if binary is not None and px_per_mm and any(p.layer == Layer.ENGRAVE_FILL for p in graph.paths):
        ys, xs = np.nonzero(binary)
        if xs.size:
            x0, y0 = xs.min(), ys.min()
            crop = binary[y0 : ys.max() + 1, x0 : xs.max() + 1]
            ras = rasterize(graph, px_per_mm, crop.shape, tol)
            st.iou_vs_binary = iou(crop, ras)
        st.holes_in_binary = morphology.count_holes(binary)
    return st


def build_warnings(graph: PathGraph, st: Stats, settings: TraceSettings, binary: np.ndarray | None, px_per_mm: float | None, engine_warnings: list[Warning]) -> list[Warning]:
    w: list[Warning] = list(engine_warnings)
    if st.paths == 0:
        w.append(Warning(code="EMPTY", message="No ink found. Check the threshold / invert.", severity="error"))
        return w

    open_fill_ids = [p.id for p in graph.paths if p.layer != Layer.ENGRAVE_LINE and any(not sp.closed for sp in p.subpaths)]
    if open_fill_ids:
        w.append(Warning(code="UNCLOSED_PATHS", message=f"{len(open_fill_ids)} fill path(s) are not closed; EZCAD fill will leak. Raise Gap close.", severity="error", path_ids=open_fill_ids))

    has_fills = any(p.layer == Layer.ENGRAVE_FILL for p in graph.paths)
    if settings.mode == "outline" and has_fills and st.holes_in_binary is not None and settings.preserve_holes and st.holes < st.holes_in_binary:
        lost = st.holes_in_binary - st.holes
        w.append(Warning(code="HOLES_LOST", message=f"{lost} hole(s)/counter(s) in the binary did not survive tracing (below Minimum feature size?).", severity="warn"))

    tiny = []
    for p in graph.paths:
        xs, ys = [], []
        for sp in p.subpaths:
            r = subpath_to_ring(sp, 0.05)
            if len(r):
                xs.extend(r[:, 0].tolist()); ys.extend(r[:, 1].tolist())
        if xs and (max(xs) - min(xs) < settings.min_feature_mm and max(ys) - min(ys) < settings.min_feature_mm):
            tiny.append(p.id)
    if tiny:
        w.append(Warning(code="TINY_PATHS", message=f"{len(tiny)} path(s) are smaller than the minimum feature ({settings.min_feature_mm} mm) and will blob.", severity="warn", path_ids=tiny))

    if binary is not None and px_per_mm and settings.mode == "outline" and st.paths:
        med, p10, p90 = morphology.stroke_width_stats(binary)
        med_mm = med / px_per_mm
        if 0 < med_mm < settings.centerline_max_width_mm and p90 / px_per_mm < settings.centerline_max_width_mm * 1.5:
            w.append(Warning(code="OUTLINE_PAIR_SUSPECT", message=f"Strokes are thin (median {med_mm:.2f} mm) and were traced as outline pairs; the laser will mark hollow tubes. Try Thin Line Art (centerline).", severity="warn"))

    if settings.node_budget and st.nodes > settings.node_budget:
        w.append(Warning(code="NODE_BUDGET_EXCEEDED", message=f"{st.nodes} nodes exceeds budget {settings.node_budget}.", severity="warn"))

    if has_fills and st.iou_vs_binary is not None and st.iou_vs_binary < 0.9:
        w.append(Warning(code="LOW_FIDELITY", message=f"Vector fill matches the binary only {st.iou_vs_binary*100:.0f}%; small text/detail likely degraded. Increase Detail or lower Smoothness.", severity="warn"))

    if st.complexity > 60:
        w.append(Warning(code="HIGH_COMPLEXITY", message=f"Complexity {st.complexity:.0f}/100: many nodes per cm2; EZCAD import will be slow. Lower Detail or set a node budget.", severity="info"))
    return w


def build_depth_warnings(report: DepthReport, hs: dict, masks: list[np.ndarray], s: DepthSettings, px_per_mm: float,
                         engine_warnings: list[Warning], hatch_capped: bool = False) -> list[Warning]:
    """Operator warnings specific to depth (3D relief) jobs."""
    import cv2
    w: list[Warning] = list(engine_warnings)
    if report.footprint_mm2 <= 0:
        w.append(Warning(code="DEPTH_EMPTY", message="No relief found after conditioning.", severity="error"))
        return w
    if hs.get("distinct", 0) < max(4, s.levels // 2):
        w.append(Warning(code="DEPTH_FEW_TONES", message=f"The height map has only {hs.get('distinct', 0)} distinct tones inside the relief; {s.levels} slices will produce terraces. Use a smoother/16-bit source or raise Smoothing.", severity="warn"))
    if hs.get("terrace_frac", 0.0) > 0.6 and s.smoothing_mm * px_per_mm < 0.8:
        w.append(Warning(code="DEPTH_TERRACED", message="Most of the relief is perfectly flat locally (posterised or 8-bit stepped source). Raise Smoothing so the slices do not stair-step.", severity="info"))
    # features narrower than their depth: the two side walls meet before the floor
    narrow = []
    th = report.slice_thickness_mm
    for i, m in enumerate(masks, start=1):
        if m.max() == 0:
            continue
        dt = cv2.distanceTransform((m > 0).astype(np.uint8), cv2.DIST_L2, 5)
        width_mm = 2.0 * float(dt.max()) / px_per_mm
        depth_mm = th * i
        if width_mm < depth_mm:
            narrow.append(i)
    if narrow:
        w.append(Warning(code="DEPTH_NARROW_FEATURES", message=f"Slices {narrow[0]}..{narrow[-1]} contain features narrower than their depth ({th * narrow[0]:.2f} mm+); side walls converge and the floor will not form. Reduce Total depth or widen the detail.", severity="warn"))
    if report.levels > 30:
        w.append(Warning(code="DEPTH_MANY_LAYERS", message=f"{report.levels} slices exceed LightBurn's 30 colour layers; use the per-slice DXF files or the height-map PNG for LightBurn.", severity="info"))
    if report.est_time_s > 2 * 3600:
        w.append(Warning(code="DEPTH_LONG_JOB", message=f"Estimated marking time ~{report.est_time_s / 3600:.1f} h at the material's starting parameters.", severity="info"))
    footprint_px = hs.get("footprint_px", 0)
    if masks and footprint_px > 0.97 * masks[0].size and s.zero_plane == "none":
        w.append(Warning(code="DEPTH_NO_ZERO_PLANE", message="The whole plate is inside slice 1: nothing was treated as untouched surface. Set Zero plane to 'border' or raise the Floor.", severity="warn"))
    if hatch_capped:
        w.append(Warning(code="DEPTH_HATCH_CAPPED", message="Hatch generation hit the line cap; deeper slices have no hatch lines. Raise max_lines or widen the spacing.", severity="warn"))
    if s.hatch.enabled and s.levels > 1 and hatch_angle_repeats(s.hatch.angle_step_deg):
        w.append(Warning(code="DEPTH_HATCH_ANGLE", message=f"Hatch angle step {s.hatch.angle_step_deg} deg repeats the same lines within a few slices and cuts grooves. Use 31-37 deg.", severity="warn"))
    return w


def hatch_angle_repeats(step_deg: float, within: int = 8) -> bool:
    """True when the hatch direction comes back to itself within `within` slices
    (0, 30, 45, 60, 90 ... all divide 180)."""
    step = step_deg % 180.0
    if step < 1e-9:
        return True
    for k in range(1, within + 1):
        if abs((k * step) % 180.0) < 1e-6 or abs((k * step) % 180.0 - 180.0) < 1e-6:
            return True
    return False
