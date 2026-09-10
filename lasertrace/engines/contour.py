"""In-house permissive outline/region engine.

mask (255 = ink) -> exact pixel-edge planar polygons (shapely, unioned so no
region overlaps another) -> mm -> corner detection on the dense ring ->
straight-run detection (lines, least-squares snapped to the pixel edges) ->
Schneider cubic fit of the curved stretches (on the midpoint-smoothed ring so
the stair steps do not distort the parametrisation) -> ring validity check
with tighter retries -> PathGraph.

Holes come straight from the polygon interiors, so counters in A/B/O/8 are
never dropped: if the binary has a hole, the path has a hole.
"""
from __future__ import annotations

import time

import numpy as np
from shapely.geometry import Polygon

from ..geometry.bezier import (_tangent_at, _unit, cubic_is_line, dedupe_consecutive, detect_corners, fit_cubics,
                               merge_collinear, rdp)
from ..geometry.polygons import contours_to_polygons, polygon_to_subpath_rings, subpath_to_ring
from ..models import Cubic, Layer, Line, Path, PathGraph, Subpath
from .base import EngineContext


def _ring_px_to_mm(ring: np.ndarray, px_per_mm: float, origin_px: tuple[float, float]) -> np.ndarray:
    return (ring - np.array(origin_px, dtype=np.float64)) / px_per_mm


def _pieces(ring: np.ndarray, corners: np.ndarray) -> list[np.ndarray]:
    """Split a closed ring at corner indices into open pieces (each includes both
    corner endpoints). With no corners the single piece is the closed ring with
    its first point repeated at the end."""
    if corners.size == 0:
        return [np.vstack([ring, ring[:1]])]
    k = len(corners)
    out = []
    for i in range(k):
        a, b = int(corners[i]), int(corners[(i + 1) % k])
        piece = ring[a : b + 1] if b > a else np.vstack([ring[a:], ring[: b + 1]])
        if len(piece) >= 2:
            out.append(piece)
    return out


def _line(p, q) -> Line:
    return Line(p1=(float(p[0]), float(p[1])), p2=(float(q[0]), float(q[1])))


def _polyline_segments(pieces: list[np.ndarray], tol: float) -> list:
    segs: list = []
    for piece in pieces:
        simp = merge_collinear(rdp(piece, tol, closed=False), 0.25, closed=False)
        for i in range(len(simp) - 1):
            segs.append(_line(simp[i], simp[i + 1]))
    return segs


def _emit_curves(curves, fit_tol: float, segs: list) -> None:
    for bez in curves:
        p0, c1, c2, p3 = bez
        if cubic_is_line(p0, c1, c2, p3, fit_tol):
            segs.append(_line(p0, p3))
        else:
            segs.append(Cubic(p0=(float(p0[0]), float(p0[1])), c1=(float(c1[0]), float(c1[1])),
                              c2=(float(c2[0]), float(c2[1])), p3=(float(p3[0]), float(p3[1]))))


def _midpoint_smooth(piece: np.ndarray, clean_tol: float) -> np.ndarray:
    """Replace a rectilinear staircase by the polyline through its edge midpoints
    (unbiased: midpoints of stair edges sit on the true boundary), then drop
    collinear points. Endpoints of an open piece are kept so corners survive;
    a closed piece (first == last) stays closed."""
    n = len(piece)
    if n < 4:
        return piece
    closed = bool(np.allclose(piece[0], piece[-1]))
    mids = (piece[:-1] + piece[1:]) * 0.5
    if closed:
        out = np.vstack([mids, mids[:1]])
    else:
        out = np.vstack([piece[:1], mids, piece[-1:]])
    return rdp(out, clean_tol, closed=False)


def _straight_runs(piece: np.ndarray, tol: float, min_len: float) -> list[tuple[int, int]]:
    """Find stretches of the dense piece that are straight within tol and at
    least min_len long. Returns (start_idx, end_idx) pairs into piece.

    `tol` must exceed the pixel staircase (>= 1.5 px) so RDP chord lengths
    reflect curvature. A genuine straight edge is much longer than the chords a
    curve produces at that tolerance: compare against the piece's median chord
    (greedy RDP makes neighbouring chords too variable to compare pairwise). On
    a circle every chord is about the median and none qualifies; on a rounded
    rectangle the four edges dwarf the corner chords."""
    n = len(piece)
    if n < 3:
        return []
    simp = rdp(piece, tol, closed=False)
    idx = []
    j = 0
    for q in simp:
        while j < n and not (abs(piece[j][0] - q[0]) < 1e-9 and abs(piece[j][1] - q[1]) < 1e-9):
            j += 1
        idx.append(min(j, n - 1))
    chords = [(a, b, float(np.hypot(*(piece[b] - piece[a])))) for a, b in zip(idx[:-1], idx[1:]) if b > a]
    if not chords:
        return []
    lengths = np.array([c[2] for c in chords])
    reference = float(np.median(lengths)) if len(chords) >= 3 else 0.0
    runs = []
    for i, (a, b, L) in enumerate(chords):
        if L < min_len:
            continue
        if reference > 0 and L < 2.5 * reference:
            continue
        if len(chords) < 3:
            other = max((c[2] for j, c in enumerate(chords) if j != i), default=0.0)
            if other > 0 and L < 2.5 * other:
                continue
        runs.append((a, b))
    merged: list[tuple[int, int]] = []
    for a, b in runs:
        if merged and merged[-1][1] == a:
            pa, pb = merged[-1]
            d0 = _unit(piece[pb] - piece[pa])
            d1 = _unit(piece[b] - piece[a])
            if float(d0 @ d1) > 0.9998:
                merged[-1] = (pa, b)
                continue
        merged.append((a, b))
    return merged


def _fit_line_endpoints(piece: np.ndarray, a: int, b: int) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares line through the dense points piece[a..b]; return the
    projections of the run endpoints onto it. On an axis-aligned pixel edge
    this lands exactly on the edge."""
    pts = piece[a : b + 1]
    if len(pts) < 3:
        return piece[a].copy(), piece[b].copy()
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    if float(d @ (piece[b] - piece[a])) < 0:
        d = -d
    pa = c + d * float((piece[a] - c) @ d)
    pb = c + d * float((piece[b] - c) @ d)
    return pa, pb


def _fit_stretch(dense: np.ndarray, smooth_tol: float, fit_tol: float, t_start, t_end, p_start, p_end, segs: list) -> None:
    """Fit one curved stretch of the dense piece with cubics, after midpoint
    smoothing and pinning the endpoints to the adjoining line endpoints."""
    sub = _midpoint_smooth(dense, smooth_tol).copy()
    if p_start is not None:
        sub[0] = p_start
    if p_end is not None:
        sub[-1] = p_end
    if len(sub) >= 2:
        _emit_curves(fit_cubics(sub, fit_tol, t_start, t_end), fit_tol, segs)


def _curve_segments(pieces: list[np.ndarray], fit_tol: float, closed_smooth: bool, straight_min_len: float, run_tol: float, smooth_tol: float) -> list:
    """Fit every piece: straight runs become Lines snapped to the pixel edges;
    the curved stretches between them get cubic fits whose end tangents match
    the adjoining lines, so a rounded rectangle is 4 lines + 4 arcs."""
    segs: list = []
    reach = fit_tol * 4.0
    for piece in pieces:
        runs = _straight_runs(piece, run_tol, straight_min_len)
        if not runs:
            sm = _midpoint_smooth(piece, smooth_tol)
            if closed_smooth:
                fwd = _tangent_at(sm, 0, +1, reach)
                bwd = _tangent_at(sm[:-1][::-1], 0, +1, reach)
                t = _unit(fwd - bwd)
                _emit_curves(fit_cubics(sm, fit_tol, t, -t), fit_tol, segs)
            else:
                _emit_curves(fit_cubics(sm, fit_tol), fit_tol, segs)
            continue

        if closed_smooth:
            # rotate the closed piece so it starts at the beginning of a straight run
            a0 = runs[0][0]
            ring = piece[:-1]
            piece = np.vstack([np.roll(ring, -a0, axis=0), ring[a0 : a0 + 1]])
            runs = _straight_runs(piece, run_tol, straight_min_len)
            if not runs:
                _emit_curves(fit_cubics(_midpoint_smooth(piece, smooth_tol), fit_tol), fit_tol, segs)
                continue

        n = len(piece)
        fitted = [_fit_line_endpoints(piece, a, b) for a, b in runs]
        cursor = 0
        prev_dir = None
        prev_pt = None
        for k, (a, b) in enumerate(runs):
            pa, pb = fitted[k]
            if a > cursor:
                _fit_stretch(piece[cursor : a + 1], smooth_tol, fit_tol, prev_dir, -_unit(pb - pa), prev_pt, pa, segs)
            elif prev_pt is not None and float(np.hypot(*(pa - prev_pt))) > 1e-9:
                segs.append(_line(prev_pt, pa))
            segs.append(_line(pa, pb))
            prev_dir = _unit(pb - pa)
            prev_pt = pb
            cursor = b
        if cursor < n - 1:
            p_end = None
            t_end = None
            if closed_smooth:
                pa0, pb0 = fitted[0]
                p_end, t_end = pa0, -_unit(pb0 - pa0)
            _fit_stretch(piece[cursor:], smooth_tol, fit_tol, prev_dir, t_end, prev_pt, p_end, segs)
        elif closed_smooth:
            pa0, _ = fitted[0]
            if prev_pt is not None and float(np.hypot(*(pa0 - prev_pt))) > 1e-9:
                segs.append(_line(prev_pt, pa0))
    return segs


def _ring_ok(segments: list, tol: float) -> bool:
    sp = Subpath(segments=segments, closed=True)
    ring = subpath_to_ring(sp, tol * 0.5)
    if len(ring) < 3:
        return False
    try:
        return Polygon(ring).is_valid
    except Exception:
        return False


def ring_to_subpath(ring_mm: np.ndarray, ctx: EngineContext, is_hole: bool) -> Subpath | None:
    """Corner-detect + fit one closed ring (mm) into a Subpath."""
    s = ctx.settings
    ring = dedupe_consecutive(np.asarray(ring_mm, dtype=np.float64))
    if len(ring) >= 2 and np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    if len(ring) < 3:
        return None

    corners = detect_corners(ring, ctx.corner_window_mm, ctx.corner_angle_deg, closed=True)
    pieces = _pieces(ring, corners)

    if s.smoothness <= 0.0:
        segments = _polyline_segments(pieces, ctx.rdp_tol_mm)
        return Subpath(segments=segments, closed=True, is_hole=is_hole) if len(segments) >= 2 else None

    closed_smooth = corners.size == 0
    straight_min = max(ctx.corner_window_mm * 1.5, ctx.fit_tol_mm * 8.0)
    run_tol = max(ctx.fit_tol_mm * 0.8, 1.5 * ctx.px_mm)
    smooth_tol = 0.3 * ctx.px_mm
    segments: list = []
    for factor in (1.0, 0.6, 0.35):
        segments = _curve_segments(pieces, ctx.fit_tol_mm * factor, closed_smooth, straight_min, run_tol, smooth_tol)
        if segments and _ring_ok(segments, ctx.fit_tol_mm):
            break
    else:
        segments = _polyline_segments(pieces, ctx.rdp_tol_mm)
    if len(segments) < 2:
        return None
    return Subpath(segments=segments, closed=True, is_hole=is_hole)


class ContourEngine:
    name = "contour"

    def available(self) -> bool:
        return True

    def trace(self, mask: np.ndarray, ctx: EngineContext) -> PathGraph:
        t0 = time.perf_counter()
        polys = contours_to_polygons(mask, pixel_edge=True)
        ctx.timings_ms["contours"] = (time.perf_counter() - t0) * 1000

        if not polys:
            return PathGraph(paths=[], units="mm", bbox=(0, 0, 0, 0), px_per_mm=ctx.px_per_mm)

        minx = min(p.bounds[0] for p in polys)
        miny = min(p.bounds[1] for p in polys)
        maxx = max(p.bounds[2] for p in polys)
        maxy = max(p.bounds[3] for p in polys)
        origin = (minx, miny)
        ppm = ctx.px_per_mm

        t1 = time.perf_counter()
        paths: list[Path] = []
        for poly in polys:
            subpaths: list[Subpath] = []
            for ring, is_hole in polygon_to_subpath_rings(poly):
                sp = ring_to_subpath(_ring_px_to_mm(ring, ppm, origin), ctx, is_hole)
                if sp is not None:
                    subpaths.append(sp)
            if not subpaths or subpaths[0].is_hole:
                continue
            paths.append(Path(subpaths=subpaths, layer=Layer.ENGRAVE_FILL, fill_rule="evenodd"))
        ctx.timings_ms["fit"] = (time.perf_counter() - t1) * 1000

        bbox = (0.0, 0.0, (maxx - minx) / ppm, (maxy - miny) / ppm)
        return PathGraph(paths=paths, units="mm", bbox=bbox, px_per_mm=ppm)
