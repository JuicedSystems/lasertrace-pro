"""Planar-map hygiene with shapely: winding, overlap union, cut-outer layer."""
from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from ..geometry.bezier import signed_area
from ..geometry.polygons import path_to_polygons, polygon_to_subpath_rings, subpath_to_ring, valid_union
from ..models import Layer, Path, PathGraph, Subpath
from .clean import _reverse


def ensure_winding(graph: PathGraph, tol: float = 0.02) -> int:
    """Outer rings positive shoelace area, holes negative. Returns flips."""
    flips = 0
    for p in graph.paths:
        for sp in p.subpaths:
            if not sp.closed or len(sp.segments) < 2:
                continue
            a = signed_area(subpath_to_ring(sp, tol))
            want_pos = not sp.is_hole
            if (a > 0) != want_pos and abs(a) > 1e-12:
                new = _reverse(sp)
                sp.segments = new.segments
                flips += 1
    return flips


def detect_overlaps(graph: PathGraph, tol: float = 0.02, layer: Layer = Layer.ENGRAVE_FILL) -> tuple[float, list[tuple[str, str]]]:
    """Return (total overlap area mm2, list of overlapping path id pairs)."""
    items: list[tuple[str, Polygon, int | None]] = []
    for p in graph.paths:
        if p.layer != layer:
            continue
        for poly in path_to_polygons(p, tol):
            items.append((p.id, poly, p.depth_index))
    if len(items) < 2:
        return 0.0, []
    tree = STRtree([poly for _, poly, _ in items])
    pairs: list[tuple[str, str]] = []
    total = 0.0
    for i, (pid, poly, di) in enumerate(items):
        for j in tree.query(poly):
            j = int(j)
            if j <= i:
                continue
            qid, q, dj = items[j]
            if pid == qid or di != dj:
                continue  # depth slices overlap by design (cumulative passes)
            inter = poly.intersection(q)
            if not inter.is_empty and inter.area > tol * tol:
                total += inter.area
                pairs.append((pid, qid))
    return total, pairs


def union_overlapping_fills(graph: PathGraph, refit, tol: float = 0.02) -> int:
    """If any ENGRAVE_FILL paths overlap, union them (planar map) and rebuild the
    affected paths with `refit(ring_mm, is_hole) -> Subpath`. Returns the number
    of paths rebuilt. Non-overlapping paths keep their fitted curves untouched."""
    _, pairs = detect_overlaps(graph, tol)
    if not pairs:
        return 0
    involved = set()
    for a, b in pairs:
        involved.add(a)
        involved.add(b)
    keep = [p for p in graph.paths if p.id not in involved]
    polys = []
    for p in graph.paths:
        if p.id in involved:
            polys.extend(path_to_polygons(p, tol))
    rebuilt = 0
    for poly in valid_union(polys):
        sps: list[Subpath] = []
        for ring, is_hole in polygon_to_subpath_rings(poly):
            sp = refit(ring, is_hole)
            if sp is not None:
                sps.append(sp)
        if sps and not sps[0].is_hole:
            keep.append(Path(subpaths=sps, layer=Layer.ENGRAVE_FILL, fill_rule="evenodd"))
            rebuilt += 1
    graph.paths = keep
    return rebuilt


def add_cut_outer(graph: PathGraph, refit, tol: float = 0.02, offset_mm: float = 0.0) -> int:
    """Cut Outer + Engrave Inner: the silhouette (union of all fills, exterior
    rings only) is added as CUT paths. Existing fills stay as ENGRAVE. Returns
    number of CUT paths added."""
    polys = []
    for p in graph.paths:
        if p.layer == Layer.ENGRAVE_FILL:
            polys.extend(path_to_polygons(p, tol))
    if not polys:
        return 0
    added = 0
    for poly in valid_union(polys):
        outer = Polygon(poly.exterior)
        if offset_mm:
            outer = outer.buffer(offset_mm, join_style=1)
        for q in ([outer] if isinstance(outer, Polygon) else list(outer.geoms)):
            ring = np.asarray(q.exterior.coords, dtype=np.float64)[:-1]
            sp = refit(ring, False)
            if sp is not None:
                graph.paths.append(Path(subpaths=[sp], layer=Layer.CUT, fill_rule="evenodd"))
                added += 1
    return added
