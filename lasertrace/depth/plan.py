"""Operator pass plan for a depth job.

For each cumulative slice: nominal depth before/after, the Z (focus) offset to
dial in before the pass, the loop count needed at the material's removal
rate, the hatch angle, area, and a time estimate. Everything is derived from
`DepthSettings` + the material table; nothing here touches geometry.
"""
from __future__ import annotations

import math

from ..models import DepthReport, DepthSettings, DepthSlice, Layer, PathGraph
from .hatch import slice_angle
from .materials import material_params

JUMP_S = 0.0008          # typical galvo jump + delays per hatch line (s)
CLEAN_PASS_EVERY = 5     # shops run ~5 roughing slices per clean pass (OMG 5:1, ComMarker every 10)


def _z_offset(depth_from_mm: float, s: DepthSettings) -> float:
    if not s.z_step or s.z_step_every_mm <= 0:
        return 0.0
    steps = math.floor(depth_from_mm / s.z_step_every_mm + 1e-9)
    return -round(steps * s.z_step_every_mm, 4)


def build_report(s: DepthSettings, graph: PathGraph, areas_mm2: list[float], hatch_lengths: dict[int, float] | None = None,
                 height_range: tuple[float, float] = (0.0, 1.0), notes: list[str] | None = None) -> DepthReport:
    mat = material_params(s.material)
    removal = float(s.removal_per_pass_um) if s.removal_per_pass_um else mat.scaled_removal_um(s.laser_w)
    removal = max(0.1, removal)
    levels = max(1, s.levels)
    th = s.total_depth_mm / levels
    passes_default = s.passes_per_slice or max(1, int(math.ceil(th * 1000.0 / removal - 1e-9)))
    spacing = s.hatch.spacing_mm if s.hatch.enabled else mat.spacing_mm
    speed = max(1.0, mat.speed_mm_s)
    hatch_lengths = hatch_lengths or {}

    slices: list[DepthSlice] = []
    total_time = 0.0
    volume = 0.0
    counts: dict[int, tuple[int, int]] = {}
    for p in graph.paths:
        if p.depth_index is None or p.layer != Layer.ENGRAVE_FILL:
            continue
        c = counts.get(p.depth_index, (0, 0))
        counts[p.depth_index] = (c[0] + 1, c[1] + p.node_count())
    for i in range(1, levels + 1):
        area = float(areas_mm2[i - 1]) if i - 1 < len(areas_mm2) else 0.0
        d_from, d_to = th * (i - 1), th * i
        n_paths, n_nodes = counts.get(i, (0, 0))
        if i in hatch_lengths:
            length = hatch_lengths[i]
            # lines = total hatch length / a typical line length; `length` is
            # already the measured total, so it must NOT be divided by the
            # pitch again (the else branch derives the same figure from area)
            n_lines = length / max(1.0, math.sqrt(max(area, 1e-9)))
        else:
            length = area / max(spacing, 1e-6)
            n_lines = math.sqrt(max(area, 0.0)) / max(spacing, 1e-6)
        t_pass = length / speed + n_lines * JUMP_S
        t_slice = t_pass * passes_default if area > 0 else 0.0
        volume += area * th
        total_time += t_slice
        slices.append(DepthSlice(
            index=i, threshold=(i - 0.5) / levels, depth_from_mm=round(d_from, 4), depth_to_mm=round(d_to, 4),
            z_offset_mm=_z_offset(d_from, s), passes=passes_default if area > 0 else 0,
            hatch_angle_deg=slice_angle(s.hatch, i), area_mm2=round(area, 3), paths=n_paths, nodes=n_nodes,
            hatch_length_mm=round(length, 1), est_time_s=round(t_slice, 1),
        ))
    footprint = float(areas_mm2[0]) if areas_mm2 else 0.0
    # raster workflow (LightBurn 3D Sliced / EZCAD3 depth map): the image is
    # sliced by the controller, so the operator enters a pass count instead of
    # running our DXF layers; at the listed power that count is depth / removal
    raster_passes = max(1, int(math.ceil(s.total_depth_mm * 1000.0 / removal - 1e-9)))
    machine = {
        "raster_passes": raster_passes,
        "material": mat.name, "laser_w": s.laser_w, "power_pct": mat.power_pct, "speed_mm_s": mat.speed_mm_s,
        "freq_khz": mat.freq_khz, "pulse_ns": mat.pulse_ns, "hatch_spacing_mm": spacing,
        "removal_um_per_pass": round(removal, 2), "passes_per_slice": passes_default,
        "hatch_angle_step_deg": s.hatch.angle_step_deg, "clean_pass": mat.clean, "clean_pass_every_slices": CLEAN_PASS_EVERY,
        "z_step": s.z_step, "z_step_every_mm": s.z_step_every_mm, "notes": mat.notes,
    }
    return DepthReport(
        levels=levels, total_depth_mm=s.total_depth_mm, slice_thickness_mm=round(th, 4), material=mat.key,
        removal_per_pass_um=round(removal, 2), footprint_mm2=round(footprint, 3), volume_mm3=round(volume, 3),
        est_time_s=round(total_time, 1), height_range=height_range, slices=slices, machine=machine, notes=list(notes or []),
    )


def report_markdown(r: DepthReport, title: str = "Depth engraving pass plan") -> str:
    m = r.machine
    mins = r.est_time_s / 60.0
    out = [
        f"# {title}", "",
        f"* Material: **{m.get('material', r.material)}**, laser {m.get('laser_w', '?')} W",
        f"* Total depth **{r.total_depth_mm} mm** in **{r.levels} slices** of {r.slice_thickness_mm} mm; removal ~{r.removal_per_pass_um} um/pass -> {m.get('passes_per_slice')} loop(s) per slice",
        f"* Footprint {r.footprint_mm2} mm2, removed volume ~{r.volume_mm3} mm3, estimated time ~{mins:.0f} min",
        f"* Starting parameters: {m.get('power_pct')}% power, {m.get('speed_mm_s')} mm/s, {m.get('freq_khz')} kHz" + (f", {m.get('pulse_ns')} ns" if m.get('pulse_ns') else "") + f", hatch {m.get('hatch_spacing_mm')} mm",
        f"* Rotate the hatch angle by {m.get('hatch_angle_step_deg')} deg every slice; clean pass ({m.get('clean_pass')}) every {m.get('clean_pass_every_slices')} slices",
        f"* Z step: {'follow focus, ' + str(m.get('z_step_every_mm')) + ' mm increments' if m.get('z_step') else 'none (fixed focus)'}",
        f"* Raster alternative (LightBurn 3D Sliced / EZCAD3 depth map): load the height map PNG, Number of Passes = {m.get('raster_passes')} at the parameters above, black = deepest, Negative Image off",
        "",
    ]
    if r.notes:
        out.append("Notes: " + "; ".join(r.notes))
        out.append("")
    out.append("| # | layer | height >= | depth from -> to (mm) | Z offset | loops | angle | area mm2 | paths | time s |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for sl in r.slices:
        out.append(f"| {sl.index} | DEPTH_{sl.index:02d} | {sl.threshold:.3f} | {sl.depth_from_mm:.3f} -> {sl.depth_to_mm:.3f} | {sl.z_offset_mm:+.3f} | {sl.passes} | {sl.hatch_angle_deg:.0f} | {sl.area_mm2:.2f} | {sl.paths} | {sl.est_time_s:.0f} |")
    out.append("")
    out.append("Run the slices in order (DEPTH_01 first). Every slice re-marks everything deeper than it, so the deepest areas receive every pass.")
    return "\n".join(out) + "\n"


def report_csv(r: DepthReport) -> str:
    rows = ["index,layer,threshold,depth_from_mm,depth_to_mm,z_offset_mm,passes,hatch_angle_deg,area_mm2,paths,nodes,hatch_length_mm,est_time_s"]
    for sl in r.slices:
        rows.append(f"{sl.index},DEPTH_{sl.index:02d},{sl.threshold:.4f},{sl.depth_from_mm:.4f},{sl.depth_to_mm:.4f},{sl.z_offset_mm:.4f},{sl.passes},{sl.hatch_angle_deg:.1f},{sl.area_mm2:.3f},{sl.paths},{sl.nodes},{sl.hatch_length_mm:.1f},{sl.est_time_s:.1f}")
    return "\n".join(rows) + "\n"
