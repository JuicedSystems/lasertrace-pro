from __future__ import annotations

import math
from typing import Callable

import numpy as np

from ..models import Arc, Cubic, Line, Path, PathGraph, Subpath
from ..units import Transform

Pt = tuple[float, float]


def _apply_pt(t: Transform, p: Pt) -> Pt:
    return t.apply(p)


def transform_segment(seg, t: Transform):
    if isinstance(seg, Line):
        return Line(p1=_apply_pt(t, seg.p1), p2=_apply_pt(t, seg.p2))
    if isinstance(seg, Cubic):
        return Cubic(p0=_apply_pt(t, seg.p0), c1=_apply_pt(t, seg.c1), c2=_apply_pt(t, seg.c2), p3=_apply_pt(t, seg.p3))
    if isinstance(seg, Arc):
        c = _apply_pt(t, seg.center)
        r = seg.r * abs(t.sx)
        if t.sy < 0:  # mirror flips angle direction
            return Arc(center=c, r=r, a0=-seg.a0, a1=-seg.a1, ccw=not seg.ccw)
        return Arc(center=c, r=r, a0=seg.a0, a1=seg.a1, ccw=seg.ccw)
    raise TypeError(seg)


def transform_graph(graph: PathGraph, t: Transform) -> PathGraph:
    paths = []
    for p in graph.paths:
        sps = [Subpath(segments=[transform_segment(s, t) for s in sp.segments], closed=sp.closed, is_hole=sp.is_hole) for sp in p.subpaths]
        paths.append(Path(id=p.id, subpaths=sps, layer=p.layer, fill_rule=p.fill_rule,
                          stroke_width_mm=(p.stroke_width_mm * abs(t.sx)) if p.stroke_width_mm else None,
                          depth_index=p.depth_index))
    x0, y0 = t.apply((graph.bbox[0], graph.bbox[1]))
    x1, y1 = t.apply((graph.bbox[2], graph.bbox[3]))
    bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    ppm = graph.px_per_mm / abs(t.sx) if graph.px_per_mm else None
    return PathGraph(paths=paths, units=graph.units, bbox=bbox, px_per_mm=ppm)


def scale_graph(graph: PathGraph, width_mm: float | None, height_mm: float | None) -> PathGraph:
    """Uniformly (or non-uniformly if both given) scale so the bbox matches."""
    w, h = graph.width, graph.height
    if w <= 0 or h <= 0:
        return graph
    if width_mm and height_mm:
        sx, sy = width_mm / w, height_mm / h
    elif width_mm:
        sx = sy = width_mm / w
    elif height_mm:
        sx = sy = height_mm / h
    else:
        return graph
    if abs(sx - 1) < 1e-12 and abs(sy - 1) < 1e-12:
        return graph
    return transform_graph(graph, Transform(sx, sy, -graph.bbox[0] * sx, -graph.bbox[1] * sy))


def merge_collinear_lines(graph: PathGraph, angle_tol_deg: float = 0.5) -> int:
    """Merge runs of consecutive Line segments that are collinear. Returns nodes removed."""
    cos_tol = math.cos(math.radians(angle_tol_deg))
    removed = 0
    for p in graph.paths:
        for sp in p.subpaths:
            segs = sp.segments
            if len(segs) < 2:
                continue
            out: list = []
            for seg in segs:
                if out and isinstance(seg, Line) and isinstance(out[-1], Line):
                    a = np.array(out[-1].p2) - np.array(out[-1].p1)
                    b = np.array(seg.p2) - np.array(seg.p1)
                    la, lb = np.hypot(*a), np.hypot(*b)
                    if la > 1e-12 and lb > 1e-12 and float(a @ b) / (la * lb) >= cos_tol:
                        out[-1] = Line(p1=out[-1].p1, p2=seg.p2)
                        removed += 1
                        continue
                out.append(seg)
            # closed ring: check wrap-around merge
            if sp.closed and len(out) >= 3 and isinstance(out[0], Line) and isinstance(out[-1], Line):
                a = np.array(out[-1].p2) - np.array(out[-1].p1)
                b = np.array(out[0].p2) - np.array(out[0].p1)
                la, lb = np.hypot(*a), np.hypot(*b)
                if la > 1e-12 and lb > 1e-12 and float(a @ b) / (la * lb) >= cos_tol:
                    out[0] = Line(p1=out[-1].p1, p2=out[0].p2)
                    out.pop()
                    removed += 1
            sp.segments = out
    return removed


def round_graph(graph: PathGraph, decimals: int = 4) -> None:
    """Round every coordinate in place (keeps files small and output deterministic)."""
    def r(p: Pt) -> Pt:
        return (round(p[0], decimals), round(p[1], decimals))
    for p in graph.paths:
        for sp in p.subpaths:
            for i, s in enumerate(sp.segments):
                if isinstance(s, Line):
                    sp.segments[i] = Line(p1=r(s.p1), p2=r(s.p2))
                elif isinstance(s, Cubic):
                    sp.segments[i] = Cubic(p0=r(s.p0), c1=r(s.c1), c2=r(s.c2), p3=r(s.p3))
