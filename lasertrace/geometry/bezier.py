"""Polyline -> Bezier fitting (Schneider 1990, Graphics Gems) plus helpers.

Everything here works on numpy arrays of shape (N, 2) in whatever unit the
caller uses; the pipeline calls it in millimetres so tolerances are physical.
"""
from __future__ import annotations

import math

import numpy as np

# --------------------------------------------------------------------------- #
# Polyline utilities
# --------------------------------------------------------------------------- #

def rdp(points: np.ndarray, tol: float, closed: bool = False) -> np.ndarray:
    """Ramer-Douglas-Peucker. Iterative, deterministic. Keeps first/last point."""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 3 or tol <= 0:
        return pts.copy()
    if closed:
        # split at the point farthest from point 0 so both halves get simplified
        d = np.sum((pts - pts[0]) ** 2, axis=1)
        k = int(np.argmax(d))
        if k == 0:
            return pts.copy()
        a = rdp(pts[: k + 1], tol, False)
        b = rdp(np.vstack([pts[k:], pts[:1]]), tol, False)
        out = np.vstack([a[:-1], b[:-1]])
        return out if len(out) >= 3 else pts.copy()
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tol * tol
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        L2 = float(seg @ seg)
        sub = pts[i + 1 : j]
        if L2 < 1e-18:
            d2 = np.sum((sub - pts[i]) ** 2, axis=1)
        else:
            t = np.clip(((sub - pts[i]) @ seg) / L2, 0.0, 1.0)
            proj = pts[i] + t[:, None] * seg
            d2 = np.sum((sub - proj) ** 2, axis=1)
        k = int(np.argmax(d2))
        if d2[k] > tol2:
            idx = i + 1 + k
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return pts[keep]


def polyline_length(points: np.ndarray, closed: bool = False) -> float:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 2:
        return 0.0
    d = np.sqrt(np.sum(np.diff(pts, axis=0) ** 2, axis=1)).sum()
    if closed:
        d += float(np.hypot(*(pts[0] - pts[-1])))
    return float(d)


def signed_area(points: np.ndarray) -> float:
    """Shoelace signed area. Positive = counter-clockwise in a y-up frame
    (= clockwise on screen with y-down). We only rely on the sign being
    consistent: outer rings positive, holes negative (see ensure_winding)."""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def merge_collinear(points: np.ndarray, angle_tol_deg: float = 0.5, closed: bool = True) -> np.ndarray:
    """Drop vertices whose incoming/outgoing directions differ by less than the tolerance."""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 3:
        return pts.copy()
    cos_tol = math.cos(math.radians(angle_tol_deg))
    keep = np.ones(n, dtype=bool)
    rng = range(n) if closed else range(1, n - 1)
    for i in rng:
        a = pts[i] - pts[i - 1]
        b = pts[(i + 1) % n] - pts[i]
        la, lb = np.hypot(*a), np.hypot(*b)
        if la < 1e-12 or lb < 1e-12:
            keep[i] = False
            continue
        if float(a @ b) / (la * lb) >= cos_tol:
            keep[i] = False
    out = pts[keep]
    return out if len(out) >= (3 if closed else 2) else pts.copy()


def dedupe_consecutive(points: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 2:
        return pts.copy()
    d = np.sqrt(np.sum(np.diff(pts, axis=0) ** 2, axis=1))
    keep = np.concatenate([[True], d > eps])
    return pts[keep]


# --------------------------------------------------------------------------- #
# Corner detection on a dense closed ring
# --------------------------------------------------------------------------- #

def detect_corners(ring: np.ndarray, window: float, angle_thresh_deg: float, closed: bool = True) -> np.ndarray:
    """Return sorted indices of corner vertices.

    The turning angle at vertex i is measured between the chord from the point
    `window` units of arc length behind and the chord to the point `window`
    ahead. This is robust to pixel staircase noise. Non-maximum suppression
    keeps one corner per window.
    """
    pts = np.asarray(ring, dtype=np.float64)
    n = len(pts)
    if n < 4:
        return np.array([], dtype=int)
    seg = np.sqrt(np.sum((np.roll(pts, -1, axis=0) - pts) ** 2, axis=1))
    cum = np.concatenate([[0.0], np.cumsum(seg)])  # cum[i] = arc length to vertex i; cum[n] = total
    total = cum[-1]
    if total <= 0:
        return np.array([], dtype=int)
    window = min(window, total / 4.0)
    if window <= 0:
        return np.array([], dtype=int)

    def point_at(s: float) -> np.ndarray:
        if closed:
            s = s % total
        else:
            s = min(max(s, 0.0), total - 1e-12)
        k = int(np.searchsorted(cum, s, side="right") - 1)
        k = min(max(k, 0), n - 1)
        L = seg[k]
        if L < 1e-12:
            return pts[k]
        t = (s - cum[k]) / L
        return pts[k] + t * (pts[(k + 1) % n] - pts[k])

    angles = np.zeros(n)
    for i in range(n):
        if not closed and (cum[i] < window or total - cum[i] < window):
            continue
        pb = point_at(cum[i] - window)
        pa = point_at(cum[i] + window)
        a = pts[i] - pb
        b = pa - pts[i]
        la, lb = np.hypot(*a), np.hypot(*b)
        if la < 1e-12 or lb < 1e-12:
            continue
        c = float(np.clip((a @ b) / (la * lb), -1.0, 1.0))
        angles[i] = math.degrees(math.acos(c))

    cand = np.where(angles >= angle_thresh_deg)[0]
    if cand.size == 0:
        return cand
    # non-maximum suppression within +-window arc length
    corners: list[int] = []
    order = cand[np.argsort(-angles[cand], kind="stable")]
    taken = np.zeros(n, dtype=bool)
    for i in order:
        if taken[i]:
            continue
        corners.append(int(i))
        # suppress neighbours within window
        s0 = cum[i]
        for j in range(n):
            ds = abs(cum[j] - s0)
            if closed:
                ds = min(ds, total - ds)
            if ds <= window:
                taken[j] = True
    return np.array(sorted(corners), dtype=int)


# --------------------------------------------------------------------------- #
# Cubic Bezier evaluation / flattening
# --------------------------------------------------------------------------- #

def cubic_point(p0, c1, c2, p3, t):
    bez = np.array([p0, c1, c2, p3], dtype=np.float64)
    t = np.atleast_1d(np.asarray(t, dtype=np.float64))
    return _bernstein(t) @ bez


def flatten_cubic(p0, c1, c2, p3, tol: float) -> np.ndarray:
    """Flatten to a polyline with chordal error <= tol. Returns points incl. p0 and p3.
    Uses the standard flatness bound: n = ceil(sqrt(sqrt(dd) / (8 tol)))."""
    p0, c1, c2, p3 = (np.asarray(v, dtype=np.float64) for v in (p0, c1, c2, p3))
    dd = max(np.hypot(*(p0 - 2 * c1 + c2)), np.hypot(*(c1 - 2 * c2 + p3)))
    if tol <= 0:
        tol = 1e-3
    n = int(math.ceil(math.sqrt(math.sqrt(dd * dd) * 0.75 / tol))) if dd > 0 else 1
    n = max(1, min(n, 256))
    t = np.linspace(0.0, 1.0, n + 1)
    return cubic_point(p0, c1, c2, p3, t)


def cubic_is_line(p0, c1, c2, p3, tol: float) -> bool:
    p0, c1, c2, p3 = (np.asarray(v, dtype=np.float64) for v in (p0, c1, c2, p3))
    chord = p3 - p0
    L = np.hypot(*chord)
    if L < 1e-12:
        return True
    nrm = np.array([-chord[1], chord[0]]) / L
    for c in (c1, c2):
        if abs(float((c - p0) @ nrm)) > tol:
            return False
        # also require the control point to lie between the endpoints along the chord
        t = float((c - p0) @ chord) / (L * L)
        if t < -0.05 or t > 1.05:
            return False
    return True


# --------------------------------------------------------------------------- #
# Schneider least-squares cubic fit
# --------------------------------------------------------------------------- #

def _chord_params(pts: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.sum(np.diff(pts, axis=0) ** 2, axis=1))
    u = np.concatenate([[0.0], np.cumsum(d)])
    if u[-1] <= 0:
        return np.linspace(0, 1, len(pts))
    return u / u[-1]


def _bernstein(u: np.ndarray) -> np.ndarray:
    """(n,4) cubic Bernstein basis."""
    mu = 1.0 - u
    mu2 = mu * mu
    u2 = u * u
    return np.stack((mu2 * mu, 3.0 * mu2 * u, 3.0 * mu * u2, u2 * u), axis=1)


def _generate_bezier(pts: np.ndarray, B: np.ndarray, t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    p0, p3 = pts[0], pts[-1]
    b1 = B[:, 1]
    b2 = B[:, 2]
    # A1 = b1 * t1, A2 = b2 * t2 (unit tangents), so the normal-equation sums collapse to scalars
    s11 = float(b1 @ b1)
    s22 = float(b2 @ b2)
    s12 = float(b1 @ b2) * float(t1 @ t2)
    tmp = pts - np.outer(B[:, 0] + b1, p0) - np.outer(b2 + B[:, 3], p3)
    x1 = float(b1 @ (tmp @ t1))
    x2 = float(b2 @ (tmp @ t2))
    det = s11 * s22 - s12 * s12
    if abs(det) > 1e-12:
        a1 = (x1 * s22 - x2 * s12) / det
        a2 = (s11 * x2 - s12 * x1) / det
    else:
        a1 = a2 = 0.0
    seg_len = float(math.hypot(p3[0] - p0[0], p3[1] - p0[1]))
    eps = 1e-6 * seg_len
    # Degenerate case guard: with near-antiparallel tangents the system is
    # singular and the alphas explode while still passing the sample-point
    # error test. A sane handle never exceeds the arc length of the piece.
    arc = float(np.sum(np.hypot(*(np.diff(pts, axis=0).T)))) if len(pts) > 1 else seg_len
    limit = max(arc, seg_len) * 1.0 + 1e-12
    if (not (a1 > eps and a2 > eps)) or (not (math.isfinite(a1) and math.isfinite(a2))) or a1 > limit or a2 > limit:
        a1 = a2 = seg_len / 3.0
    return np.array([p0, p0 + t1 * a1, p3 + t2 * a2, p3])


def _max_error(pts: np.ndarray, bez: np.ndarray, B: np.ndarray) -> tuple[float, int]:
    diff = B @ bez - pts
    d2 = np.einsum("ij,ij->i", diff, diff)
    k = int(np.argmax(d2))
    return float(math.sqrt(d2[k])), k


def _reparam(pts: np.ndarray, bez: np.ndarray, u: np.ndarray, B: np.ndarray) -> np.ndarray:
    p0, c1, c2, p3 = bez
    diff = B @ bez - pts
    mu = 1.0 - u
    D1 = np.stack((mu * mu, 2.0 * mu * u, u * u), axis=1) @ np.array([c1 - p0, c2 - c1, p3 - c2]) * 3.0
    D2 = np.stack((mu, u), axis=1) @ np.array([c2 - 2 * c1 + p0, p3 - 2 * c2 + c1]) * 6.0
    num = np.einsum("ij,ij->i", diff, D1)
    den = np.einsum("ij,ij->i", D1, D1) + np.einsum("ij,ij->i", diff, D2)
    safe = np.abs(den) > 1e-12
    nu = u.copy()
    nu[safe] -= num[safe] / den[safe]
    np.clip(nu, 0.0, 1.0, out=nu)
    nu[0], nu[-1] = 0.0, 1.0
    return np.maximum.accumulate(nu)


def _unit(v: np.ndarray) -> np.ndarray:
    L = float(np.hypot(*v))
    return v / L if L > 1e-12 else np.array([1.0, 0.0])


def _tangent_at(pts: np.ndarray, i: int, direction: int, reach: float) -> np.ndarray:
    """Direction leaving vertex i (direction=+1 forward, -1 backward), estimated
    with a chord that reaches ~`reach` units along the polyline so pixel
    staircase noise averages out."""
    n = len(pts)
    j = i
    acc = 0.0
    while 0 <= j + direction < n:
        step = float(np.hypot(*(pts[j + direction] - pts[j])))
        j += direction
        acc += step
        if acc >= reach:
            break
    if j == i:
        j = min(max(i + direction, 0), n - 1)
    return _unit(pts[j] - pts[i])


def _split_tangent(pts: np.ndarray, s: int, reach: float) -> np.ndarray:
    """Forward tangent at an interior split vertex from a symmetric chord."""
    fwd = _tangent_at(pts, s, +1, reach)
    bwd = _tangent_at(pts, s, -1, reach)
    t = _unit(fwd - bwd)
    if float(np.hypot(*t)) < 1e-9:
        return fwd
    return t


def fit_cubics(points: np.ndarray, tol: float, t_start: np.ndarray | None = None, t_end: np.ndarray | None = None, max_depth: int = 24) -> list[np.ndarray]:
    """Fit an open polyline with a chain of cubic Beziers, max error <= tol.
    Returns a list of (4,2) arrays. Adjacent curves share endpoints."""
    pts = dedupe_consecutive(np.asarray(points, dtype=np.float64))
    n = len(pts)
    if n < 2:
        return []
    if n == 2:
        d = (pts[1] - pts[0]) / 3.0
        return [np.array([pts[0], pts[0] + d, pts[1] - d, pts[1]])]
    reach = max(tol * 4.0, 1e-9)
    t1 = _unit(t_start) if t_start is not None else _tangent_at(pts, 0, +1, reach)
    t2 = _unit(t_end) if t_end is not None else _tangent_at(pts, n - 1, -1, reach)
    return _fit_rec(pts, t1, t2, tol, max_depth, reach)


def _fit_rec(pts: np.ndarray, t1: np.ndarray, t2: np.ndarray, tol: float, depth: int, reach: float) -> list[np.ndarray]:
    n = len(pts)
    if n == 2:
        d = (pts[1] - pts[0]) / 3.0
        return [np.array([pts[0], pts[0] + d, pts[1] - d, pts[1]])]
    u = _chord_params(pts)
    B = _bernstein(u)
    bez = _generate_bezier(pts, B, t1, t2)
    err, split = _max_error(pts, bez, B)
    if err <= tol:
        return [bez]
    if err <= tol * 12:
        best, best_err, best_split = bez, err, split
        for _ in range(4):
            u = _reparam(pts, bez, u, B)
            B = _bernstein(u)
            bez = _generate_bezier(pts, B, t1, t2)
            err, split = _max_error(pts, bez, B)
            if err < best_err:
                best, best_err, best_split = bez, err, split
            if err <= tol:
                return [bez]
        bez, err, split = best, best_err, best_split
    if depth <= 0 or n <= 3:
        # give up: polyline the remainder as degenerate cubics (straight)
        out = []
        for i in range(n - 1):
            d = (pts[i + 1] - pts[i]) / 3.0
            out.append(np.array([pts[i], pts[i] + d, pts[i + 1] - d, pts[i + 1]]))
        return out
    split = min(max(split, 1), n - 2)
    tc = _split_tangent(pts, split, reach)
    left = _fit_rec(pts[: split + 1], t1, -tc, tol, depth - 1, reach)
    right = _fit_rec(pts[split:], tc, t2, tol, depth - 1, reach)
    return left + right


def fit_closed_ring(ring: np.ndarray, tol: float, corners: np.ndarray, max_depth: int = 24) -> list[tuple[np.ndarray, bool]]:
    """Fit a closed ring split at `corners` (indices). Returns a list of
    (bezier(4,2), is_corner_piece_start) — the flag is not used by callers yet
    but kept for future stroke-cap logic. Curves are emitted in ring order and
    join end-to-end, closing back to the first point."""
    pts = np.asarray(ring, dtype=np.float64)
    n = len(pts)
    if n < 3:
        return []
    corners = np.asarray(sorted(set(int(c) for c in corners)), dtype=int)
    out: list[tuple[np.ndarray, bool]] = []
    if corners.size == 0:
        # closed smooth curve: fit from 0 around to 0, with a shared tangent
        closed_pts = np.vstack([pts, pts[:1]])
        reach = max(tol * 4.0, 1e-9)
        fwd = _tangent_at(closed_pts, 0, +1, reach)
        bwd = _tangent_at(np.vstack([pts[-1:], pts]), 1, -1, reach) if n > 2 else -fwd
        t = _unit(fwd - bwd)
        if float(np.hypot(*t)) < 1e-9:
            t = fwd
        for b in fit_cubics(closed_pts, tol, t, -t, max_depth):
            out.append((b, False))
        return out
    k = len(corners)
    for ci in range(k):
        a = corners[ci]
        b = corners[(ci + 1) % k]
        if b > a:
            piece = pts[a : b + 1]
        else:
            piece = np.vstack([pts[a:], pts[: b + 1]])
        if len(piece) < 2:
            continue
        for bz in fit_cubics(piece, tol, None, None, max_depth):
            out.append((bz, True))
    return out
