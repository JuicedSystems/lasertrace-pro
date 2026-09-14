"""Headless CLI.

  lasertrace input.png --preset logo-fill --width-mm 50 --out out.dxf
  lasertrace input.png --preset qr --out out.svg --out out.dxf --stats stats.json
  lasertrace --batch in_dir --out-dir out_dir --preset logo-fill --format dxf
  lasertrace input.png --classify
  lasertrace --list-presets
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from . import __version__
from .export import export
from .ingest import load_file, supported_ext
from .models import Job
from .pipeline import run
from .preprocess.classify import classify
from .presets import list_presets, load_preset


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lasertrace", description="Laser-first black & white vectorizer")
    ap.add_argument("input", nargs="?", help="image file (png/jpg/webp/bmp/tif/gif/pdf/svg)")
    ap.add_argument("--preset", "-p", default=None, help="preset or AUTO mode (auto, auto-bw, auto-photo, auto-lines, auto-depth; see --list-presets). Default: auto")
    ap.add_argument("--out", "-o", action="append", default=[], help="output file; extension picks the format (dxf/svg/plt/png/pdf). Repeatable.")
    ap.add_argument("--width-mm", type=float, default=None)
    ap.add_argument("--height-mm", type=float, default=None)
    ap.add_argument("--engine", choices=["contour", "potrace", "centerline"], default=None)
    ap.add_argument("--mode", choices=["outline", "centerline", "hybrid"], default=None)
    ap.add_argument("--threshold", type=int, default=None, help="0-255 darkness cutoff / Otsu bias")
    ap.add_argument("--detail", type=float, default=None, help="0..1")
    ap.add_argument("--smoothness", type=float, default=None, help="0..1")
    ap.add_argument("--corner", type=float, default=None, help="corner sharpness 0..1")
    ap.add_argument("--min-feature-mm", type=float, default=None)
    ap.add_argument("--despeckle-mm2", type=float, default=None)
    ap.add_argument("--node-budget", type=int, default=None)
    ap.add_argument("--invert", action="store_true", help="treat light pixels as ink")
    ap.add_argument("--dxf-version", choices=["R12", "R2000"], default=None)
    ap.add_argument("--curves", choices=["polyline", "spline"], default=None)
    ap.add_argument("--origin", choices=["top_left", "bottom_left", "center"], default=None)
    ap.add_argument("--flatten-layers", action="store_true")
    dp = ap.add_argument_group("depth (3D relief) engraving")
    dp.add_argument("--depth", action="store_true", help="treat the image as a height map and produce cumulative depth slices (DEPTH_01..NN layers)")
    dp.add_argument("--depth-levels", type=int, default=None, help="number of slices / passes")
    dp.add_argument("--depth-mm", type=float, default=None, help="total engraving depth at the deepest tone (mm)")
    dp.add_argument("--material", default=None, help="stainless, brass, aluminum, copper, titanium, mild_steel, tool_steel, generic")
    dp.add_argument("--laser-w", type=float, default=None, help="source power (scales removal per pass)")
    dp.add_argument("--removal-um", type=float, default=None, help="measured removal per pass in micrometres (overrides the material table)")
    dp.add_argument("--white-deep", action="store_true", help="white = deepest (height/bump map convention); default black = deepest")
    dp.add_argument("--photo-relief", action="store_true", help="convert a photo's luminance to a bas-relief (gradient compression)")
    dp.add_argument("--depth-smoothing-mm", type=float, default=None)
    dp.add_argument("--depth-gamma", type=float, default=None)
    dp.add_argument("--draft-deg", type=float, default=None, help="wall draft angle (0 = vertical)")
    dp.add_argument("--depth-hatch", action="store_true", help="generate rotating-angle hatch lines per slice (DEPTH_nn_HATCH layers)")
    dp.add_argument("--hatch-spacing", type=float, default=None, help="hatch pitch mm")
    dp.add_argument("--angle-step", type=float, default=None, help="hatch angle increment per slice (deg)")
    dp.add_argument("--png-16", action="store_true", help="16-bit height map PNG")
    dp.add_argument("--depth-pack", default=None, help="write the full depth pack (multi-layer DXF, per-slice DXFs, height-map PNG, plan) into this folder")
    ap.add_argument("--stats", default=None, help="write stats + warnings JSON here")
    ap.add_argument("--save-binary", default=None, help="write the preprocessed 1-bit PNG here")
    ap.add_argument("--save-job", default=None, help="write the job (settings) JSON here")
    ap.add_argument("--classify", action="store_true", help="print the auto-classification and exit")
    ap.add_argument("--list-presets", action="store_true")
    ap.add_argument("--batch", default=None, help="input folder")
    ap.add_argument("--out-dir", default=None, help="output folder for --batch")
    ap.add_argument("--format", default="dxf", help="format(s) for --batch, comma separated")
    ap.add_argument("--quiet", "-q", action="store_true")
    ap.add_argument("--version", action="version", version=f"lasertrace {__version__}")
    return ap


def apply_overrides(job: Job, a: argparse.Namespace) -> None:
    t = job.trace
    if a.engine: t.engine = a.engine
    if a.mode: t.mode = a.mode
    if a.threshold is not None: t.threshold = a.threshold
    if a.detail is not None: t.detail = a.detail
    if a.smoothness is not None: t.smoothness = a.smoothness
    if a.corner is not None: t.corner_sharpness = a.corner
    if a.min_feature_mm is not None: t.min_feature_mm = a.min_feature_mm
    if a.despeckle_mm2 is not None: t.despeckle_mm2 = a.despeckle_mm2
    if a.node_budget is not None: t.node_budget = a.node_budget
    if a.invert:
        from .models import PreprocessOp
        if job.stack.find("invert") is None:
            job.stack.ops.append(PreprocessOp(op="invert"))
    d = job.depth
    if a.depth: d.enabled = True
    if a.depth_levels is not None: d.levels = a.depth_levels
    if a.depth_mm is not None: d.total_depth_mm = a.depth_mm
    if a.material: d.material = a.material
    if a.laser_w is not None: d.laser_w = a.laser_w
    if a.removal_um is not None: d.removal_per_pass_um = a.removal_um
    if a.white_deep: d.dark_is_deep = False
    if a.photo_relief: d.photo_to_relief = True
    if a.depth_smoothing_mm is not None: d.smoothing_mm = a.depth_smoothing_mm
    if a.depth_gamma is not None: d.depth_gamma = a.depth_gamma
    if a.draft_deg is not None: d.draft_angle_deg = a.draft_deg
    if a.depth_hatch: d.hatch.enabled = True
    if a.hatch_spacing is not None: d.hatch.spacing_mm = a.hatch_spacing
    if a.angle_step is not None: d.hatch.angle_step_deg = a.angle_step
    if a.png_16: d.png_bits = 16
    if any((a.depth_levels is not None, a.depth_mm is not None, a.material, a.removal_um is not None, a.white_deep, a.photo_relief, a.depth_hatch, a.depth_pack)):
        d.enabled = True
    e = job.export
    if a.width_mm is not None: e.width_mm = a.width_mm
    if a.height_mm is not None: e.height_mm = a.height_mm
    if a.dxf_version: e.dxf_version = a.dxf_version
    if a.curves: e.curves = a.curves
    if a.origin: e.origin = a.origin
    if a.flatten_layers: e.flatten_layers = True


def make_job(rgb, src, preset_name: str | None, a: argparse.Namespace, quiet: bool) -> Job:
    from .auto import build_job, is_auto_mode
    preset_name = preset_name or "auto"
    if is_auto_mode(preset_name):
        job = build_job(rgb, preset_name, a.width_mm, src)
        if not quiet:
            print(f"{preset_name}: {job.notes}", file=sys.stderr)
    else:
        job = Job.from_preset(load_preset(preset_name), src)
    apply_overrides(job, a)
    return job


def process_one(path: Path, outs: list[Path], a: argparse.Namespace, quiet: bool) -> dict:
    t0 = time.perf_counter()
    rgb, src = load_file(path)
    job = make_job(rgb, src, a.preset, a, quiet)
    po = run(rgb, job)
    res = po.result
    job_json = json.dumps(job.model_dump(mode="json"), separators=(",", ":"))
    written = []
    for o in outs:
        export(res, job.export, o, job_json=job_json, height=po.height, job=job)
        written.append(str(o))
    pack_note = None
    if job.depth.enabled and a.depth_pack:
        from .export import write_depth_pack
        files = write_depth_pack(res, job, a.depth_pack, height=po.height, stem=path.stem, job_json=job_json)
        written.extend(str(f) for f in files)
        pack_note = f"{a.depth_pack}  ({len(files)} files: multi-layer DXF, per-slice DXFs, height map PNG, plan)"
    if a.save_binary:
        from PIL import Image
        Image.fromarray(255 - po.binary).convert("1").save(a.save_binary)
    if a.save_job:
        Path(a.save_job).write_text(json.dumps(job.model_dump(mode="json"), indent=2), encoding="utf-8")
    elapsed = (time.perf_counter() - t0) * 1000
    st = res.stats
    summary = {
        "input": str(path), "preset": job.preset_name, "engine": res.engine, "outputs": written,
        "paths": st.paths, "nodes": st.nodes, "open_paths": st.open_paths, "holes": st.holes,
        "holes_in_binary": st.holes_in_binary, "iou": st.iou_vs_binary, "width_mm": round(st.width_mm, 3),
        "height_mm": round(st.height_mm, 3), "fill_area_mm2": round(st.fill_area_mm2, 3),
        "complexity": round(st.complexity, 1), "time_ms": round(elapsed, 1),
        "warnings": [w.model_dump() for w in res.warnings],
    }
    if res.depth is not None:
        r = res.depth
        summary["depth"] = {"levels": r.levels, "total_depth_mm": r.total_depth_mm, "slice_thickness_mm": r.slice_thickness_mm,
                            "material": r.material, "removal_per_pass_um": r.removal_per_pass_um, "passes_per_slice": r.machine.get("passes_per_slice"),
                            "raster_passes": r.machine.get("raster_passes"), "footprint_mm2": r.footprint_mm2, "est_time_min": round(r.est_time_s / 60, 1)}
        if not quiet:
            print(f"  depth: {r.levels} slices x {r.slice_thickness_mm} mm = {r.total_depth_mm} mm on {r.material}; {r.machine.get('passes_per_slice')} loop(s)/slice ({r.removal_per_pass_um} um/pass), raster passes {r.machine.get('raster_passes')}, ~{r.est_time_s / 60:.0f} min", file=sys.stderr)
    if a.stats:
        Path(a.stats).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not quiet:
        print(f"{path.name}: {st.paths} paths, {st.nodes} nodes, {st.holes} holes, {st.width_mm:.1f}x{st.height_mm:.1f} mm, {elapsed:.0f} ms", file=sys.stderr)
        for w in res.warnings:
            print(f"  [{w.severity}] {w.code}: {w.message}", file=sys.stderr)
        for o in written:
            if pack_note and o.startswith(str(Path(a.depth_pack))):
                continue
            print(f"  -> {o}", file=sys.stderr)
        if pack_note:
            print(f"  -> depth pack {pack_note}", file=sys.stderr)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.list_presets:
        from .auto import AUTO_MODES
        for name, (disp, desc) in AUTO_MODES.items():
            print(f"{name:26s} {disp:28s} {desc}")
        for p in list_presets():
            print(f"{p.name:26s} {p.display_name:28s} {p.description}")
        return 0
    if a.batch:
        in_dir = Path(a.batch)
        out_dir = Path(a.out_dir or (in_dir / "vectors"))
        out_dir.mkdir(parents=True, exist_ok=True)
        fmts = [f.strip().lstrip(".") for f in a.format.split(",") if f.strip()]
        exts = supported_ext()
        files = sorted(f for f in in_dir.iterdir() if f.suffix.lower() in exts)
        rows = []
        for f in files:
            outs = [out_dir / f"{f.stem}.{fmt}" for fmt in fmts]
            try:
                s = process_one(f, outs, a, a.quiet)
                s["error"] = ""
            except Exception as e:
                s = {"input": str(f), "error": str(e), "warnings": []}
                print(f"{f.name}: ERROR {e}", file=sys.stderr)
            rows.append(s)
        log = out_dir / "lasertrace_log.csv"
        with log.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["input", "preset", "paths", "nodes", "open_paths", "holes", "iou", "time_ms", "warnings", "error"])
            for r in rows:
                w.writerow([r.get("input"), r.get("preset"), r.get("paths"), r.get("nodes"), r.get("open_paths"), r.get("holes"), r.get("iou"), r.get("time_ms"),
                            "; ".join(f"{x['code']}" for x in r.get("warnings", [])), r.get("error", "")])
        print(f"batch done: {len(rows)} file(s), log {log}", file=sys.stderr)
        return 0
    if not a.input:
        ap.error("input file required (or --batch / --list-presets)")
    path = Path(a.input)
    if a.classify:
        rgb, _ = load_file(path)
        c = classify(rgb)
        print(json.dumps({"suggested_preset": c.suggested_preset, "kind": c.kind, "confidence": c.confidence, "signals": c.signals, "notes": c.notes}, indent=2))
        return 0
    outs = [Path(o) for o in a.out] or [path.with_suffix(".dxf")]
    process_one(path, outs, a, a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
