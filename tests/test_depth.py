"""Depth (3D relief) engraving: height-map conditioning, cumulative slicing,
hatch generation, pass plan, exports, AUTO mode and CLI."""
import json
import math

import ezdxf
import numpy as np
import pytest
from PIL import Image

from lasertrace.depth.hatch import hatch_polygon, slice_angle
from lasertrace.depth.heightmap import conditioned_height, equalize, height_to_image, photo_to_relief, quantize_height
from lasertrace.depth.materials import MATERIALS, material_params
from lasertrace.depth.plan import build_report, report_markdown
from lasertrace.depth.slicer import masks_to_height, slice_masks, slice_thresholds
from lasertrace.export import export, prepare_graph, write_depth_pack
from lasertrace.models import DepthSettings, Job, Layer, depth_layer_name
from lasertrace.pipeline import run
from lasertrace.presets import load_preset
from lasertrace.stats import hatch_angle_repeats


def relief_rgb(w: int = 400, h: int = 300) -> np.ndarray:
    """Dome + ramp height map, white = surface, black = deepest."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(xx - 110, yy - 150) / 90.0
    z = np.sqrt(np.clip(1 - r * r, 0, 1))
    z = np.maximum(z, np.where((xx > 220) & (xx < 380) & (yy > 80) & (yy < 220), (xx - 220) / 160.0, 0))
    g = (255 * (1 - z)).astype(np.uint8)
    return np.stack([g] * 3, axis=-1)


@pytest.fixture
def depth_job() -> Job:
    job = Job.from_preset(load_preset("depth-relief"))
    job.export.width_mm = 40
    job.depth.levels = 10
    job.depth.total_depth_mm = 0.5
    return job


# ----------------------------------------------------------------- height map
def test_orientation_and_zero_plane():
    rgb = relief_rgb()
    s = DepthSettings(enabled=True, smoothing_mm=0.0, floor=0.0)
    h, info = conditioned_height(rgb[..., 0], s, 10.0)
    assert h.min() == 0.0 and abs(h.max() - 1.0) < 1e-6
    assert h[0, 0] == 0.0                      # white border = surface
    assert h[150, 110] > 0.99                  # dome apex = deepest
    inv = (255 - rgb[..., 0]).astype(np.uint8)
    h2, _ = conditioned_height(inv, s.model_copy(update={"dark_is_deep": False}), 10.0)
    assert np.allclose(h, h2, atol=1e-6)


def test_floor_and_gamma_and_quantize():
    rgb = relief_rgb()
    s = DepthSettings(enabled=True, smoothing_mm=0.0, floor=0.3, depth_gamma=2.0)
    h, _ = conditioned_height(rgb[..., 0], s, 10.0)
    assert (h[(h > 0) & (h < 0.3 ** 2 - 1e-6)]).size == 0  # everything below the floor is 0 (gamma applied after)
    q = quantize_height(h, 4)
    assert set(np.unique(np.round(q * 4)).tolist()) <= {0, 1, 2, 3, 4}
    img8 = height_to_image(h, 8, True)
    assert img8.dtype == np.uint8 and img8.min() == 0 and img8.max() == 255
    img16 = height_to_image(h, 16, False)
    assert img16.dtype == np.uint16 and img16.max() == 65535


def test_smoothing_removes_terraces_keeps_walls():
    g = np.full((200, 200), 255, np.uint8)
    g[50:150, 50:150] = 0                          # a deep square with vertical walls
    g[60:140, 60:140] = 4                          # 8-bit step inside
    s = DepthSettings(enabled=True, smoothing_mm=0.1, floor=0.0)
    h, _ = conditioned_height(g, s, 10.0)          # sigma = 1 px
    assert h[100, 100] > 0.95 and h[20, 20] == 0.0
    # wall stays sharp: crosses 0.5 within ~2 px
    row = h[100, 40:60]
    idx = np.nonzero(row > 0.5)[0]
    assert 8 <= idx[0] <= 12


def test_photo_to_relief_compresses_gradients():
    yy, xx = np.mgrid[0:120, 0:160].astype(np.float32)
    lum = np.clip(0.2 + 0.6 * np.exp(-(((xx - 80) / 40) ** 2 + ((yy - 60) / 30) ** 2)), 0, 1)
    lum[:, 20:30] = 0.0                            # hard dark bar
    out = photo_to_relief(lum, 0.8)
    assert out.shape == lum.shape and 0.0 <= out.min() and out.max() <= 1.0
    # the hard bar's step is attenuated relative to the smooth blob
    step_in = abs(float(lum[60, 31] - lum[60, 25]))
    step_out = abs(float(out[60, 31] - out[60, 25]))
    assert step_out < step_in
    assert photo_to_relief(lum, 0.0) is lum


def test_equalize_blend_monotone():
    h = np.zeros((50, 50), np.float32)
    h[10:40, 10:40] = np.linspace(0.1, 1.0, 900, dtype=np.float32).reshape(30, 30) ** 3
    e = equalize(h, 1.0)
    assert e[h == 0].max() == 0
    vals, ev = h[h > 0], e[h > 0]
    order = np.argsort(vals)
    assert np.all(np.diff(ev[order]) >= -1e-6)


# ----------------------------------------------------------------- slicer
def test_thresholds_are_midpoints():
    t = slice_thresholds(4)
    assert t == [0.125, 0.375, 0.625, 0.875]


def test_slices_are_cumulative_and_nested():
    rgb = relief_rgb()
    s = DepthSettings(enabled=True, levels=8, smoothing_mm=0.0, draft_angle_deg=0.0, min_island_mm2=0.0)
    h, _ = conditioned_height(rgb[..., 0], s, 10.0)
    masks = slice_masks(h, s, 10.0)
    assert len(masks) == 8
    for i in range(1, 8):
        assert not np.any((masks[i] > 0) & (masks[i - 1] == 0))
    areas = [int((m > 0).sum()) for m in masks]
    assert all(a >= b for a, b in zip(areas, areas[1:]))
    # pass count per pixel rounds the height: reconstruct and compare
    rec = masks_to_height(masks)
    assert np.abs(rec - h)[h > 0].max() <= 0.5 / 8 + 1e-6


def test_draft_angle_insets_deeper_slices():
    g = np.full((200, 200), 255, np.uint8)
    g[40:160, 40:160] = 0
    s0 = DepthSettings(enabled=True, levels=4, total_depth_mm=1.0, smoothing_mm=0.0, draft_angle_deg=0.0)
    s1 = s0.model_copy(update={"draft_angle_deg": 20.0})
    h, _ = conditioned_height(g, s0, 10.0)
    m0 = slice_masks(h, s0, 10.0)
    m1 = slice_masks(h, s1, 10.0)
    assert (m0[3] > 0).sum() == (m0[0] > 0).sum()          # vertical walls: every slice identical
    inset_px = 0.75 * math.tan(math.radians(20)) * 10.0     # slice 4 wall starts at 0.75 mm
    expected = (120 - 2 * inset_px) ** 2
    assert abs((m1[3] > 0).sum() - expected) < 0.06 * expected


def test_min_island_removes_pits_and_specks():
    g = np.full((200, 200), 255, np.uint8)
    g[40:160, 40:160] = 0
    g[100, 100] = 255       # 1 px pit
    g[10, 10] = 0           # 1 px speck
    s = DepthSettings(enabled=True, levels=2, smoothing_mm=0.0, draft_angle_deg=0.0, min_island_mm2=0.05)
    h, _ = conditioned_height(g, s, 10.0)
    m = slice_masks(h, s, 10.0)
    assert m[0][100, 100] == 255 and m[0][10, 10] == 0


# ----------------------------------------------------------------- hatch
def test_hatch_polygon_covers_and_alternates():
    from shapely.geometry import Polygon
    poly = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])
    segs = hatch_polygon(poly, 0.5, 0.0, bidirectional=True)
    assert len(segs) == 10
    ys = [s[0][1] for s in segs]
    assert np.allclose(sorted(ys), 0.25 + 0.5 * np.arange(10))
    assert segs[0][0][0] < segs[0][1][0] and segs[1][0][0] > segs[1][1][0]   # serpentine
    rot = hatch_polygon(poly, 0.5, 45.0)
    d = rot[0][1] - rot[0][0]
    assert abs(math.degrees(math.atan2(abs(d[1]), abs(d[0]))) - 45) < 1e-6
    total = sum(float(np.hypot(*(s[1] - s[0]))) for s in segs)
    assert abs(total - 100.0) < 1e-6


def test_hatch_angle_helpers():
    from lasertrace.models import DepthHatchSettings
    hs = DepthHatchSettings(angle_start_deg=0, angle_step_deg=37)
    assert slice_angle(hs, 1) == 0 and slice_angle(hs, 2) == 37 and slice_angle(hs, 6) == (5 * 37) % 180
    assert hatch_angle_repeats(90) and hatch_angle_repeats(45) and hatch_angle_repeats(0)
    assert not hatch_angle_repeats(37) and not hatch_angle_repeats(31)


# ----------------------------------------------------------------- pipeline + plan
def test_depth_pipeline_end_to_end(depth_job):
    rgb = relief_rgb()
    po = run(rgb, depth_job)
    res = po.result
    assert res.engine == "depth" and res.depth is not None
    assert res.graph.depth_levels() == 10
    assert abs(res.stats.width_mm - 40) < 1e-6
    assert all(sp.closed for p in res.graph.paths for sp in p.subpaths)
    assert not [w for w in res.warnings if w.severity == "error"]
    idx = sorted({p.depth_index for p in res.graph.paths})
    assert idx[0] == 1 and idx[-1] == 10
    r = res.depth
    assert r.levels == 10 and abs(r.slice_thickness_mm - 0.05) < 1e-9
    assert r.slices[0].area_mm2 >= r.slices[-1].area_mm2 > 0
    assert r.slices[0].z_offset_mm == 0.0 and r.slices[-1].z_offset_mm <= -0.4
    assert r.machine["passes_per_slice"] >= 1 and r.machine["raster_passes"] >= r.machine["passes_per_slice"]
    assert r.footprint_mm2 == r.slices[0].area_mm2
    assert po.height is not None and len(po.masks) == 10
    assert "DEPTH_01" in report_markdown(r)


def test_depth_pipeline_deterministic(depth_job):
    rgb = relief_rgb()
    a = run(rgb, depth_job).result.graph
    b = run(rgb, depth_job).result.graph
    strip = lambda g: [(p.depth_index, [[s.model_dump() for s in sp.segments] for sp in p.subpaths]) for p in g.paths]  # noqa: E731
    assert strip(a) == strip(b)


def test_depth_hatch_layers(depth_job):
    depth_job.depth.hatch.enabled = True
    depth_job.depth.hatch.spacing_mm = 0.1
    depth_job.depth.levels = 4
    res = run(relief_rgb(), depth_job).result
    hatch = [p for p in res.graph.paths if p.layer == Layer.ENGRAVE_LINE]
    assert hatch and {p.depth_index for p in hatch} == {1, 2, 3, 4}
    assert all(p.layer_name().endswith("_HATCH") for p in hatch)
    assert res.depth.slices[0].hatch_length_mm > res.depth.slices[-1].hatch_length_mm > 0


def test_plan_material_scaling():
    m = material_params("brass")
    assert m.scaled_removal_um(m.ref_w) == m.removal_um
    assert m.scaled_removal_um(m.ref_w / 3) < m.removal_um
    assert material_params("nope").key == "generic"
    s = DepthSettings(enabled=True, levels=5, total_depth_mm=0.5, material="stainless", laser_w=60, removal_per_pass_um=20)
    from lasertrace.models import PathGraph
    r = build_report(s, PathGraph(), [10, 8, 6, 4, 2])
    assert r.removal_per_pass_um == 20 and r.machine["passes_per_slice"] == 5    # 0.1 mm / 20 um
    assert abs(r.volume_mm3 - 3.0) < 1e-6
    assert set(MATERIALS) >= {"stainless", "brass", "aluminum", "copper"}


def test_flat_image_gives_error(depth_job):
    flat = np.full((100, 100, 3), 255, np.uint8)
    res = run(flat, depth_job).result
    assert any(w.code == "DEPTH_EMPTY" and w.severity == "error" for w in res.warnings)


# ----------------------------------------------------------------- export
def test_depth_dxf_layers_and_pack(depth_job, tmp_path):
    rgb = relief_rgb()
    po = run(rgb, depth_job)
    res = po.result
    out = tmp_path / "d.dxf"
    export(res, depth_job.export, out)
    doc = ezdxf.readfile(str(out))
    names = sorted(l.dxf.name for l in doc.layers if l.dxf.name.startswith("DEPTH_"))
    assert names == [depth_layer_name(i) for i in range(1, 11)]
    assert len({doc.layers.get(n).color for n in names}) == 10          # distinct colours for LightBurn
    for e in doc.modelspace():
        assert e.dxftype() == "LWPOLYLINE" and e.closed
    jj = json.dumps(depth_job.model_dump(mode="json"))
    files = write_depth_pack(res, depth_job, tmp_path / "pack", height=po.height, stem="t", job_json=jj)
    names = {f.name for f in files}
    assert {"t_depth.dxf", "t_heightmap.png", "t_preview.png", "t_plan.md", "t_plan.csv", "t_plan.json"} <= names
    assert sum(1 for f in files if f.parent.name == "slices") == 10
    s1 = ezdxf.readfile(str(tmp_path / "pack" / "slices" / "t_DEPTH_01.dxf"))
    layers1 = {e.dxf.layer for e in s1.modelspace()}
    assert layers1 == {"DEPTH_01", "IGNORE"}                           # slice + alignment frame
    img = Image.open(tmp_path / "pack" / "t_heightmap.png")
    a = np.asarray(img)
    assert img.mode == "L" and a.min() == 0 and a.max() == 255          # full range, black = deep
    assert a[0, 0] == 255                                               # corner = untouched surface = white
    dpi = img.info.get("dpi", (0, 0))[0]
    assert abs(dpi - 25.4 / 0.025) < 1.0
    plan = json.loads((tmp_path / "pack" / "t_plan.json").read_text())
    assert plan["report"]["levels"] == 10 and plan["job"]["depth"]["enabled"]
    # png via export(): height map, not a 1-bit mask
    export(res, depth_job.export, tmp_path / "h.png", height=po.height, job=depth_job)
    assert Image.open(tmp_path / "h.png").mode == "L"
    # svg groups
    export(res, depth_job.export, tmp_path / "d.svg")
    svg = (tmp_path / "d.svg").read_text()
    assert 'id="DEPTH_01"' in svg and 'id="DEPTH_10"' in svg


def test_scaled_export_keeps_depth_index(depth_job, tmp_path):
    res = run(relief_rgb(), depth_job).result
    prof = depth_job.export.model_copy(update={"width_mm": 20})
    export(res, prof, tmp_path / "s.dxf")
    doc = ezdxf.readfile(str(tmp_path / "s.dxf"))
    assert "DEPTH_10" in doc.layers
    xs = [p[0] for e in doc.modelspace() for p in e.get_points("xy")]
    assert abs(max(xs) - 20) < 0.05


# ----------------------------------------------------------------- auto + cli
def test_auto_depth_mode():
    from lasertrace.auto import analyze_depth, build_job
    rgb = relief_rgb()
    d = analyze_depth(rgb)
    assert d.surface_is_light and not d.looks_like_photo
    job = build_job(rgb, "auto-depth", 30)
    assert job.depth.enabled and job.depth.dark_is_deep and not job.depth.photo_to_relief
    assert 4 <= job.depth.levels <= 30 and "height map" in job.notes
    inv = (255 - rgb).astype(np.uint8)
    job2 = build_job(inv, "auto-depth", 30)
    assert not job2.depth.dark_is_deep
    res = run(inv, job2).result
    assert res.depth is not None and res.graph.depth_levels() == job2.depth.levels
    # photo-like input flips on photo_to_relief
    rng = np.random.default_rng(0)
    noisy = np.clip(rgb.astype(np.float32) + rng.normal(0, 25, rgb.shape), 0, 255).astype(np.uint8)
    job3 = build_job(noisy, "auto-depth", 30)
    assert job3.depth.photo_to_relief


def test_cli_depth_pack(tmp_path):
    from lasertrace.cli import main as cli_main
    src = tmp_path / "relief.png"
    Image.fromarray(relief_rgb()[..., 0]).save(src)
    out = tmp_path / "r.dxf"
    stats = tmp_path / "s.json"
    rc = cli_main([str(src), "--preset", "depth-relief", "--depth-levels", "6", "--depth-mm", "0.3", "--material", "brass", "--width-mm", "30",
                   "--out", str(out), "--depth-pack", str(tmp_path / "pack"), "--stats", str(stats), "-q"])
    assert rc == 0
    st = json.loads(stats.read_text())
    assert st["depth"]["levels"] == 6 and st["depth"]["material"] == "brass" and abs(st["depth"]["total_depth_mm"] - 0.3) < 1e-9
    assert (tmp_path / "pack" / "relief_plan.md").exists() and (tmp_path / "pack" / "slices" / "relief_DEPTH_06.dxf").exists()
    doc = ezdxf.readfile(str(out))
    assert "DEPTH_06" in doc.layers and "DEPTH_07" not in doc.layers


# --------------------------------------------------------------------- regressions

def test_depth_frame_is_the_relief_footprint(depth_job, tmp_path):
    """The reported size, the exported DXF and the exported height map must all
    mean the same millimetres.

    Regression: the graph used to be framed on slice 1's bbox (inset, because
    slice 1 only holds what is deeper than half a slice) while px/mm came from
    the `height > 0` footprint the PNG is cropped to. The status bar under-
    reported the size and the PNG came out at a different scale than the DXF.
    """
    rgb = relief_rgb()
    res = run(rgb, depth_job)
    target = depth_job.export.width_mm

    assert abs(res.result.stats.width_mm - target) < 0.05, res.result.stats.width_mm
    graph = prepare_graph(res.result.graph, depth_job.export)
    assert abs(graph.width - target) < 0.05

    write_depth_pack(res.result, depth_job, tmp_path, height=res.height, stem="r", job_json=None)
    png = Image.open(tmp_path / "r_heightmap.png")
    png_mm = png.width / png.info["dpi"][0] * 25.4
    assert abs(png_mm - target) < 0.2, png_mm

    doc = ezdxf.readfile(str(tmp_path / "r_depth.dxf"))
    xs = [v[0] for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE" for v in e.get_points()]
    assert abs((max(xs) - min(xs)) - target) < 0.1


def test_alignment_frame_survives_layer_flattening(depth_job, tmp_path):
    """Flattening merges the mark layers; it must never swallow IGNORE, or the
    per-slice registration frame is engraved as a box on every pass."""
    depth_job.export.flatten_layers = True
    res = run(relief_rgb(), depth_job)
    write_depth_pack(res.result, depth_job, tmp_path, height=res.height, stem="r", job_json=None)
    doc = ezdxf.readfile(str(tmp_path / "slices" / "r_DEPTH_01.dxf"))
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert "IGNORE" in layers
    assert "ENGRAVE" not in layers, layers


def test_hatch_does_not_inflate_the_time_estimate(depth_job):
    """Regression: the measured-hatch branch divided by the pitch twice, so
    turning hatch generation on roughly doubled the estimate for the same job."""
    rgb = relief_rgb()
    depth_job.depth.hatch.enabled = False
    off = run(rgb, depth_job).result.depth
    depth_job.depth.hatch.enabled = True
    on = run(rgb, depth_job).result.depth
    assert off is not None and on is not None and off.est_time_s > 0
    assert 0.5 < on.est_time_s / off.est_time_s < 1.6, (off.est_time_s, on.est_time_s)


def test_equalize_keeps_flat_regions_flat():
    """Regression: ranks were handed out in argsort order, so tied heights got
    consecutive ranks and a flat floor came back as a raster-scan ramp."""
    flat = np.full((40, 40), 0.5, np.float32)
    out = equalize(flat, 1.0)
    assert float(out.max() - out.min()) < 1e-6
    assert abs(float(out.mean()) - 0.5) < 1e-6
    # a genuine gradient still equalises, and stays monotonic
    ramp = np.tile(np.linspace(0.1, 1.0, 40, dtype=np.float32), (40, 1))
    eq = equalize(ramp, 1.0)
    assert np.all(np.diff(eq[0]) >= -1e-6)
    assert eq.max() - eq.min() > 0.9
