import numpy as np

from lasertrace.hygiene import close_gaps, dedupe_subpaths, remove_micro_paths, union_overlapping_fills
from lasertrace.hygiene.topology import detect_overlaps, ensure_winding
from lasertrace.hygiene.simplify import merge_collinear_lines, scale_graph
from lasertrace.models import Job, Layer, Line, Path, PathGraph, Subpath
from lasertrace.pipeline import run, trace_binary
from lasertrace.presets import list_presets, load_preset
from lasertrace.ingest import to_gray
from lasertrace.preprocess import threshold


def square(x, y, s, hole=False):
    pts = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    if hole:
        pts = pts[::-1]
    segs = [Line(p1=pts[i], p2=pts[(i + 1) % 4]) for i in range(4)]
    return Subpath(segments=segs, closed=True, is_hole=hole)


def test_remove_micro_paths_by_area():
    g = PathGraph(paths=[Path(subpaths=[square(0, 0, 10)]), Path(subpaths=[square(20, 20, 0.05)])], bbox=(0, 0, 30, 30))
    removed, ids = remove_micro_paths(g, min_area_mm2=0.01, min_len_mm=0.1)
    assert removed == 1 and len(g.paths) == 1 and len(ids) == 1


def test_remove_micro_holes_but_keep_big_ones():
    p = Path(subpaths=[square(0, 0, 10), square(2, 2, 3, hole=True), square(7, 7, 0.05, hole=True)])
    g = PathGraph(paths=[p], bbox=(0, 0, 10, 10))
    remove_micro_paths(g, min_area_mm2=0.01, min_len_mm=0.1)
    assert g.paths[0].holes() == 1


def test_close_gaps():
    segs = [Line(p1=(0, 0), p2=(10, 0)), Line(p1=(10, 0), p2=(10, 10)), Line(p1=(10, 10), p2=(0, 10)), Line(p1=(0, 10), p2=(0, 0.03))]
    g = PathGraph(paths=[Path(subpaths=[Subpath(segments=segs, closed=False)])], bbox=(0, 0, 10, 10))
    assert close_gaps(g, 0.05) == 1
    sp = g.paths[0].subpaths[0]
    assert sp.closed and len(sp.segments) == 5


def test_dedupe_identical_paths():
    g = PathGraph(paths=[Path(subpaths=[square(0, 0, 5)]), Path(subpaths=[square(0, 0, 5)])], bbox=(0, 0, 5, 5))
    assert dedupe_subpaths(g) == 1 and len(g.paths) == 1


def test_ensure_winding_flips_reversed_outer():
    sp = square(0, 0, 5)
    sp.segments = [Line(p1=s.p2, p2=s.p1) for s in reversed(sp.segments)]  # reversed = negative area
    g = PathGraph(paths=[Path(subpaths=[sp])], bbox=(0, 0, 5, 5))
    assert ensure_winding(g) == 1
    assert ensure_winding(g) == 0


def test_union_overlapping_fills_produces_single_planar_region():
    from lasertrace.engines.base import EngineContext
    from lasertrace.engines.contour import ring_to_subpath
    from lasertrace.models import TraceSettings
    g = PathGraph(paths=[Path(subpaths=[square(0, 0, 10)]), Path(subpaths=[square(5, 5, 10)])], bbox=(0, 0, 15, 15))
    area, pairs = detect_overlaps(g)
    assert area > 20 and len(pairs) == 1
    ctx = EngineContext(px_per_mm=10, settings=TraceSettings(smoothness=0.0))
    rebuilt = union_overlapping_fills(g, lambda r, h: ring_to_subpath(r, ctx, h))
    assert rebuilt == 1 and len(g.paths) == 1
    area, _ = detect_overlaps(g)
    assert area == 0


def test_merge_collinear_lines():
    segs = [Line(p1=(0, 0), p2=(5, 0)), Line(p1=(5, 0), p2=(10, 0)), Line(p1=(10, 0), p2=(10, 10)), Line(p1=(10, 10), p2=(0, 10)), Line(p1=(0, 10), p2=(0, 0))]
    g = PathGraph(paths=[Path(subpaths=[Subpath(segments=segs, closed=True)])], bbox=(0, 0, 10, 10))
    assert merge_collinear_lines(g) == 1
    assert len(g.paths[0].subpaths[0].segments) == 4


def test_scale_graph_to_width():
    g = PathGraph(paths=[Path(subpaths=[square(0, 0, 10)])], bbox=(0, 0, 10, 10))
    s = scale_graph(g, 50, None)
    assert abs(s.width - 50) < 1e-9 and abs(s.height - 50) < 1e-9
    assert s.paths[0].subpaths[0].segments[1].p2 == (50.0, 50.0)


def test_all_presets_load_and_have_shop_names():
    ps = list_presets()
    names = {p.name for p in ps}
    for required in ["logo-fill", "thin-line-art", "stamp-stencil", "photo-to-plate", "small-text", "qr-datamatrix", "dirty-phone-photo", "cut-outer-engrave-inner"]:
        assert required in names
    for p in ps:
        assert p.display_name and p.trace.engine


def test_pipeline_end_to_end_ring(ring_rgb):
    job = Job.from_preset(load_preset("logo-fill"))
    job.export.width_mm = 40
    po = run(ring_rgb, job)
    r = po.result
    assert r.stats.paths == 1 and r.stats.holes == 1
    assert abs(r.stats.width_mm - 40) < 1e-6
    assert r.stats.iou_vs_binary > 0.98
    assert not [w for w in r.warnings if w.severity == "error"]
    assert r.stats.open_paths == 0


def test_pipeline_deterministic(ring_rgb):
    job = Job.from_preset(load_preset("logo-fill"))
    a = run(ring_rgb, job).result
    b = run(ring_rgb, job).result
    assert a.graph.model_dump(exclude={"paths": {"__all__": {"id"}}}) == b.graph.model_dump(exclude={"paths": {"__all__": {"id"}}})


def test_node_budget_is_enforced(circle_rgb):
    job = Job.from_preset(load_preset("logo-fill"))
    job.trace.node_budget = 12
    job.trace.detail = 1.0
    r = run(circle_rgb, job).result
    assert r.stats.nodes <= 12 or any(w.code == "NODE_BUDGET_EXCEEDED" for w in r.warnings)


def test_cut_outer_adds_cut_layer(ring_rgb):
    job = Job.from_preset(load_preset("cut-outer-engrave-inner"))
    r = run(ring_rgb, job).result
    layers = {p.layer for p in r.graph.paths}
    assert Layer.CUT in layers and Layer.ENGRAVE_FILL in layers
    cut = [p for p in r.graph.paths if p.layer == Layer.CUT]
    assert len(cut) == 1 and cut[0].holes() == 0


def test_empty_image_warns_not_crashes():
    rgb = np.full((100, 100, 3), 255, np.uint8)
    job = Job.from_preset(load_preset("logo-fill"))
    r = run(rgb, job).result
    assert r.stats.paths == 0 and any(w.code == "EMPTY" for w in r.warnings)


def test_thin_line_art_preset_uses_centerline(lines_rgb):
    job = Job.from_preset(load_preset("thin-line-art"))
    r = run(lines_rgb, job).result
    assert r.engine == "centerline"
    assert all(p.layer == Layer.ENGRAVE_LINE for p in r.graph.paths)
    assert not any(w.code == "HOLES_LOST" for w in r.warnings)


def test_trace_binary_direct(circle_rgb):
    m = threshold.otsu(to_gray(circle_rgb))
    job = Job.from_preset(load_preset("logo-fill"))
    r, ppm = trace_binary(m, job)
    assert r.stats.paths == 1 and abs(ppm - 501 / 50) < 1e-6  # ellipse 50..550 inclusive = 501 px
