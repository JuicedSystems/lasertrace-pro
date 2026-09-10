"""HPGL / PLT export: 40 plotter units per mm (0.025 mm), y-up, origin bottom-left.
One pen per layer (SP1 ENGRAVE_FILL, SP2 ENGRAVE_LINE, SP3 CUT, SP4 SCORE)."""
from __future__ import annotations

from pathlib import Path

from ..geometry.polygons import subpath_to_ring
from ..hygiene.simplify import transform_graph
from ..models import ExportProfile, Layer, PathGraph
from ..units import Transform

UNITS_PER_MM = 40.0
PEN = {Layer.ENGRAVE_FILL: 1, Layer.ENGRAVE_LINE: 2, Layer.CUT: 3, Layer.SCORE: 4, Layer.IGNORE: 5}


def plt_string(graph: PathGraph, profile: ExportProfile) -> str:
    g = transform_graph(graph, Transform(1.0, -1.0, -graph.bbox[0], graph.height + graph.bbox[1]))
    out = ["IN;", "PA;"]
    current_pen = None
    groups: list[tuple[int, list]] = []
    depth_idx = sorted({p.depth_index for p in g.paths if p.depth_index is not None})
    for i in depth_idx:  # depth slices: pen number = slice index
        groups.append((i, [p for p in g.paths if p.depth_index == i]))
    for layer in (Layer.ENGRAVE_FILL, Layer.ENGRAVE_LINE, Layer.CUT, Layer.SCORE, Layer.IGNORE):
        paths = [p for p in g.paths if p.layer == layer and p.depth_index is None]
        if paths:
            flat = profile.flatten_layers and layer != Layer.IGNORE   # frames keep their own pen
            groups.append((1 if flat else PEN[layer], paths))
    for pen, paths in groups:
        if pen != current_pen:
            out.append(f"SP{pen};")
            current_pen = pen
        for p in paths:
            for sp in p.subpaths:
                pts = subpath_to_ring(sp, profile.flatten_tol_mm)
                if len(pts) < 2:
                    continue
                ints = [(int(round(x * UNITS_PER_MM)), int(round(y * UNITS_PER_MM))) for x, y in pts]
                if sp.closed:
                    ints.append(ints[0])
                out.append(f"PU{ints[0][0]},{ints[0][1]};")
                body = ",".join(f"{x},{y}" for x, y in ints[1:])
                out.append(f"PD{body};")
    out.append("PU;")
    out.append("SP0;")
    out.append("IN;")
    return "\n".join(out) + "\n"


def write_plt(graph: PathGraph, profile: ExportProfile, out_path: str | Path) -> None:
    Path(out_path).write_text(plt_string(graph, profile), encoding="ascii")
