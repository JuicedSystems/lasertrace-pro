"""Hatch (fill line) generation per depth slice.

Most galvo software hatches fills itself, but for depth work two things
matter that EZCAD2 in particular cannot do per pass on an imported layer
stack: rotate the hatch angle every pass (stacked parallel ridges at the same
angle build up into visible grooves / moire), and keep an inset contour pass
so the walls stay crisp. This module produces both as `ENGRAVE_LINE` paths
tagged with the slice's `depth_index`, so the exported DXF carries a
`DEPTH_nn_HATCH` layer next to each `DEPTH_nn` fill layer.

Algorithm: rotate the slice polygon by -angle, intersect a family of
horizontal lines at `spacing` with it (one shapely intersection per polygon,
not per line), rotate the segments back, order rows bottom-up and alternate
direction per row (serpentine = no return jump).
"""
from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

from ..geometry.polygons import path_to_polygons, polygons_of
from ..models import DepthHatchSettings, Layer, Line, Path, PathGraph, Subpath


def hatch_polygon(poly: Polygon, spacing_mm: float, angle_deg: float, bidirectional: bool = True, max_lines: int | None = None) -> list[np.ndarray]:
    """Parallel line segments (each a (2,2) array) filling `poly` at `angle_deg`.
    Rows are ordered along the hatch normal; within a row segments are ordered
    along the line; alternate rows are reversed when `bidirectional`."""
    if poly.is_empty or spacing_mm <= 0:
        return []
    rot = affinity.rotate(poly, -angle_deg, origin=(0, 0), use_radians=False)
    minx, miny, maxx, maxy = rot.bounds
    y0 = miny + spacing_mm * 0.5
    n = int(math.floor((maxy - y0) / spacing_mm)) + 1
    if n <= 0:
        return []
    if max_lines is not None and n > max_lines:
        n = max_lines
    ys = y0 + spacing_mm * np.arange(n)
    lines = MultiLineString([[(minx - 1.0, float(y)), (maxx + 1.0, float(y))] for y in ys])
    inter = rot.intersection(lines)
    segs_by_row: dict[int, list[tuple[float, float, float]]] = {}
    for ls in _linestrings(inter):
        c = np.asarray(ls.coords, dtype=np.float64)
        if len(c) < 2:
            continue
        y = float(c[0, 1])
        row = int(round((y - y0) / spacing_mm))
        xa, xb = float(c[:, 0].min()), float(c[:, 0].max())
        if xb - xa < 1e-9:
            continue
        segs_by_row.setdefault(row, []).append((xa, xb, y))
    ca, sa = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    out: list[np.ndarray] = []
    for k, row in enumerate(sorted(segs_by_row)):
        segs = sorted(segs_by_row[row])
        reverse = bidirectional and (k % 2 == 1)
        if reverse:
            segs = [(b, a, y) for a, b, y in reversed(segs)]
        for xa, xb, y in segs:
            p = np.array([[xa * ca - y * sa, xa * sa + y * ca], [xb * ca - y * sa, xb * sa + y * ca]])
            out.append(p)
    return out


def _linestrings(geom) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if hasattr(geom, "geoms"):
        res: list[LineString] = []
        for g in geom.geoms:
            res.extend(_linestrings(g))
        return res
    return []


def contour_lines(poly: Polygon, inset_mm: float) -> list[np.ndarray]:
    """Closed rings inset by `inset_mm` (one per exterior / interior boundary)."""
    if inset_mm <= 0:
        shrunk = poly
    else:
        shrunk = poly.buffer(-inset_mm, join_style=1)
    rings: list[np.ndarray] = []
    for q in polygons_of(shrunk):
        rings.append(np.asarray(q.exterior.coords, dtype=np.float64)[:-1])
        for h in q.interiors:
            rings.append(np.asarray(h.coords, dtype=np.float64)[:-1])
    return [r for r in rings if len(r) >= 3]


def slice_angle(hs: DepthHatchSettings, index: int) -> float:
    return (hs.angle_start_deg + hs.angle_step_deg * (index - 1)) % 180.0


def add_hatch_paths(graph: PathGraph, hs: DepthHatchSettings, tol_mm: float = 0.02) -> dict[int, float]:
    """Append hatch + contour ENGRAVE_LINE paths for every depth slice in
    `graph`. Returns {depth_index: total line length mm}."""
    lengths: dict[int, float] = {}
    if not hs.enabled:
        return lengths
    by_idx: dict[int, list[Polygon]] = {}
    for p in graph.paths:
        if p.depth_index is None or p.layer != Layer.ENGRAVE_FILL:
            continue
        by_idx.setdefault(p.depth_index, []).extend(path_to_polygons(p, tol_mm))
    budget = hs.max_lines
    new_paths: list[Path] = []
    for idx in sorted(by_idx):
        polys = polygons_of(unary_union(by_idx[idx]))
        angle = slice_angle(hs, idx)
        segs: list[Subpath] = []
        total = 0.0
        for poly in polys:
            hatch_poly = poly.buffer(-hs.spacing_mm * 0.5, join_style=1) if hs.contour_pass else poly
            for q in polygons_of(hatch_poly):
                for seg in hatch_polygon(q, hs.spacing_mm, angle, hs.bidirectional, max_lines=max(0, budget)):
                    segs.append(Subpath(segments=[Line(p1=(float(seg[0, 0]), float(seg[0, 1])), p2=(float(seg[1, 0]), float(seg[1, 1])))], closed=False))
                    total += float(np.hypot(*(seg[1] - seg[0])))
                    budget -= 1
                    if budget <= 0:
                        break
                if budget <= 0:
                    break
            if hs.contour_pass:
                for ring in contour_lines(poly, hs.spacing_mm * 0.5):
                    lines = [Line(p1=(float(ring[k][0]), float(ring[k][1])), p2=(float(ring[(k + 1) % len(ring)][0]), float(ring[(k + 1) % len(ring)][1]))) for k in range(len(ring))]
                    segs.append(Subpath(segments=lines, closed=True))
                    total += float(np.sum(np.hypot(*(np.roll(ring, -1, axis=0) - ring).T)))
            if budget <= 0:
                break
        if segs:
            new_paths.append(Path(subpaths=segs, layer=Layer.ENGRAVE_LINE, depth_index=idx, stroke_width_mm=hs.spacing_mm))
            lengths[idx] = total
        if budget <= 0:
            break
    graph.paths.extend(new_paths)
    return lengths
