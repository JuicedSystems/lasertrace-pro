"""Regression harness: trace every fixture with its target preset, write
reports/metrics.csv + reports/overlays/*.png, and enforce quality gates.

  python tools/regress.py            # run + gate
  python tools/regress.py --no-gate  # just report
  python tools/regress.py --only circle_sharp.png

Gates (per fixture, from fixtures/targets.json):
  nodes   <= 3 x target_nodes
  holes   == target holes (when given)
  no UNCLOSED_PATHS warnings for fill presets
  iou     >= 0.85 for fill presets
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lasertrace.export.png import render_mask  # noqa: E402
from lasertrace.ingest import load_file  # noqa: E402
from lasertrace.models import Job  # noqa: E402
from lasertrace.pipeline import run  # noqa: E402
from lasertrace.presets import load_preset  # noqa: E402
from lasertrace.hygiene.topology import detect_overlaps  # noqa: E402

FIX = ROOT / "fixtures"
REPORTS = ROOT / "reports"
OVERLAYS = REPORTS / "overlays"


def overlay(binary: np.ndarray, graph, ppm: float) -> Image.Image:
    """Red = binary only, blue = vector only, black = both."""
    ys, xs = np.nonzero(binary)
    if xs.size == 0:
        return Image.new("RGB", (64, 64), "white")
    crop = binary[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] > 0
    from lasertrace.geometry.polygons import rasterize
    ras = rasterize(graph, ppm, crop.shape) > 0
    h, w = crop.shape
    rgb = np.full((h, w, 3), 255, np.uint8)
    rgb[crop & ras] = (0, 0, 0)
    rgb[crop & ~ras] = (220, 40, 40)
    rgb[~crop & ras] = (40, 80, 220)
    return Image.fromarray(rgb)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--only", default=None)
    ap.add_argument("--width-mm", type=float, default=None)
    a = ap.parse_args()
    targets = json.loads((FIX / "targets.json").read_text())
    OVERLAYS.mkdir(parents=True, exist_ok=True)
    rows = []
    failures = []
    for name, t in targets.items():
        if a.only and a.only not in name:
            continue
        f = FIX / name
        if not f.exists():
            failures.append(f"{name}: fixture missing")
            continue
        t0 = time.perf_counter()
        rgb, src = load_file(f)
        job = Job.from_preset(load_preset(t["preset"]), src)
        if a.width_mm:
            job.export.width_mm = a.width_mm
        try:
            po = run(rgb, job)
        except Exception as e:
            failures.append(f"{name}: crashed: {e}")
            rows.append({"fixture": name, "preset": t["preset"], "error": str(e)})
            continue
        res = po.result
        st = res.stats
        overlap_area, _ = detect_overlaps(res.graph)
        elapsed = (time.perf_counter() - t0) * 1000
        codes = [w.code for w in res.warnings]
        row = {
            "fixture": name, "preset": t["preset"], "engine": res.engine,
            "paths": st.paths, "nodes": st.nodes, "target_nodes": t["target_nodes"],
            "holes": st.holes, "target_holes": t.get("holes", ""), "holes_in_binary": st.holes_in_binary,
            "open_paths": st.open_paths, "iou": None if st.iou_vs_binary is None else round(st.iou_vs_binary, 4),
            "overlap_mm2": round(overlap_area, 4), "width_mm": round(st.width_mm, 2), "height_mm": round(st.height_mm, 2),
            "complexity": round(st.complexity, 1), "time_ms": round(elapsed), "warnings": ";".join(codes), "error": "",
        }
        rows.append(row)
        try:
            overlay(po.binary, res.graph, po.px_per_mm).save(OVERLAYS / f"{f.stem}_overlay.png")
            m = render_mask(res.graph, 200, 1.0)
            Image.fromarray(255 - m).save(OVERLAYS / f"{f.stem}_vector.png")
        except Exception as e:  # pragma: no cover
            print(f"overlay failed for {name}: {e}", file=sys.stderr)
        # gates
        is_depth = bool(t.get("depth"))
        if is_depth:
            r = res.depth
            if r is None:
                failures.append(f"{name}: no depth report")
            else:
                if res.graph.depth_levels() != job.depth.levels:
                    failures.append(f"{name}: {res.graph.depth_levels()} slices != {job.depth.levels}")
                for i in range(1, len(po.masks)):
                    if np.any((po.masks[i] > 0) & (po.masks[i - 1] == 0)):
                        failures.append(f"{name}: slice {i + 1} not nested in slice {i}")
                        break
                if [w for w in res.warnings if w.severity == "error"]:
                    failures.append(f"{name}: error warnings {codes}")
                if st.open_paths:
                    failures.append(f"{name}: {st.open_paths} open slice paths")
            row["depth_levels"] = res.graph.depth_levels()
        is_fill = job.trace.mode == "outline" and not is_depth
        if st.nodes > 3 * t["target_nodes"]:
            failures.append(f"{name}: nodes {st.nodes} > 3x target {t['target_nodes']}")
        if "holes" in t and is_fill and st.holes != t["holes"]:
            failures.append(f"{name}: holes {st.holes} != target {t['holes']}")
        if is_fill and "UNCLOSED_PATHS" in codes:
            failures.append(f"{name}: unclosed fill paths")
        if is_fill and st.iou_vs_binary is not None and st.iou_vs_binary < 0.85:
            failures.append(f"{name}: iou {st.iou_vs_binary:.3f} < 0.85")
        if overlap_area > 0.01:
            failures.append(f"{name}: overlapping fills {overlap_area:.3f} mm2")
        print(f"{name:36s} {t['preset']:24s} paths={st.paths:4d} nodes={st.nodes:5d}/{t['target_nodes']:<5d} holes={st.holes}/{t.get('holes', '-')} iou={row['iou']} {elapsed:6.0f}ms {' '.join(codes)}")

    REPORTS.mkdir(exist_ok=True)
    keys = ["fixture", "preset", "engine", "paths", "nodes", "target_nodes", "holes", "target_holes", "holes_in_binary", "open_paths", "iou", "overlap_mm2", "width_mm", "height_mm", "complexity", "time_ms", "warnings", "error", "depth_levels"]
    with (REPORTS / "metrics.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    print(f"\nwrote {REPORTS / 'metrics.csv'} and {len(rows)} overlays")
    if failures:
        print("\nQUALITY GATE FAILURES:")
        for f in failures:
            print("  -", f)
        return 0 if a.no_gate else 1
    print("all quality gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
