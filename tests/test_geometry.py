import math

import numpy as np
from shapely.geometry import Polygon

from lasertrace.geometry.bezier import (cubic_is_line, cubic_point, detect_corners, fit_cubics, flatten_cubic, merge_collinear, rdp, signed_area)
from lasertrace.geometry.polygons import contours_to_polygons, iou, rasterize


def circle_pts(r=10.0, n=720, cx=0.0, cy=0.0):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], axis=1)


def test_rdp_keeps_endpoints_and_reduces():
    pts = np.array([[0, 0], [1, 0.01], [2, -0.01], [3, 0], [3, 3]], dtype=float)
    out = rdp(pts, 0.05)
    assert np.allclose(out[0], pts[0]) and np.allclose(out[-1], pts[-1])
    assert len(out) == 3


def test_rdp_closed_ring():
    ring = circle_pts(10, 360)
    out = rdp(ring, 0.05, closed=True)
    assert 3 <= len(out) < 360


def test_signed_area_sign_flips_with_orientation():
    sq = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    assert signed_area(sq) > 0
    assert signed_area(sq[::-1]) < 0
    assert abs(signed_area(sq)) == 1.0


def test_merge_collinear():
    pts = np.array([[0, 0], [1, 0], [2, 0], [2, 1], [0, 1]], dtype=float)
    out = merge_collinear(pts, 0.5, closed=True)
    assert len(out) == 4


def test_detect_corners_square_vs_circle():
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    dense = []
    for i in range(4):
        a, b = sq[i], sq[(i + 1) % 4]
        for t in np.linspace(0, 1, 50, endpoint=False):
            dense.append(a + t * (b - a))
    dense = np.array(dense)
    c = detect_corners(dense, window=1.0, angle_thresh_deg=45)
    assert len(c) == 4
    assert len(detect_corners(circle_pts(10, 720), window=1.0, angle_thresh_deg=45)) == 0


def test_fit_cubics_quarter_circle_single_segment():
    t = np.linspace(0, math.pi / 2, 100)
    pts = np.stack([10 * np.cos(t), 10 * np.sin(t)], axis=1)
    curves = fit_cubics(pts, 0.02)
    assert len(curves) == 1
    p = cubic_point(*curves[0], np.linspace(0, 1, 50))
    err = np.abs(np.hypot(p[:, 0], p[:, 1]) - 10).max()
    assert err < 0.03


def test_fit_cubics_full_circle_few_segments():
    ring = np.vstack([circle_pts(25, 720), circle_pts(25, 720)[:1]])
    t = np.array([0.0, 1.0])
    curves = fit_cubics(ring, 0.05, t, -t)
    assert 4 <= len(curves) <= 8


def test_fit_cubics_never_explodes_control_points():
    # nearly straight, slightly noisy line: alphas must stay bounded
    x = np.linspace(0, 20, 60)
    y = 0.02 * np.sin(x * 3)
    pts = np.stack([x, y], axis=1)
    for bez in fit_cubics(pts, 0.05):
        assert np.abs(bez).max() < 25


def test_cubic_is_line_and_flatten():
    p0, c1, c2, p3 = (0, 0), (1, 0.001), (2, -0.001), (3, 0)
    assert cubic_is_line(p0, c1, c2, p3, 0.01)
    assert not cubic_is_line((0, 0), (0, 3), (3, 3), (3, 0), 0.01)
    pts = flatten_cubic((0, 0), (0, 3), (3, 3), (3, 0), 0.01)
    assert np.allclose(pts[0], (0, 0)) and np.allclose(pts[-1], (3, 0)) and len(pts) > 4


def test_contours_to_polygons_pixel_edge_area_and_holes():
    mask = np.zeros((100, 100), np.uint8)
    mask[10:60, 20:80] = 255
    mask[30:40, 40:60] = 0
    polys = contours_to_polygons(mask)
    assert len(polys) == 1
    p = polys[0]
    assert len(p.interiors) == 1
    assert abs(p.area - (50 * 60 - 10 * 20)) < 1e-6  # exact pixel area
    assert abs(p.bounds[0] - 19.5) < 1e-9 and abs(p.bounds[2] - 79.5) < 1e-9


def test_contours_diagonal_touch_keeps_hole():
    # four squares touching only at corners enclose a white region: must be a hole
    mask = np.zeros((60, 60), np.uint8)
    mask[10:30, 10:30] = 255
    mask[30:50, 30:50] = 255
    mask[10:30, 30:50] = 0
    mask[30:50, 10:30] = 0
    # complete the enclosure with two more squares touching diagonally
    mask[10:30, 30:50] = 255
    mask[30:50, 10:30] = 255
    mask[25:35, 25:35] = 0  # small enclosed white diamond in the middle
    polys = contours_to_polygons(mask)
    assert sum(len(p.interiors) for p in polys) == 1


def test_rasterize_roundtrip_iou():
    mask = np.zeros((120, 160), np.uint8)
    mask[20:100, 30:140] = 255
    mask[50:70, 60:110] = 0
    polys = contours_to_polygons(mask)
    from lasertrace.models import Line, Path, PathGraph, Subpath
    from lasertrace.geometry.polygons import polygon_to_subpath_rings
    sps = []
    for ring, hole in polygon_to_subpath_rings(polys[0]):
        segs = [Line(p1=tuple(ring[i]), p2=tuple(ring[(i + 1) % len(ring)])) for i in range(len(ring))]
        sps.append(Subpath(segments=segs, closed=True, is_hole=hole))
    g = PathGraph(paths=[Path(subpaths=sps)], units="px", bbox=polys[0].bounds)
    # graph is in absolute pixel-centre coordinates (edges at x-0.5): shift by +0.5 to pixel-edge space
    ras = rasterize(g, 1.0, mask.shape, 0.1, offset_px=(0.5, 0.5))
    assert iou(mask, ras) > 0.995
