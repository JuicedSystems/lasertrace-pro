"""End-to-end pipeline: rgb -> binary -> PathGraph (mm) -> hygiene -> stats.

Deterministic: no randomness, paths sorted by the planar union, coordinates
rounded on output.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import numpy as np

from .engines import get_engine
from .engines.base import EngineContext
from .engines.contour import ring_to_subpath
from .hygiene import (add_cut_outer, close_gaps, dedupe_subpaths, ensure_winding, join_open_chains,
                      merge_collinear_lines, remove_micro_paths, union_overlapping_fills)
from .hygiene.simplify import round_graph
from .models import Job, Layer, PathGraph, PreprocessOp, PreprocessStack, TraceResult, Warning
from .preprocess import morphology, run_stack_with_intermediates, threshold
from .stats import build_warnings, compute_stats
from .units import px_per_mm_for_width

DEFAULT_WIDTH_MM = 50.0


@dataclass
class PipelineOutput:
    result: TraceResult
    binary: np.ndarray
    intermediates: list[tuple[str, np.ndarray]] = field(default_factory=list)
    px_per_mm: float = 1.0
    # depth (3D relief) jobs only
    height: np.ndarray | None = None          # conditioned float32 height, full image frame (0 surface .. 1 deepest)
    masks: list[np.ndarray] = field(default_factory=list)   # one nested uint8 mask per slice


def _ink_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def estimate_px_per_mm(rgb: np.ndarray, job: Job) -> float:
    """Pass-1 estimate so mm-based preprocess ops work before the real bbox is known."""
    from .ingest import to_gray
    target_w = job.export.width_mm or DEFAULT_WIDTH_MM
    g = to_gray(rgb)
    m = threshold.otsu(g)
    inv = job.stack.find("invert")
    if inv is not None and inv.enabled:
        m = morphology.invert(m)
    bb = _ink_bbox(m)
    w = (bb[2] - bb[0]) if bb else rgb.shape[1]
    return px_per_mm_for_width(max(1, w), target_w)


def effective_stack(job: Job) -> PreprocessStack:
    """Apply the Threshold slider to the stack's threshold op (manual value or Otsu bias)."""
    stack = job.stack.model_copy(deep=True)
    t = stack.find("threshold")
    if t is None:
        stack.ops.insert(0, PreprocessOp(op="threshold", params={"method": "otsu", "bias": job.trace.threshold - 128}))
    else:
        method = t.params.get("method", "otsu")
        if method == "manual":
            t.params["value"] = job.trace.threshold
        elif method == "otsu":
            t.params["bias"] = job.trace.threshold - 128
    return stack


def run_preprocess(rgb: np.ndarray, job: Job, px_per_mm: float | None = None) -> tuple[np.ndarray, list[tuple[str, np.ndarray]], float]:
    ppm = px_per_mm or estimate_px_per_mm(rgb, job)
    stack = effective_stack(job)
    binary, steps, _ = run_stack_with_intermediates(rgb, stack, ppm)
    if job.trace.despeckle_mm2 > 0:
        binary = morphology.despeckle(binary, min_area_mm2=job.trace.despeckle_mm2, px_per_mm=ppm)
        steps.append(("despeckle(slider)", binary))
    return binary, steps, ppm


def trace_binary(binary: np.ndarray, job: Job, px_per_mm: float | None = None, source_scale: float = 1.0) -> tuple[TraceResult, float]:
    """Trace an already-binarized mask (255 = ink). Returns (result, px_per_mm).
    `source_scale` = binary px / original px, so upscaled inputs keep pixel-level tolerances."""
    t_all = time.perf_counter()
    s = job.trace
    target_w = job.export.width_mm
    bb = _ink_bbox(binary)
    if bb is None:
        ppm = px_per_mm or 1.0
        res = TraceResult(graph=PathGraph(px_per_mm=ppm), stats=compute_stats(PathGraph()), warnings=[Warning(code="EMPTY", message="No ink found. Check the threshold / invert.", severity="error")], engine=s.engine)
        return res, ppm
    if target_w:
        ppm = px_per_mm_for_width(bb[2] - bb[0], target_w)
    elif px_per_mm:
        ppm = px_per_mm
    else:
        ppm = px_per_mm_for_width(bb[2] - bb[0], DEFAULT_WIDTH_MM)

    ctx = EngineContext(px_per_mm=ppm, settings=s, source_scale=source_scale)
    engine_name = s.engine
    if s.mode == "centerline":
        engine_name = "centerline"
    elif s.mode == "hybrid":
        engine_name = "hybrid"
    binary_sha = hashlib.sha256(binary.tobytes()).hexdigest()[:16]

    # --- node budget loop: retrace with looser tolerance until under budget ---
    graph = None
    tol_scale = 1.0
    for attempt in range(6):
        ctx.settings = s.model_copy(update={"curve_tol_mm": s.curve_tol_mm * tol_scale})
        ctx.warnings = []
        graph = _trace_with_engine(engine_name, binary, ctx)
        if not s.node_budget or graph.node_count() <= s.node_budget:
            break
        tol_scale *= 1.6
    assert graph is not None
    if s.node_budget and graph.node_count() > s.node_budget:
        ctx.warn("NODE_BUDGET_EXCEEDED", f"Could not get under {s.node_budget} nodes ({graph.node_count()}).", "warn")

    # --- hygiene ---
    t_h = time.perf_counter()
    refit = lambda ring, is_hole: ring_to_subpath(ring, ctx, is_hole)  # noqa: E731
    ensure_winding(graph)
    close_gaps(graph, s.gap_close_mm)
    if any(p.layer == Layer.ENGRAVE_LINE for p in graph.paths):
        join_open_chains(graph, max(s.gap_close_mm, 2.0 / ppm))
    min_area = max(s.min_feature_mm ** 2, 1e-6)
    removed, _ = remove_micro_paths(graph, min_area, s.min_feature_mm)
    if removed:
        ctx.warn("MICRO_PATHS_REMOVED", f"Removed {removed} path(s)/hole(s) smaller than {s.min_feature_mm} mm.", "info")
    dedupe_subpaths(graph)
    if s.union_fills:
        rebuilt = union_overlapping_fills(graph, refit)
        if rebuilt:
            ctx.warn("OVERLAPS_UNIONED", f"Unioned {rebuilt} overlapping fill region(s) to prevent double marking.", "info")
    merge_collinear_lines(graph)
    if s.cut_outer:
        add_cut_outer(graph, refit)
    round_graph(graph, 4)
    ctx.timings_ms["hygiene"] = (time.perf_counter() - t_h) * 1000

    t_s = time.perf_counter()
    st = compute_stats(graph, binary, ppm)
    ctx.timings_ms["stats"] = (time.perf_counter() - t_s) * 1000
    ctx.timings_ms["total"] = (time.perf_counter() - t_all) * 1000
    st.time_ms = dict(ctx.timings_ms)
    warnings = build_warnings(graph, st, s, binary, ppm, ctx.warnings)
    res = TraceResult(graph=graph, stats=st, warnings=warnings, binary_sha=binary_sha, engine=engine_name, preset_name=job.preset_name)
    return res, ppm


def _trace_with_engine(name: str, binary: np.ndarray, ctx: EngineContext) -> PathGraph:
    if name == "hybrid":
        return _trace_hybrid(binary, ctx)
    try:
        eng = get_engine(name)
    except ValueError:
        ctx.warn("ENGINE_FALLBACK", f"Unknown engine {name!r}; used contour.", "warn")
        eng = get_engine("contour")
    if not eng.available():
        ctx.warn("ENGINE_FALLBACK", f"Engine {name!r} is not available on this machine; used contour.", "warn")
        eng = get_engine("contour")
    try:
        return eng.trace(binary, ctx)
    except Exception as e:  # engine crashed: fall back rather than lose the job
        if eng.name == "contour":
            raise
        ctx.warn("ENGINE_FALLBACK", f"Engine {eng.name} failed ({e}); used contour.", "warn")
        return get_engine("contour").trace(binary, ctx)


def _trace_hybrid(binary: np.ndarray, ctx: EngineContext) -> PathGraph:
    """Filled regions -> outline engine; thin strokes -> centerline engine.

    Split by local stroke width: an opening with a disk of diameter
    `centerline_max_width_mm` keeps only the thick regions; everything else is
    a thin stroke and goes to the centerline engine."""
    import cv2
    from .hygiene.simplify import transform_graph
    from .units import Transform
    s = ctx.settings
    ppm = ctx.px_per_mm
    ink = (binary > 0).astype(np.uint8)
    max_w_px = s.centerline_max_width_mm * ppm
    r = max(1, int(round(max_w_px / 2.0)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    thick_core = cv2.morphologyEx(ink, cv2.MORPH_OPEN, k)
    thick = cv2.bitwise_and(cv2.dilate(thick_core, k), ink)
    thin = cv2.bitwise_and(ink, cv2.bitwise_not(thick))
    ys, xs = np.nonzero(ink)
    ox, oy = float(xs.min()), float(ys.min())
    paths = []
    for mask, engine_name in ((thick * 255, "contour"), (thin * 255, "centerline")):
        if mask.max() == 0:
            continue
        eng = get_engine(engine_name)
        if not eng.available():
            eng = get_engine("contour")
        g = eng.trace(mask, ctx)
        # each engine puts its own bbox origin at 0,0; shift into the common frame
        mys, mxs = np.nonzero(mask)
        dx, dy = (float(mxs.min()) - ox) / ppm, (float(mys.min()) - oy) / ppm
        g = transform_graph(g, Transform(1, 1, dx, dy))
        paths.extend(g.paths)
    bbox = (0.0, 0.0, (float(xs.max()) + 1 - ox) / ppm, (float(ys.max()) + 1 - oy) / ppm)
    return PathGraph(paths=paths, units="mm", bbox=bbox, px_per_mm=ppm)


def run_depth(rgb: np.ndarray, job: Job) -> PipelineOutput:
    """Depth (3D relief) pipeline: colour-stage preprocess -> gray -> conditioned
    height field -> N cumulative slices -> contour-traced layers (+ optional
    hatch) -> pass plan. The threshold / binary stages of the stack are not
    used: a height map is not binarised."""
    from .depth.hatch import add_hatch_paths
    from .depth.heightmap import conditioned_height, height_stats, orient, zero_plane_level
    from .depth.plan import build_report
    from .depth.slicer import footprint_bbox, slice_masks, trace_slices
    from .preprocess.ops import gray_before_threshold
    from .stats import build_depth_warnings

    t0 = time.perf_counter()
    s = job.depth
    target_w = job.export.width_mm or DEFAULT_WIDTH_MM
    inv = job.stack.find("invert")
    dark_is_deep = s.dark_is_deep != bool(inv is not None and inv.enabled)
    ds = s.model_copy(update={"dark_is_deep": dark_is_deep})

    # colour-stage ops only (crop / rotate / upscale / denoise ...)
    gray = gray_before_threshold(rgb, job.stack, None)
    source_scale = gray.shape[1] / max(1, rgb.shape[1])

    # pass 1: rough footprint from the raw oriented height -> px/mm for mm-based conditioning
    h0 = orient(gray, ds.dark_is_deep, ds.black_point, ds.white_point)
    h0 = np.clip(h0 - zero_plane_level(h0, ds.zero_plane), 0, 1)
    bb0 = _ink_bbox((h0 > max(ds.floor, 0.02)).astype(np.uint8))
    ppm = px_per_mm_for_width((bb0[2] - bb0[0]) if bb0 else gray.shape[1], target_w)

    height, info = conditioned_height(gray, ds, ppm)
    t_cond = (time.perf_counter() - t0) * 1000
    fp_bb = _ink_bbox((height > 0).astype(np.uint8))
    if fp_bb is None:
        res = TraceResult(graph=PathGraph(px_per_mm=ppm), stats=compute_stats(PathGraph()), engine="depth",
                          warnings=[Warning(code="DEPTH_EMPTY", message="No relief found: the image is flat after conditioning. Check dark/white = deep, the floor, and the zero plane.", severity="error")])
        return PipelineOutput(result=res, binary=np.zeros(gray.shape, np.uint8), px_per_mm=ppm, height=height, masks=[])
    ppm = px_per_mm_for_width(fp_bb[2] - fp_bb[0], target_w)

    t1 = time.perf_counter()
    masks = slice_masks(height, ds, ppm)
    t_slice = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    ctx = EngineContext(px_per_mm=ppm, settings=job.trace, source_scale=source_scale)
    # frame on the relief footprint, the same region the height PNG is cropped
    # to and the region `ppm` was derived from, so the exported DXF, the
    # exported height map and the reported size all mean the same millimetres
    graph, areas = trace_slices(masks, ctx, frame_bbox=fp_bb)
    ensure_winding(graph)
    min_area = max(job.trace.min_feature_mm ** 2, 1e-6)
    removed, _ = remove_micro_paths(graph, min_area, job.trace.min_feature_mm)
    if removed:
        ctx.warn("MICRO_PATHS_REMOVED", f"Removed {removed} slice path(s)/pit(s) smaller than {job.trace.min_feature_mm} mm.", "info")
    merge_collinear_lines(graph)
    t_trace = (time.perf_counter() - t2) * 1000

    t3 = time.perf_counter()
    hatch_lengths = add_hatch_paths(graph, ds.hatch, job.export.flatten_tol_mm)
    round_graph(graph, 4)
    t_hatch = (time.perf_counter() - t3) * 1000

    hs = height_stats(height)
    notes = [f"{k}={v}" for k, v in info.items()]
    report = build_report(ds, graph, areas, hatch_lengths, (round(hs["min"], 4), round(hs["max"], 4)), notes)
    st = compute_stats(graph, None, ppm)   # IoU / hole counts are for flat art; stacked slices would double count
    st.fill_area_mm2 = report.footprint_mm2
    st.holes = sum(1 for p in graph.paths if p.depth_index == 1 for sp in p.subpaths if sp.is_hole)
    st.time_ms = {"conditioning": t_cond, "slicing": t_slice, "trace": t_trace, "hatch": t_hatch, "total": (time.perf_counter() - t0) * 1000}
    hatch_capped = bool(ds.hatch.enabled and len(hatch_lengths) < sum(1 for a in areas if a > 0))
    warnings = build_depth_warnings(report, hs, masks, ds, ppm, ctx.warnings, hatch_capped=hatch_capped)
    binary_sha = hashlib.sha256(masks[0].tobytes()).hexdigest()[:16] if masks else ""
    res = TraceResult(graph=graph, stats=st, warnings=warnings, binary_sha=binary_sha, engine="depth", preset_name=job.preset_name, depth=report)
    from .depth.heightmap import height_to_image
    steps = [("height", height_to_image(height, 8, True))] + [(f"slice {i:02d}", m) for i, m in enumerate(masks, start=1)]
    return PipelineOutput(result=res, binary=masks[0], intermediates=steps, px_per_mm=ppm, height=height, masks=masks)


def run(rgb: np.ndarray, job: Job) -> PipelineOutput:
    if job.depth.enabled:
        return run_depth(rgb, job)
    t0 = time.perf_counter()
    binary, steps, ppm_est = run_preprocess(rgb, job)
    t_pre = (time.perf_counter() - t0) * 1000
    source_scale = binary.shape[1] / max(1, rgb.shape[1])
    result, ppm = trace_binary(binary, job, ppm_est, source_scale=source_scale)
    result.stats.time_ms["preprocess"] = t_pre
    return PipelineOutput(result=result, binary=binary, intermediates=steps, px_per_mm=ppm)
