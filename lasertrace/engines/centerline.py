"""Centerline / stroke engine (first version).

mask -> Zhang-Suen thinning (skimage) -> pixel graph -> walk into polylines
(junction-aware: each branch between junctions/endpoints is one polyline;
straight-through continuation is preferred at crossings) -> prune spurs shorter
than min_feature -> RDP in mm -> ENGRAVE_LINE open paths with stroke width.

Strokes whose local width exceeds `centerline_max_width_mm` are reported so
the hybrid mode can hand them to the outline engine.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict

import cv2
import numpy as np

from ..geometry.bezier import fit_cubics, cubic_is_line, rdp
from ..models import Cubic, Layer, Line, Path, PathGraph, Subpath
from .base import EngineContext

try:
    from skimage.morphology import skeletonize as _skel
    _HAVE_SKIMAGE = True
except Exception:  # pragma: no cover
    _HAVE_SKIMAGE = False

_NB8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def skeletonize(mask: np.ndarray) -> np.ndarray:
    if not _HAVE_SKIMAGE:
        raise RuntimeError("centerline engine needs scikit-image")
    sk = _skel(mask > 0)
    return sk.astype(np.uint8)


def _neighbors(sk: np.ndarray, y: int, x: int) -> list[tuple[int, int]]:
    h, w = sk.shape
    out = []
    for dy, dx in _NB8:
        yy, xx = y + dy, x + dx
        if 0 <= yy < h and 0 <= xx < w and sk[yy, xx]:
            out.append((yy, xx))
    return out


def skeleton_to_polylines(sk: np.ndarray) -> list[tuple[np.ndarray, bool]]:
    """Walk the skeleton into branches. Returns [(points(N,2) as (x,y), closed)]."""
    ys, xs = np.nonzero(sk)
    if ys.size == 0:
        return []
    pts = set(zip(ys.tolist(), xs.tolist()))
    deg = {p: len(_neighbors(sk, *p)) for p in pts}
    nodes = {p for p, d in deg.items() if d != 2}  # endpoints + junctions
    visited_edges: set[frozenset] = set()
    polylines: list[tuple[np.ndarray, bool]] = []

    def walk(start, nxt):
        path = [start, nxt]
        visited_edges.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while cur not in nodes:
            nb = [q for q in _neighbors(sk, *cur) if q != prev and frozenset((cur, q)) not in visited_edges]
            if not nb:
                break
            # prefer straight continuation (junction-aware)
            if len(nb) > 1:
                d = (cur[0] - prev[0], cur[1] - prev[1])
                nb.sort(key=lambda q: -((q[0] - cur[0]) * d[0] + (q[1] - cur[1]) * d[1]))
            q = nb[0]
            visited_edges.add(frozenset((cur, q)))
            path.append(q)
            prev, cur = cur, q
            if cur == start:
                break
        return path

    for n in sorted(nodes):
        for q in _neighbors(sk, *n):
            if frozenset((n, q)) in visited_edges:
                continue
            path = walk(n, q)
            closed = path[0] == path[-1] and len(path) > 3
            arr = np.array([(x, y) for (y, x) in path], dtype=np.float64)
            polylines.append((arr, closed))
    # pure cycles (no nodes at all, e.g. a ring)
    remaining = [p for p in sorted(pts) if all(frozenset((p, q)) not in visited_edges for q in _neighbors(sk, *p))]
    seen: set = set()
    for p in remaining:
        if p in seen:
            continue
        nb = _neighbors(sk, *p)
        if not nb:
            seen.add(p)
            continue
        path = walk(p, nb[0])
        for q in path:
            seen.add(q)
        arr = np.array([(x, y) for (y, x) in path], dtype=np.float64)
        polylines.append((arr, True))
    return polylines


def _prune_spurs(polys: list[tuple[np.ndarray, bool]], min_len: float, sk_endpoints: set) -> list[tuple[np.ndarray, bool]]:
    """Drop open branches shorter than min_len that end in a free endpoint."""
    out = []
    for arr, closed in polys:
        if closed:
            out.append((arr, closed))
            continue
        L = float(np.sum(np.hypot(*np.diff(arr, axis=0).T)))
        ends_free = (int(arr[0][1]), int(arr[0][0])) in sk_endpoints or (int(arr[-1][1]), int(arr[-1][0])) in sk_endpoints
        if L < min_len and ends_free:
            continue
        out.append((arr, closed))
    return out


def _join_chains(polys: list[tuple[np.ndarray, bool]]) -> list[tuple[np.ndarray, bool]]:
    """Join open polylines that meet at a degree-2 junction created by pruning."""
    open_polys = [list(map(tuple, a)) for a, c in polys if not c]
    closed = [(a, c) for a, c in polys if c]
    endpoint_index: dict[tuple, list[int]] = defaultdict(list)
    for i, p in enumerate(open_polys):
        endpoint_index[p[0]].append(i)
        endpoint_index[p[-1]].append(i)
    used = [False] * len(open_polys)
    result: list[tuple[np.ndarray, bool]] = []
    for i in range(len(open_polys)):
        if used[i]:
            continue
        chain = list(open_polys[i])
        used[i] = True
        changed = True
        while changed:
            changed = False
            for end_idx in (0, -1):
                key = chain[end_idx]
                cands = [j for j in endpoint_index[key] if not used[j]]
                if len(cands) == 1 and len(endpoint_index[key]) == 2:
                    j = cands[0]
                    other = list(open_polys[j])
                    if other[0] != key:
                        other.reverse()
                    used[j] = True
                    chain = other[::-1][:-1] + chain if end_idx == 0 else chain + other[1:]
                    changed = True
        result.append((np.array(chain, dtype=np.float64), False))
    return closed + result


class CenterlineEngine:
    name = "centerline"

    def available(self) -> bool:
        return _HAVE_SKIMAGE

    def trace(self, mask: np.ndarray, ctx: EngineContext) -> PathGraph:
        t0 = time.perf_counter()
        s = ctx.settings
        ppm = ctx.px_per_mm
        ink = (mask > 0).astype(np.uint8)
        if ink.max() == 0:
            return PathGraph(paths=[], units="mm", bbox=(0, 0, 0, 0), px_per_mm=ppm)
        # light close to bridge 1px breaks before thinning
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        sk = skeletonize(ink * 255)
        dt = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
        ctx.timings_ms["skeleton"] = (time.perf_counter() - t0) * 1000

        polys = skeleton_to_polylines(sk)
        endpoints = {(y, x) for y, x in zip(*np.nonzero(sk)) if len(_neighbors(sk, y, x)) == 1}
        min_len_px = max(2.0, s.min_feature_mm * ppm * 2.0)
        polys = _prune_spurs(polys, min_len_px, endpoints)
        polys = _join_chains(polys)

        ys, xs = np.nonzero(ink)
        minx, miny, maxx, maxy = float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)
        origin = np.array([minx, miny])

        paths: list[Path] = []
        wide_paths = 0
        for arr, closed in polys:
            if len(arr) < 2:
                continue
            # width along this branch
            iy = np.clip(arr[:, 1].astype(int), 0, dt.shape[0] - 1)
            ix = np.clip(arr[:, 0].astype(int), 0, dt.shape[1] - 1)
            width_mm = float(np.median(dt[iy, ix]) * 2.0 / ppm)
            if width_mm > s.centerline_max_width_mm:
                wide_paths += 1
            mm = (arr - origin) / ppm
            simp = rdp(mm, ctx.rdp_tol_mm, closed=closed)
            if closed and len(simp) >= 3:
                simp = np.vstack([simp, simp[:1]])
            segments: list = []
            if s.smoothness <= 0 or len(simp) < 3:
                for i in range(len(simp) - 1):
                    segments.append(Line(p1=(float(simp[i][0]), float(simp[i][1])), p2=(float(simp[i + 1][0]), float(simp[i + 1][1]))))
            else:
                for bez in fit_cubics(simp, ctx.fit_tol_mm):
                    p0, c1, c2, p3 = bez
                    if cubic_is_line(p0, c1, c2, p3, ctx.fit_tol_mm * 0.5):
                        segments.append(Line(p1=(float(p0[0]), float(p0[1])), p2=(float(p3[0]), float(p3[1]))))
                    else:
                        segments.append(Cubic(p0=(float(p0[0]), float(p0[1])), c1=(float(c1[0]), float(c1[1])), c2=(float(c2[0]), float(c2[1])), p3=(float(p3[0]), float(p3[1]))))
            if not segments:
                continue
            paths.append(Path(subpaths=[Subpath(segments=segments, closed=closed, is_hole=False)], layer=Layer.ENGRAVE_LINE, stroke_width_mm=round(width_mm, 3)))

        if wide_paths:
            ctx.warn("WIDE_STROKE_AS_CENTERLINE", f"{wide_paths} stroke(s) are wider than {s.centerline_max_width_mm} mm; consider hybrid or outline mode so the laser fills them.", "info")
        ctx.timings_ms["centerline"] = (time.perf_counter() - t0) * 1000
        bbox = (0.0, 0.0, (maxx - minx) / ppm, (maxy - miny) / ppm)
        return PathGraph(paths=paths, units="mm", bbox=bbox, px_per_mm=ppm)
