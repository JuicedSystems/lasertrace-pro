"""AUTO modes: dynamic presets tuned from measurements of the pasted image.

A static preset is a fixed recipe. An AUTO mode measures the artwork first
(is it already black/white, is it white-on-black, how noisy, how thin are the
strokes, is it a module grid, how many pixels) and derives a concrete `Job`
whose sliders the operator can still tweak. The decisions are written into
`Job.notes` so the UI/CLI can show why.

Modes
-----
auto        classify -> nearest shop preset -> tune everything below
auto-bw     artwork already black on white (or white on black): hard 50%
            threshold, no source smoothing, sharp corners, auto invert,
            outline / centerline / hybrid from stroke widths
auto-photo  photos and scans: deskew, denoise and local threshold tuned from
            the measured noise and image size
auto-lines  line art: centerline everywhere thin, fills only where strokes
            are wider than the detected width
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .ingest import to_gray
from .models import Job, PreprocessOp, SourceImage
from .preprocess import morphology, threshold
from .preprocess.classify import Classification, classify
from .presets import load_preset
from .units import px_per_mm_for_width

DEFAULT_WIDTH_MM = 50.0

AUTO_MODES: dict[str, tuple[str, str]] = {
    "auto": ("AUTO (detect everything)",
             "Classifies the artwork, picks the closest shop preset, then tunes threshold, invert, despeckle, minimum feature and outline/centerline/hybrid from the image."),
    "auto-bw": ("AUTO Black & White",
                "Art that is already black on white (or white on black): hard 50% threshold, no source smoothing, sharp corners, auto invert, mode from stroke widths."),
    "auto-photo": ("AUTO Photo / Scan",
                   "Photos and scans: deskew, denoise and local threshold tuned from the measured noise and size."),
    "auto-lines": ("AUTO Line Art",
                   "Centerline for everything thin; fills only where strokes are wider than the detected width."),
    "auto-depth": ("AUTO Depth (3D relief)",
                   "Grayscale height map or photo -> cumulative depth slices. Detects which tone is the surface, whether the source is a photo, and picks slice count and smoothing from the tonal range."),
}


def is_auto_mode(name: str | None) -> bool:
    return bool(name) and name in AUTO_MODES


@dataclass
class Analysis:
    width_px: int
    height_px: int
    px_per_mm: float
    otsu_t: int
    mid_frac: float          # fraction of pixels that are neither dark nor light
    hard_bw: bool            # essentially 1-bit already
    inverted: bool           # white artwork on dark background
    noise_std: float         # std of the flat background
    ink_frac: float
    stroke_med_mm: float
    stroke_p90_mm: float
    stroke_max_mm: float     # widest solid region (2x max distance transform)
    components: int
    specks: int              # components smaller than 4 px
    holes: int
    grid_score: float
    classification: Classification
    notes: list[str] = field(default_factory=list)


def analyze(rgb: np.ndarray, width_mm: float | None = None) -> Analysis:
    gray = to_gray(rgb)
    h, w = gray.shape
    hist = threshold.histogram(gray).astype(np.float64)
    total = float(hist.sum())
    dark = hist[:64].sum() / total
    light = hist[192:].sum() / total
    mid = 1.0 - dark - light
    t = threshold.otsu_value(gray)
    mask = threshold.manual(gray, t)

    # inverted? ink covers most of the image and the border is ink
    border = np.concatenate([mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]])
    border_ink = float((border > 0).mean())
    ink_frac = float((mask > 0).mean())
    # artwork almost never runs solid ink along the whole border; a dark frame
    # all the way round means the paper is dark and the art is light
    inverted = border_ink > 0.75 or (ink_frac > 0.5 and border_ink > 0.5)
    if inverted:
        mask = morphology.invert(mask)
        ink_frac = 1.0 - ink_frac

    ys, xs = np.nonzero(mask)
    ink_w = int(xs.max() - xs.min() + 1) if xs.size else w
    ppm = px_per_mm_for_width(max(1, ink_w), width_mm or DEFAULT_WIDTH_MM)

    bg = gray[mask == 0] if not inverted else (255 - gray)[mask == 0]
    noise = float(bg.std()) if bg.size > 100 else 0.0
    med, p10, p90 = morphology.stroke_width_stats(mask)
    dt = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5) if mask.max() else np.zeros_like(mask, dtype=np.float32)
    stroke_max = float(dt.max()) * 2.0
    n, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA] if n > 1 else np.array([])
    specks = int((areas < 4).sum()) if areas.size else 0
    cls = classify(rgb)
    hard_bw = mid < 0.02 and noise < 3.0
    return Analysis(
        width_px=w, height_px=h, px_per_mm=ppm, otsu_t=t, mid_frac=float(mid), hard_bw=hard_bw,
        inverted=inverted, noise_std=noise, ink_frac=ink_frac,
        stroke_med_mm=med / ppm, stroke_p90_mm=p90 / ppm, stroke_max_mm=stroke_max / ppm, components=max(0, n - 1), specks=specks,
        holes=morphology.count_holes(mask), grid_score=float(cls.signals.get("grid_score", 0.0)), classification=cls,
    )


def _set_op(job: Job, op: str, enabled: bool, params: dict | None = None, front: bool = False) -> None:
    existing = job.stack.find(op)
    if existing is None:
        if not enabled:
            return
        new = PreprocessOp(op=op, params=params or {})  # type: ignore[arg-type]
        if front:
            job.stack.ops.insert(0, new)
        else:
            job.stack.ops.append(new)
        return
    existing.enabled = enabled
    if params is not None:
        existing.params = params


def _choose_mode(a: Analysis, job: Job, notes: list[str], allow_outline: bool = True) -> None:
    """Outline / centerline / hybrid from the stroke width distribution (mm)."""
    cl_max = job.trace.centerline_max_width_mm
    if a.stroke_med_mm <= 0:
        return
    line_art = a.classification.kind == "line_art"
    if a.stroke_max_mm < cl_max * 1.5 or (line_art and a.stroke_max_mm < cl_max * 2.5):
        # no solid region anywhere: everything is a stroke
        job.trace.mode, job.trace.engine = "centerline", "centerline"
        notes.append(f"strokes {a.stroke_med_mm:.2f} mm median, widest {a.stroke_max_mm:.2f} mm, no fills -> centerline")
    elif a.stroke_med_mm < cl_max and a.stroke_max_mm >= cl_max * 1.5:
        job.trace.mode, job.trace.engine = "hybrid", "contour"
        notes.append(f"thin strokes ({a.stroke_med_mm:.2f} mm) plus fills up to {a.stroke_max_mm:.1f} mm -> hybrid")
    elif allow_outline:
        job.trace.mode, job.trace.engine = "outline", "contour"
        notes.append(f"stroke width {a.stroke_med_mm:.2f} mm -> outline fill")
    else:
        job.trace.mode, job.trace.engine = "hybrid", "contour"
        notes.append("wide strokes in line mode -> hybrid")


def _common_tuning(a: Analysis, job: Job, notes: list[str]) -> None:
    px_mm = 1.0 / a.px_per_mm
    if a.inverted:
        _set_op(job, "invert", True)
        notes.append("white-on-black artwork -> inverted")
    # tiny sources: upscale so the staircase can be smoothed (before the
    # min-feature rule so the rule can use the upscaled pixel size)
    if max(a.width_px, a.height_px) < 300 and job.stack.find("upscale") is None:
        factor = 4.0 if max(a.width_px, a.height_px) < 150 else 2.0
        _set_op(job, "upscale", True, {"factor": factor, "method": "lanczos"}, front=True)
        notes.append(f"small source ({a.width_px}x{a.height_px}) -> upscale {factor:g}x")
    up = job.stack.find("upscale")
    scale = float(up.params.get("factor", 1.0)) if up is not None and up.enabled else 1.0
    # minimum feature: never below ~2.5 (upscaled) pixels, the tracer cannot
    # resolve less; capped so a tiny favicon still keeps its counters
    mf = max(job.trace.min_feature_mm, min(0.5, round(2.5 * px_mm / scale, 3)))
    if mf != job.trace.min_feature_mm:
        notes.append(f"min feature raised to {mf} mm (2.5 px)")
        job.trace.min_feature_mm = mf
    # despeckle from speck count
    if a.specks > 200:
        job.trace.despeckle_mm2 = max(job.trace.despeckle_mm2, 0.3)
        notes.append(f"{a.specks} specks -> despeckle 0.3 mm2")
    elif a.specks > 30:
        job.trace.despeckle_mm2 = max(job.trace.despeckle_mm2, 0.1)
        notes.append(f"{a.specks} specks -> despeckle 0.1 mm2")
    # module grids: never round
    if a.grid_score > 0.7:
        job.trace.smoothness = 0.0
        job.trace.corner_sharpness = 1.0
        job.trace.detail = 1.0
        for op in ("morph_open", "morph_close", "dilate", "erode", "fill_holes"):
            _set_op(job, op, False)
        notes.append("module grid detected -> square polylines, no morphology")


@dataclass
class DepthAnalysis:
    surface_is_light: bool     # border tone: light border -> white = untouched surface -> dark = deep
    mid_frac: float            # fraction of pixels that are neither near-black nor near-white
    distinct: int              # distinct gray levels inside the relief
    texture: float             # high-frequency energy (photo texture) relative to tonal range
    edge_density: float        # Canny edge pixels / footprint pixels
    looks_like_photo: bool
    looks_like_heightmap: bool
    footprint_frac: float


def analyze_depth(rgb: np.ndarray) -> DepthAnalysis:
    gray = to_gray(rgb)
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]]).astype(np.float32)
    surface_is_light = float(np.median(border)) >= 128
    hist = threshold.histogram(gray).astype(np.float64)
    total = float(hist.sum())
    mid = float(hist[24:232].sum() / total)
    surf = 255 if surface_is_light else 0
    fp = np.abs(gray.astype(np.int16) - surf) > 6
    footprint = float(fp.mean())
    vals = gray[fp]
    distinct = int(np.unique(vals).size) if vals.size else 0
    g = gray.astype(np.float32)
    hf = g - cv2.GaussianBlur(g, (0, 0), 2.0)
    span = float(np.percentile(vals, 98) - np.percentile(vals, 2)) if vals.size > 10 else 1.0
    texture = float(np.abs(hf[fp]).mean() / max(span, 1.0)) if vals.size else 0.0
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float((edges[fp] > 0).mean()) if vals.size else 0.0
    looks_like_photo = texture > 0.035 or edge_density > 0.08
    looks_like_heightmap = (not looks_like_photo) and mid > 0.15 and distinct >= 24
    return DepthAnalysis(surface_is_light, mid, distinct, texture, edge_density, looks_like_photo, looks_like_heightmap, footprint)


_ALPHA_NOTES = {
    "alpha_ink": "transparent background, one-colour art -> shape taken from transparency",
    "light_inverted": "light art with no dark detail on a transparent background -> inverted so the art is the ink",
}


def _source_notes(source: SourceImage | None) -> list[str]:
    """What ingest decided about the file itself, so the operator sees it too."""
    if source is None:
        return []
    notes = []
    if source.bit_depth >= 16:
        # ingest scales every high-bit image from a fixed range (see ingest._high_bit_to_l)
        full = "0..1" if source.mode == "F" else "0..65535"
        notes.append(f"{source.bit_depth}-bit source -> {full} scaled to 8-bit")
    if source.alpha_policy in _ALPHA_NOTES:
        notes.append(_ALPHA_NOTES[source.alpha_policy])
    return notes


def _build_depth_job(rgb: np.ndarray, width_mm: float | None, source: SourceImage | None) -> Job:
    d = analyze_depth(rgb)
    notes: list[str] = _source_notes(source)
    base = "depth-photo-relief" if d.looks_like_photo else "depth-relief"
    job = Job.from_preset(load_preset(base), source)
    job.preset_name = "auto-depth"
    if width_mm:
        job.export.width_mm = width_mm
    s = job.depth
    s.enabled = True
    s.dark_is_deep = d.surface_is_light
    notes.append(("light border -> white is the surface, black = deep" if d.surface_is_light else "dark border -> black is the surface, white = deep"))
    if d.looks_like_photo:
        s.photo_to_relief = True
        notes.append(f"photo texture {d.texture:.3f}, edges {d.edge_density:.2f} -> photo-to-relief, heavier smoothing")
    else:
        s.photo_to_relief = False
        notes.append(f"smooth tones ({d.distinct} levels, texture {d.texture:.3f}) -> height map")
    # slice count from the tonal range actually present (each slice needs ~4 source levels)
    if d.distinct >= 200:
        s.levels = 30   # LightBurn maps at most 30 colours to layers
    elif d.distinct >= 96:
        s.levels = 24
    elif d.distinct >= 48:
        s.levels = 16
    else:
        s.levels = max(4, min(12, d.distinct // 3 or 4))
        notes.append(f"only {d.distinct} tones -> {s.levels} slices, smoothing raised")
        s.smoothing_mm = max(s.smoothing_mm, 0.1)
    notes.append(f"{s.levels} slices")
    if d.footprint_frac > 0.97:
        s.zero_plane = "min"
        notes.append("relief fills the whole image -> zero plane = lightest tone")
    job.notes = "; ".join(notes)
    return job


def build_job(rgb: np.ndarray, mode: str = "auto", width_mm: float | None = None, source: SourceImage | None = None) -> Job:
    """Build a concrete Job for an AUTO mode from measurements of `rgb`."""
    if mode not in AUTO_MODES:
        raise ValueError(f"unknown auto mode {mode!r}; choose from {list(AUTO_MODES)}")
    if mode == "auto-depth":
        return _build_depth_job(rgb, width_mm, source)
    a = analyze(rgb, width_mm)
    notes: list[str] = _source_notes(source)

    if mode == "auto":
        base = a.classification.suggested_preset
        notes.append(f"classified as {a.classification.kind} -> {base}")
    elif mode == "auto-bw":
        base = "small-text" if a.classification.kind == "text" else "logo-fill"
    elif mode == "auto-photo":
        base = "dirty-phone-photo"
    else:
        base = "thin-line-art"

    job = Job.from_preset(load_preset(base), source)
    job.preset_name = mode
    if width_mm:
        job.export.width_mm = width_mm

    if mode in ("auto", "auto-bw"):
        if a.hard_bw:
            _set_op(job, "threshold", True, {"method": "manual", "value": 128})
            job.trace.threshold = 128
            for op in ("bilateral", "denoise", "knockout_halo", "unsharp"):
                _set_op(job, op, False)
            job.trace.corner_sharpness = max(job.trace.corner_sharpness, 0.8)
            notes.append("already 1-bit -> hard 50% threshold, no smoothing, sharp corners")
        elif mode == "auto-bw":
            _set_op(job, "threshold", True, {"method": "otsu", "bias": 0})
            for op in ("bilateral", "denoise"):
                _set_op(job, op, False)
            notes.append(f"anti-aliased -> Otsu at {a.otsu_t}, halo knockout on")
        # text stays outline (glyph strokes are thin but must be filled); grids never change mode
        if a.classification.kind not in ("qr", "text"):
            _choose_mode(a, job, notes)
    elif mode == "auto-photo":
        win = int(max(15, min(a.width_px, a.height_px) // 12)) | 1
        _set_op(job, "threshold", True, {"method": "sauvola", "window": win, "k": 0.25})
        if a.noise_std < 4:
            _set_op(job, "denoise", False)
            notes.append(f"noise {a.noise_std:.1f} -> denoise off")
        else:
            strength = float(min(15.0, max(6.0, a.noise_std)))
            _set_op(job, "denoise", True, {"strength": strength})
            notes.append(f"noise {a.noise_std:.1f} -> denoise {strength:.0f}")
        notes.append(f"local threshold window {win} px")
        if a.stroke_p90_mm > 0 and a.stroke_p90_mm < job.trace.centerline_max_width_mm:
            _choose_mode(a, job, notes)
    else:  # auto-lines
        _choose_mode(a, job, notes, allow_outline=False)
        if a.stroke_med_mm > 0:
            job.trace.centerline_max_width_mm = round(max(0.3, min(2.0, a.stroke_med_mm * 2.0)), 2)
            notes.append(f"centerline max width {job.trace.centerline_max_width_mm} mm (2x median stroke)")

    _common_tuning(a, job, notes)
    if mode == "auto" and a.mid_frac > 0.25:
        d = analyze_depth(rgb)
        if d.looks_like_heightmap:
            notes.append("smooth gray gradients: this looks like a height map; use AUTO Depth for a 3D relief")
    job.notes = "; ".join(notes)
    return job
