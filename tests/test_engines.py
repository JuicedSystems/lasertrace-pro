import numpy as np
import pytest

from lasertrace.engines import available_engines, get_engine
from lasertrace.engines.base import EngineContext
from lasertrace.ingest import to_gray
from lasertrace.models import Layer, TraceSettings
from lasertrace.preprocess import threshold


def ctx_for(width_px: int, width_mm: float = 50.0, **kw) -> EngineContext:
    return EngineContext(px_per_mm=width_px / width_mm, settings=TraceSettings(**kw))


def test_contour_circle_is_few_nodes(circle_rgb):
    m = threshold.otsu(to_gray(circle_rgb))
    g = get_engine("contour").trace(m, ctx_for(500))
    assert len(g.paths) == 1
    assert g.node_count() <= 24
    assert all(sp.closed for p in g.paths for sp in p.subpaths)
    assert abs(g.width - 50.1) < 1e-6  # PIL ellipse 50..550 inclusive = 501 px at 10 px/mm


def test_contour_ring_preserves_hole(ring_rgb):
    m = threshold.otsu(to_gray(ring_rgb))
    g = get_engine("contour").trace(m, ctx_for(500))
    assert len(g.paths) == 1
    assert g.paths[0].holes() == 1
    outer, hole = g.paths[0].subpaths
    assert not outer.is_hole and hole.is_hole


def test_contour_letter_b_two_counters(letter_b_rgb):
    m = threshold.otsu(to_gray(letter_b_rgb))
    g = get_engine("contour").trace(m, ctx_for(300))
    assert len(g.paths) == 1 and g.paths[0].holes() == 2
    # a blocky B is all straight edges: expect lines only, 4 nodes per ring
    assert g.node_count() == 12


def test_contour_rectangle_is_four_lines(rect_rgb):
    m = threshold.otsu(to_gray(rect_rgb))
    g = get_engine("contour").trace(m, ctx_for(600))
    sp = g.paths[0].subpaths[0]
    assert len(sp.segments) == 4 and all(s.kind == "line" for s in sp.segments)


def test_polyline_mode_when_smoothness_zero(circle_rgb):
    m = threshold.otsu(to_gray(circle_rgb))
    g = get_engine("contour").trace(m, ctx_for(500, smoothness=0.0))
    assert all(s.kind == "line" for p in g.paths for sp in p.subpaths for s in sp.segments)


def test_engine_output_is_deterministic(ring_rgb):
    m = threshold.otsu(to_gray(ring_rgb))
    a = get_engine("contour").trace(m, ctx_for(500))
    b = get_engine("contour").trace(m, ctx_for(500))
    strip = lambda g: [[[s.model_dump() for s in sp.segments] for sp in p.subpaths] for p in g.paths]  # noqa: E731
    assert strip(a) == strip(b)


def test_centerline_lines_become_open_paths(lines_rgb):
    m = threshold.otsu(to_gray(lines_rgb))
    eng = get_engine("centerline")
    if not eng.available():
        pytest.skip("scikit-image missing")
    g = eng.trace(m, ctx_for(500, mode="centerline"))
    assert g.paths and all(p.layer == Layer.ENGRAVE_LINE for p in g.paths)
    # 3 px strokes at 10 px/mm -> ~0.3 mm reported width
    widths = [p.stroke_width_mm for p in g.paths]
    assert all(0.15 <= w <= 0.5 for w in widths)
    # centerline of a 3px stroke must NOT be an outline pair: node count small
    assert g.node_count() < 60


def test_available_engines_lists_contour():
    av = available_engines()
    assert av["contour"] is True
    assert "potrace" in av and "centerline" in av


@pytest.mark.skipif(not get_engine("potrace").available(), reason="potrace sidecar not available")
def test_potrace_sidecar_ring(ring_rgb):
    m = threshold.otsu(to_gray(ring_rgb))
    g = get_engine("potrace").trace(m, ctx_for(500))
    assert len(g.paths) == 1 and g.paths[0].holes() == 1
    assert 4 <= g.node_count() <= 60
