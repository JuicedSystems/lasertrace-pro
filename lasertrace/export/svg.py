"""Plain SVG: mm units, y-down, one <g> per layer, paths only, no text/filters.

LightBurn maps stroke/fill colours to layers, so the layer colours from the
ExportProfile are emitted verbatim.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from ..geometry.bezier import flatten_cubic
from ..models import Arc, Cubic, ExportProfile, Layer, Line, PathGraph, Subpath, depth_layer_name


def depth_hex(index: int) -> str:
    """Distinct, LightBurn-palette-friendly colour per slice (hue steps of 137.5 deg)."""
    import colorsys
    hue = ((index - 1) * 137.5 % 360.0) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.75)
    return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))


def _f(v: float, prec: int) -> str:
    s = f"{v:.{prec}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def subpath_d(sp: Subpath, prec: int, polyline: bool = False, tol: float = 0.02) -> str:
    if not sp.segments:
        return ""
    parts = []
    s = sp.start()
    parts.append(f"M{_f(s[0], prec)},{_f(s[1], prec)}")
    for seg in sp.segments:
        if isinstance(seg, Line):
            parts.append(f"L{_f(seg.p2[0], prec)},{_f(seg.p2[1], prec)}")
        elif isinstance(seg, Cubic):
            if polyline:
                for p in flatten_cubic(seg.p0, seg.c1, seg.c2, seg.p3, tol)[1:]:
                    parts.append(f"L{_f(p[0], prec)},{_f(p[1], prec)}")
            else:
                parts.append(f"C{_f(seg.c1[0], prec)},{_f(seg.c1[1], prec)} {_f(seg.c2[0], prec)},{_f(seg.c2[1], prec)} {_f(seg.p3[0], prec)},{_f(seg.p3[1], prec)}")
        elif isinstance(seg, Arc):
            e = seg.p_end
            large = 1 if abs(seg.a1 - seg.a0) > 3.141592653589793 else 0
            sweep = 1 if seg.a1 > seg.a0 else 0
            parts.append(f"A{_f(seg.r, prec)},{_f(seg.r, prec)} 0 {large} {sweep} {_f(e[0], prec)},{_f(e[1], prec)}")
    if sp.closed:
        parts.append("Z")
    return "".join(parts)


def svg_string(graph: PathGraph, profile: ExportProfile, job_json: str | None = None) -> str:
    prec = profile.precision
    w, h = graph.width, graph.height
    ox, oy = graph.bbox[0], graph.bbox[1]
    polyline = profile.curves == "polyline"
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(w, prec)}mm" height="{_f(h, prec)}mm" viewBox="{_f(ox, prec)} {_f(oy, prec)} {_f(w, prec)} {_f(h, prec)}">',
        "<!-- LaserTrace Pro: units mm, y-down, origin top-left -->",
    ]
    if job_json and profile.embed_preset:
        out.append(f"<desc>{escape(job_json)}</desc>")
    # depth slices first: one group per DEPTH_nn (+ _HATCH), distinct colours
    depth_groups: dict[str, list] = {}
    for p in graph.paths:
        if p.depth_index is not None:
            depth_groups.setdefault(p.layer_name(), []).append(p)
    for gid in sorted(depth_groups):
        paths = depth_groups[gid]
        idx = paths[0].depth_index or 1
        color = depth_hex(idx + (15 if paths[0].layer == Layer.ENGRAVE_LINE else 0))
        if paths[0].layer == Layer.ENGRAVE_LINE:
            style = f'fill="none" stroke="{color}" stroke-width="{_f(paths[0].stroke_width_mm or 0.03, 3)}" stroke-linecap="round"'
        else:
            style = f'fill="{color}" fill-rule="evenodd" stroke="none"'
        out.append(f'<g id="{gid}" data-depth-index="{idx}" {style}>')
        for p in paths:
            d = " ".join(subpath_d(sp, prec, polyline, profile.flatten_tol_mm) for sp in p.subpaths if sp.segments)
            if d:
                out.append(f'<path id="{p.id}" d="{d}"/>')
        out.append("</g>")
    layers = [Layer.ENGRAVE_FILL, Layer.ENGRAVE_LINE, Layer.CUT, Layer.SCORE, Layer.IGNORE]
    for layer in layers:
        paths = [p for p in graph.paths if p.layer == layer and p.depth_index is None]
        if not paths:
            continue
        color = profile.layer_colors.get(layer.value, "#000000")
        if layer == Layer.ENGRAVE_FILL:
            style = f'fill="{color}" fill-rule="evenodd" stroke="none"'
        elif layer == Layer.CUT:
            style = f'fill="none" stroke="{color}" stroke-width="{_f(profile.cut_hairline_mm, 3)}"'
        else:
            style = f'fill="none" stroke="{color}" stroke-width="0.05" stroke-linecap="round" stroke-linejoin="round"'
        # IGNORE is construction geometry (alignment frames): never merged
        gid = "ENGRAVE" if profile.flatten_layers and layer != Layer.IGNORE else layer.value
        out.append(f'<g id="{gid}" {style}>')
        for p in paths:
            d = " ".join(subpath_d(sp, prec, polyline, profile.flatten_tol_mm) for sp in p.subpaths if sp.segments)
            if not d:
                continue
            extra = ""
            if layer == Layer.ENGRAVE_LINE and p.stroke_width_mm:
                extra = f' data-stroke-mm="{_f(p.stroke_width_mm, 3)}"'
            out.append(f'<path id="{p.id}" d="{d}"{extra}/>')
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_svg(graph: PathGraph, profile: ExportProfile, out_path: str | Path, job_json: str | None = None) -> None:
    Path(out_path).write_text(svg_string(graph, profile, job_json), encoding="utf-8")
