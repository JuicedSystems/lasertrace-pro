"""DXF export tuned for EZCAD2/3 and LightBurn.

* $INSUNITS = 4 (millimetres), $MEASUREMENT = 1 (metric)
* Y axis flipped to CAD convention (y-up), origin bottom-left by default
* One layer per operation, ACI colours from the profile
* Fills as closed LWPOLYLINE (R2000) / POLYLINE (R12): EZCAD hatch-fills
  closed polylines reliably; nested holes are separate closed polylines on the
  same layer so even-odd hatch leaves counters open
* Optional SPLINE output (R2000 only) built from the cubic Beziers
"""
from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.math import Vec3

from ..geometry.bezier import flatten_cubic
from ..geometry.polygons import subpath_to_ring
from ..models import Arc, Cubic, ExportProfile, Layer, Line, PathGraph, Subpath, depth_layer_aci
from ..units import Transform
from ..hygiene.simplify import transform_graph


def dxf_transform(graph: PathGraph, profile: ExportProfile) -> Transform:
    """Working space (mm, y-down, top-left) -> DXF (y-up)."""
    w, h = graph.width, graph.height
    ox, oy = graph.bbox[0], graph.bbox[1]
    if profile.origin == "bottom_left":
        return Transform(1.0, -1.0, -ox, h + oy)
    if profile.origin == "center":
        return Transform(1.0, -1.0, -ox - w / 2.0, oy + h / 2.0)
    # top_left: content extends into negative y so the top-left corner is (0,0)
    return Transform(1.0, -1.0, -ox, oy)


def _points(sp: Subpath, tol: float) -> list[tuple[float, float]]:
    ring = subpath_to_ring(sp, tol)
    return [(float(x), float(y)) for x, y in ring]


def _round_pts(pts, prec):
    return [(round(x, prec), round(y, prec)) for x, y in pts]


def layer_name_for(p, profile: ExportProfile) -> str:
    """Export layer: depth slices keep DEPTH_nn even when flattening (a depth
    stack collapsed to one layer would engrave everything once), and IGNORE
    keeps its own layer always - it carries construction geometry such as the
    per-slice alignment frame, and merging that into the mark layer would
    engrave a box around the artwork on every pass."""
    if p.depth_index is not None:
        return p.layer_name()
    if p.layer == Layer.IGNORE:
        return Layer.IGNORE.value
    return "ENGRAVE" if profile.flatten_layers else p.layer.value


def layer_aci_for(p, profile: ExportProfile) -> int:
    if p.depth_index is not None:
        # hatch layers get a colour 15 palette steps away from their fill layer
        return depth_layer_aci(p.depth_index + (15 if p.layer == Layer.ENGRAVE_LINE else 0))
    return profile.layer_aci.get(p.layer.value, 7)


def write_dxf(graph: PathGraph, profile: ExportProfile, out_path: str | Path, job_json: str | None = None, frame: PathGraph | None = None) -> None:
    """`frame`: optional graph whose bbox defines the coordinate transform
    (used so every per-slice depth file lands in the same place)."""
    version = "R12" if profile.dxf_version == "R12" else "R2000"
    doc = ezdxf.new(version)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    if version != "R12":
        doc.header["$LUNITS"] = 2
    msp = doc.modelspace()

    g = transform_graph(graph, dxf_transform(frame or graph, profile))
    prec = profile.precision
    use_spline = profile.curves == "spline" and version != "R12"

    layer_defs: dict[str, int] = {}
    for p in g.paths:
        layer_defs.setdefault(layer_name_for(p, profile), layer_aci_for(p, profile))
    for name in sorted(layer_defs):
        if name not in doc.layers:
            doc.layers.add(name, color=layer_defs[name])

    # extents for viewers
    if g.paths:
        doc.header["$EXTMIN"] = (g.bbox[0], g.bbox[1], 0)
        doc.header["$EXTMAX"] = (g.bbox[2], g.bbox[3], 0)

    for p in g.paths:
        lname = layer_name_for(p, profile)
        attribs = {"layer": lname}
        for sp in p.subpaths:
            if not sp.segments:
                continue
            closed = sp.closed or (profile.close_fills and p.layer == Layer.ENGRAVE_FILL)
            all_lines = all(isinstance(s, Line) for s in sp.segments)
            if use_spline and not all_lines:
                _add_spline(msp, sp, attribs, closed)
                continue
            pts = _round_pts(_points(sp, profile.flatten_tol_mm), prec)
            if len(pts) < 2:
                continue
            if version == "R12":
                pl = msp.add_polyline2d(pts, dxfattribs=attribs)
                if closed:
                    pl.close(True)
            else:
                msp.add_lwpolyline(pts, format="xy", close=closed, dxfattribs=attribs)

    if job_json and profile.embed_preset:
        try:
            doc.header.custom_vars.append("LASERTRACE_JOB", job_json[:255])
        except Exception:
            pass
    doc.saveas(str(out_path))


def _add_spline(msp, sp: Subpath, attribs: dict, closed: bool) -> None:
    """Emit one SPLINE per cubic Bezier segment (exact conversion). Lines stay lines."""
    from ezdxf.math import Bezier4P, bezier_to_bspline
    beziers = []
    for s in sp.segments:
        if isinstance(s, Cubic):
            beziers.append(Bezier4P([Vec3(s.p0), Vec3(s.c1), Vec3(s.c2), Vec3(s.p3)]))
        elif isinstance(s, Line):
            a, b = Vec3(s.p1), Vec3(s.p2)
            d = (b - a) / 3.0
            beziers.append(Bezier4P([a, a + d, b - d, b]))
        elif isinstance(s, Arc):
            pts = flatten_cubic(s.p_start, s.p_start, s.p_end, s.p_end, 0.01)
            a, b = Vec3(pts[0]), Vec3(pts[-1])
            d = (b - a) / 3.0
            beziers.append(Bezier4P([a, a + d, b - d, b]))
    if not beziers:
        return
    try:
        bspline = bezier_to_bspline(beziers)
        spline = msp.add_spline(dxfattribs=attribs)
        spline.apply_construction_tool(bspline)
        if closed:
            spline.closed = True
    except Exception:
        # fall back to a polyline if ezdxf refuses the geometry
        pts = [(float(x), float(y)) for x, y in subpath_to_ring(sp, 0.02)]
        msp.add_lwpolyline(pts, format="xy", close=closed, dxfattribs=attribs)
