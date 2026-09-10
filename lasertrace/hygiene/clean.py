from __future__ import annotations

import hashlib
import math

import numpy as np

from ..geometry.bezier import polyline_length, signed_area
from ..geometry.polygons import subpath_to_ring
from ..models import Layer, Line, Path, PathGraph, Subpath


def _sp_area(sp: Subpath, tol: float) -> float:
    return abs(signed_area(subpath_to_ring(sp, tol)))


def _sp_length(sp: Subpath, tol: float) -> float:
    return polyline_length(subpath_to_ring(sp, tol), closed=sp.closed)


def remove_micro_paths(graph: PathGraph, min_area_mm2: float, min_len_mm: float, tol: float = 0.02) -> tuple[int, list[str]]:
    """Drop paths whose *outer* area (fills) or length (lines) is below the
    laser's minimum feature. Holes smaller than min_area are removed from their
    parent too (they would fill in on the metal anyway). Returns (removed, ids)."""
    kept: list[Path] = []
    removed = 0
    removed_ids: list[str] = []
    for p in graph.paths:
        if p.layer == Layer.ENGRAVE_LINE or not p.subpaths[0].closed if p.subpaths else True:
            L = sum(_sp_length(sp, tol) for sp in p.subpaths)
            if L < min_len_mm:
                removed += 1
                removed_ids.append(p.id)
                continue
            kept.append(p)
            continue
        outer_area = sum(_sp_area(sp, tol) for sp in p.subpaths if not sp.is_hole)
        if outer_area < min_area_mm2:
            removed += 1
            removed_ids.append(p.id)
            continue
        new_sps = [sp for sp in p.subpaths if not sp.is_hole or _sp_area(sp, tol) >= min_area_mm2]
        removed += len(p.subpaths) - len(new_sps)
        p.subpaths = new_sps
        kept.append(p)
    graph.paths = kept
    return removed, removed_ids


def close_gaps(graph: PathGraph, gap_mm: float) -> int:
    """Close open subpaths whose endpoints are within gap_mm (fills only)."""
    closed = 0
    for p in graph.paths:
        if p.layer == Layer.ENGRAVE_LINE:
            continue
        for sp in p.subpaths:
            if sp.closed or not sp.segments:
                continue
            a, b = sp.start(), sp.end()
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d <= gap_mm:
                if d > 1e-9:
                    sp.segments.append(Line(p1=b, p2=a))
                sp.closed = True
                closed += 1
    return closed


def join_open_chains(graph: PathGraph, gap_mm: float) -> int:
    """Join open ENGRAVE_LINE paths whose endpoints are within gap_mm into single
    continuous paths (fewer pen-ups on the galvo). Returns number of joins."""
    lines = [p for p in graph.paths if p.layer == Layer.ENGRAVE_LINE and len(p.subpaths) == 1 and not p.subpaths[0].closed]
    others = [p for p in graph.paths if p not in lines]
    joins = 0
    changed = True
    while changed and len(lines) > 1:
        changed = False
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                a, b = lines[i].subpaths[0], lines[j].subpaths[0]
                pairs = [
                    (a.end(), b.start(), False, False),
                    (a.end(), b.end(), False, True),
                    (a.start(), b.start(), True, False),
                    (a.start(), b.end(), True, True),
                ]
                for pa, pb, rev_a, rev_b in pairs:
                    if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= gap_mm:
                        sa = _reverse(a) if rev_a else a
                        sb = _reverse(b) if rev_b else b
                        segs = list(sa.segments)
                        if math.hypot(sa.end()[0] - sb.start()[0], sa.end()[1] - sb.start()[1]) > 1e-9:
                            segs.append(Line(p1=sa.end(), p2=sb.start()))
                        segs.extend(sb.segments)
                        lines[i].subpaths = [Subpath(segments=segs, closed=False)]
                        lines.pop(j)
                        joins += 1
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
    # close chains that came back to their start
    for p in lines:
        sp = p.subpaths[0]
        a, b = sp.start(), sp.end()
        if len(sp.segments) > 2 and math.hypot(a[0] - b[0], a[1] - b[1]) <= gap_mm:
            sp.closed = True
    graph.paths = others + lines
    return joins


def _reverse(sp: Subpath) -> Subpath:
    from ..models import Cubic, Arc
    segs = []
    for s in reversed(sp.segments):
        if isinstance(s, Line):
            segs.append(Line(p1=s.p2, p2=s.p1))
        elif isinstance(s, Cubic):
            segs.append(Cubic(p0=s.p3, c1=s.c2, c2=s.c1, p3=s.p0))
        elif isinstance(s, Arc):
            segs.append(Arc(center=s.center, r=s.r, a0=s.a1, a1=s.a0, ccw=not s.ccw))
    return Subpath(segments=segs, closed=sp.closed, is_hole=sp.is_hole)


def _sp_hash(sp: Subpath, decimals: int = 3) -> str:
    h = hashlib.sha1()
    ring = subpath_to_ring(sp, 0.05)
    if len(ring):
        # rotation-invariant: start from the lexicographically smallest point
        r = np.round(ring, decimals)
        k = int(np.lexsort((r[:, 1], r[:, 0]))[0])
        r = np.roll(r, -k, axis=0)
        h.update(r.tobytes())
    return h.hexdigest()


def dedupe_subpaths(graph: PathGraph) -> int:
    """Remove exact duplicate subpaths/paths (double-marking hazard)."""
    seen: set[str] = set()
    removed = 0
    new_paths: list[Path] = []
    for p in graph.paths:
        sps = []
        for sp in p.subpaths:
            k = _sp_hash(sp)
            if k in seen:
                removed += 1
                continue
            seen.add(k)
            sps.append(sp)
        if sps and not sps[0].is_hole:
            p.subpaths = sps
            new_paths.append(p)
        elif sps:
            removed += len(sps)
    graph.paths = new_paths
    return removed
