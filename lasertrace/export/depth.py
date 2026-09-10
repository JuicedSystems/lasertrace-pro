"""Depth (3D relief) export pack.

One depth job produces several files because the three target workflows
want different things:

* **LightBurn 3D Sliced / EZCAD3 depth map** want a clean grayscale image at
  the hatch pitch (black = deepest, full 0..255 range, untouched surface =
  pure white) -> `<stem>_heightmap.png` (8 or 16 bit, DPI tag set).
* **LightBurn vector layers / EZCAD3** take one DXF with a layer + distinct
  colour per slice -> `<stem>_depth.dxf` (LightBurn keys on colour, EZCAD on
  layer name).
* **EZCAD2** has no slicer and ignores layers on import, so the safest
  hand-off is one DXF per slice with an identical alignment frame in every
  file -> `slices/<stem>_DEPTH_nn.dxf`.
* The operator needs the pass plan: depth, Z offset, loop count and hatch
  angle per slice -> `<stem>_plan.md/.csv/.json`, plus a shaded preview.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..depth.heightmap import height_to_image, quantize_height
from ..depth.plan import report_csv, report_markdown
from ..models import ExportProfile, Job, Layer, Line, Path as VPath, PathGraph, Subpath, TraceResult
from .dxf import write_dxf


def slice_graph(graph: PathGraph, index: int, include_hatch: bool = True) -> PathGraph:
    """Paths of one slice only (same bbox so every slice file shares the frame)."""
    paths = [p for p in graph.paths if p.depth_index == index and (include_hatch or p.layer == Layer.ENGRAVE_FILL)]
    return PathGraph(paths=paths, units=graph.units, bbox=graph.bbox, px_per_mm=graph.px_per_mm)


def frame_path(graph: PathGraph) -> VPath:
    """Rectangle around the footprint on the IGNORE layer. Imported into EZCAD2
    with every slice file, it keeps all slices registered even when the
    program centres an import on its own bounding box."""
    x0, y0, x1, y1 = graph.bbox
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    segs = [Line(p1=pts[k], p2=pts[(k + 1) % 4]) for k in range(4)]
    return VPath(subpaths=[Subpath(segments=segs, closed=True)], layer=Layer.IGNORE)


def height_crop(height: np.ndarray, px_per_mm: float, graph: PathGraph) -> np.ndarray:
    """Crop the full-frame height field to the traced footprint."""
    fp = height > 0
    ys, xs = np.nonzero(fp)
    if xs.size == 0:
        return height
    return height[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def height_png_array(height: np.ndarray, job: Job, width_mm: float, pitch_mm: float | None = None) -> tuple[np.ndarray, float]:
    """Resample the footprint-cropped height to the hatch pitch and convert to
    the target convention. Returns (image array, dpi)."""
    s = job.depth
    pitch = pitch_mm or (s.hatch.spacing_mm if s.hatch.enabled else None)
    if pitch is None:
        from ..depth.materials import material_params
        pitch = material_params(s.material).spacing_mm
    pitch = max(0.005, float(pitch))
    dpi = 25.4 / pitch
    h = height
    hh, ww = h.shape
    if ww < 2 or hh < 2:
        return height_to_image(h, s.png_bits, s.png_black_is_deep), dpi
    target_w = max(2, int(round(width_mm / pitch)))
    target_h = max(2, int(round(target_w * hh / ww)))
    interp = cv2.INTER_AREA if target_w < ww else cv2.INTER_LINEAR
    r = cv2.resize(h.astype(np.float32), (target_w, target_h), interpolation=interp)
    if s.quantize_png:
        r = quantize_height(r, s.levels)
    return height_to_image(np.clip(r, 0, 1), s.png_bits, s.png_black_is_deep), dpi


def write_height_png(height: np.ndarray, job: Job, width_mm: float, out_path: str | Path, pitch_mm: float | None = None) -> Path:
    arr, dpi = height_png_array(height, job, width_mm, pitch_mm)
    mode = "I;16" if arr.dtype == np.uint16 else "L"
    img = Image.fromarray(arr, mode=mode) if mode == "L" else Image.fromarray(arr.astype(np.uint16))
    img.save(str(out_path), dpi=(dpi, dpi))
    return Path(out_path)


def shaded_preview(height: np.ndarray, azimuth_deg: float = 315.0, altitude_deg: float = 45.0, scale: float = 6.0) -> np.ndarray:
    """Hillshade of the height field (deep = dark floor, lit walls) as uint8."""
    h = height.astype(np.float32)
    if h.size < 4:
        return np.full(h.shape, 255, np.uint8)
    depth = -h * scale  # engraving goes *into* the part
    gy, gx = np.gradient(depth)
    az, al = np.radians(azimuth_deg), np.radians(altitude_deg)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(al) * np.cos(slope) + np.cos(al) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip(shade, 0, 1)
    tone = 0.55 + 0.45 * shade
    tone = tone * (1.0 - 0.35 * h)  # floors darker with depth
    return np.clip(tone * 255, 0, 255).astype(np.uint8)


def write_depth_pack(result: TraceResult, job: Job, out_dir: str | Path, height: np.ndarray | None = None, stem: str = "depth",
                     job_json: str | None = None, per_slice: bool = True, frame: bool = True) -> list[Path]:
    """Write every depth deliverable into `out_dir`. Returns the written paths."""
    if result.depth is None:
        raise ValueError("result has no depth report; run the depth pipeline first")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from . import prepare_graph
    profile: ExportProfile = job.export
    graph = prepare_graph(result.graph, profile)
    written: list[Path] = []

    # 1. one DXF, one layer + colour per slice
    p = out / f"{stem}_depth.dxf"
    write_dxf(graph, profile, p, job_json=job_json)
    written.append(p)

    # 2. one DXF per slice, identical frame
    if per_slice:
        sdir = out / "slices"
        sdir.mkdir(exist_ok=True)
        for i in range(1, result.depth.levels + 1):
            g = slice_graph(graph, i)
            if not g.paths:
                continue
            if frame:
                g.paths.append(frame_path(graph))
            sp = sdir / f"{stem}_DEPTH_{i:02d}.dxf"
            write_dxf(g, profile, sp, job_json=None, frame=graph)
            written.append(sp)

    # 3. height map + shaded preview
    if height is not None:
        crop = height_crop(height, graph.px_per_mm or 1.0, graph)
        hp = out / f"{stem}_heightmap.png"
        write_height_png(crop, job, graph.width, hp)
        written.append(hp)
        pv = out / f"{stem}_preview.png"
        Image.fromarray(shaded_preview(crop)).save(str(pv))
        written.append(pv)

    # 4. plan
    md = out / f"{stem}_plan.md"
    md.write_text(report_markdown(result.depth, f"Depth pass plan: {stem}"), encoding="utf-8")
    written.append(md)
    cs = out / f"{stem}_plan.csv"
    cs.write_text(report_csv(result.depth), encoding="utf-8")
    written.append(cs)
    js = out / f"{stem}_plan.json"
    payload = {"report": result.depth.model_dump(mode="json"), "job": json.loads(job_json) if job_json else job.model_dump(mode="json"),
               "warnings": [w.model_dump() for w in result.warnings]}
    js.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    written.append(js)
    return written
