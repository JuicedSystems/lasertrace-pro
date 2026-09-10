"""Shapely-based planar helpers shared by engines and hygiene.

A *ring* is an (N,2) float array. A *region* is a shapely Polygon (with holes).
All functions are pure.
"""
from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from .bezier import flatten_cubic, signed_area
from ..models import Arc, Cubic, Line, Path, PathGraph, Subpath


def polygons_of(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(polygons_of(g))
        return out
    return []


def valid_union(polys: Iterable[Polygon]) -> list[Polygon]:
    """Union same-layer regions so nothing double-marks; returns clean polygons
    sorted deterministically (by area desc, then bounds)."""
    fixed = []
    for p in polys:
        if p.is_empty:
            continue
        if not p.is_valid:
            p = make_valid(p)
        fixed.extend(polygons_of(p))
    if not fixed:
        return []
    u = unary_union(fixed)
    out = polygons_of(u)
    return sort_polygons(out)


def sort_polygons(polys: list[Polygon]) -> list[Polygon]:
    return sorted(polys, key=lambda p: (-round(p.area, 6), round(p.bounds[0], 4), round(p.bounds[1], 4)))


def ring_array(ring) -> np.ndarray:
    a = np.asarray(ring.coords, dtype=np.float64)
    if len(a) > 1 and np.allclose(a[0], a[-1]):
        a = a[:-1]
    return a


def orient_ring(ring: np.ndarray, positive: bool) -> np.ndarray:
    """Make shoelace area positive (outer) or negative (hole)."""
    a = signed_area(ring)
    if (a > 0) != positive:
        return ring[::-1].copy()
    return ring


def _rectilinear(cnt: np.ndarray, padded: np.ndarray) -> np.ndarray:
    """Turn an 8-connected pixel-centre contour into a 4-connected one.

    OpenCV steps diagonally around concave corners (hole corners, the inside of
    an L) and along diagonal edges. For every diagonal step insert the corner
    pixel that is ink, so that buffering by 0.5 px afterwards yields the exact
    pixel-edge boundary (square QR modules, exact raster area)."""
    pts = cnt.astype(np.int64)
    n = len(pts)
    if n < 2:
        return pts
    nxt = np.roll(pts, -1, axis=0)
    d = nxt - pts
    diag = (np.abs(d[:, 0]) == 1) & (np.abs(d[:, 1]) == 1)
    if not diag.any():
        return pts
    a = np.stack([nxt[:, 0], pts[:, 1]], axis=1)   # (q.x, p.y)
    b = np.stack([pts[:, 0], nxt[:, 1]], axis=1)   # (p.x, q.y)
    ia = padded[a[:, 1], a[:, 0]] > 0
    ib = padded[b[:, 1], b[:, 0]] > 0
    ins = diag & (ia != ib)
    inserted = np.where(ia[:, None], a, b)
    out = np.empty((n + int(ins.sum()), 2), dtype=np.int64)
    # interleave: positions of original points shift by the number of insertions before them
    shift = np.concatenate([[0], np.cumsum(ins)[:-1]])
    out[np.arange(n) + shift] = pts
    out[np.arange(n)[ins] + shift[ins] + 1] = inserted[ins]
    return out


def contours_to_polygons(mask: np.ndarray, pixel_edge: bool = True) -> list[Polygon]:
    """Extract ink regions from a binary mask (255 = ink) as shapely polygons
    in pixel coordinates. When `pixel_edge` is set, contours (which OpenCV
    returns through pixel centres) are made rectilinear and pushed outward by
    half a pixel so the region boundary sits exactly on pixel edges and the
    area matches the raster."""
    if mask is None or mask.size == 0 or mask.max() == 0:
        return []
    padded = cv2.copyMakeBorder((mask > 0).astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    contours, hierarchy = cv2.findContours(padded, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE if pixel_edge else cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]

    def ring(idx: int) -> np.ndarray:
        c = contours[idx][:, 0, :]
        if pixel_edge:
            c = _rectilinear(c, padded)
        return c.astype(np.float64) - 1.0  # remove padding

    polys: list[Polygon] = []
    for i, cnt in enumerate(contours):
        nxt, prv, child, parent = hierarchy[i]
        if parent != -1:
            continue  # holes are attached below
        outer = ring(i)
        if len(outer) < 3:
            # 1- or 2-pixel blobs: make a tiny square so tiny features are not lost silently
            x, y = outer[0]
            outer = np.array([[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]], dtype=np.float64) - 0.5
        holes = []
        c = child
        while c != -1:
            h = ring(c)
            if len(h) >= 3:
                holes.append(h)
            c = hierarchy[c][0]
        try:
            poly = Polygon(outer, holes)
        except Exception:
            continue
        # Buffer BEFORE validating: pixel-centre contours of 8-connected ink
        # touch themselves at diagonal contacts; make_valid would split them
        # and lose the enclosed holes, whereas GEOS buffer handles the
        # self-touching ring and yields exactly the raster's holes.
        if pixel_edge:
            poly = poly.buffer(0.5, join_style=2, mitre_limit=2.0).simplify(1e-6, preserve_topology=True)
        elif not poly.is_valid:
            poly = make_valid(poly)
        polys.extend(polygons_of(poly))
    return valid_union(polys)


def polygon_to_subpath_rings(poly: Polygon) -> list[tuple[np.ndarray, bool]]:
    """Return [(ring_array, is_hole)] with outer positive / holes negative area."""
    out = [(orient_ring(ring_array(poly.exterior), True), False)]
    for hole in poly.interiors:
        r = ring_array(hole)
        if len(r) >= 3:
            out.append((orient_ring(r, False), True))
    return out


# --------------------------------------------------------------------------- #
# PathGraph <-> polygon conversion (for rasterizing, IoU, and booleans)
# --------------------------------------------------------------------------- #

def subpath_to_ring(sp: Subpath, tol: float) -> np.ndarray:
    pts: list[np.ndarray] = []
    for seg in sp.segments:
        if isinstance(seg, Line):
            pts.append(np.array([seg.p1], dtype=np.float64))
        elif isinstance(seg, Cubic):
            pts.append(flatten_cubic(seg.p0, seg.c1, seg.c2, seg.p3, tol)[:-1])
        elif isinstance(seg, Arc):
            n = max(2, int(abs(seg.a1 - seg.a0) * seg.r / max(tol, 1e-4)) // 4 + 2)
            a = np.linspace(seg.a0, seg.a1, n + 1)[:-1]
            pts.append(np.stack([seg.center[0] + seg.r * np.cos(a), seg.center[1] + seg.r * np.sin(a)], axis=1))
    if not pts:
        return np.zeros((0, 2))
    ring = np.vstack(pts)
    if not sp.closed:
        ring = np.vstack([ring, np.array([sp.end()], dtype=np.float64)])
    return ring


def path_to_polygons(path: Path, tol: float) -> list[Polygon]:
    outer_rings = []
    hole_rings = []
    for sp in path.subpaths:
        if not sp.closed:
            continue
        r = subpath_to_ring(sp, tol)
        if len(r) < 3:
            continue
        (hole_rings if sp.is_hole else outer_rings).append(r)
    polys = []
    for o in outer_rings:
        try:
            p = Polygon(o)
        except Exception:
            continue
        if not p.is_valid:
            p = make_valid(p)
        for pp in polygons_of(p):
            polys.append(pp)
    if not polys:
        return []
    result = []
    for p in polys:
        inner = [h for h in hole_rings if p.contains(Polygon(h).representative_point())]
        try:
            q = Polygon(np.asarray(p.exterior.coords), inner)
        except Exception:
            q = p
        if not q.is_valid:
            q = make_valid(q)
        result.extend(polygons_of(q))
    return result


def rasterize(graph: PathGraph, px_per_mm: float, shape: tuple[int, int], tol_mm: float = 0.02, offset_px: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Rasterize filled paths (ENGRAVE_FILL / CUT interiors are NOT filled, only
    ENGRAVE_FILL) to a uint8 mask at the given resolution. Used for IoU stats and
    the simulation view. `offset_px` shifts the graph origin (bbox top-left)."""
    h, w = shape
    # OpenCV fills polygons inclusively (a 1 px wide polygon paints 2 px), so
    # render 4x supersampled and threshold coverage at 50% for pixel-accurate area.
    ss = 4
    out = np.zeros((h * ss, w * ss), dtype=np.uint8)
    fills = [p for p in graph.paths if p.layer.value == "ENGRAVE_FILL"]
    # draw big first so islands inside holes overwrite correctly
    def approx_area(p: Path) -> float:
        a = 0.0
        for sp in p.subpaths:
            if not sp.is_hole and sp.closed:
                a += abs(signed_area(subpath_to_ring(sp, tol_mm * 4)))
        return a
    fills.sort(key=approx_area, reverse=True)
    for p in fills:
        outers, holes = [], []
        for sp in p.subpaths:
            if not sp.closed:
                continue
            r = subpath_to_ring(sp, tol_mm)
            if len(r) < 3:
                continue
            # -0.5: graph coordinates sit on pixel edges, OpenCV vertices are pixel centres
            r_px = np.round(((r * px_per_mm + np.array(offset_px)) * ss - 0.5) * 16).astype(np.int32)  # fixed point
            (holes if sp.is_hole else outers).append(r_px.reshape(-1, 1, 2))
        if outers:
            cv2.fillPoly(out, outers, 255, lineType=cv2.LINE_8, shift=4)
        if holes:
            cv2.fillPoly(out, holes, 0, lineType=cv2.LINE_8, shift=4)
    cov = out.reshape(h, ss, w, ss).mean(axis=(1, 3))
    return ((cov >= 127.5) * 255).astype(np.uint8)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    A = a > 0
    B = b > 0
    inter = np.count_nonzero(A & B)
    union = np.count_nonzero(A | B)
    return float(inter) / float(union) if union else 1.0
