"""Exporters. All take a PathGraph in mm (y-down, origin top-left) and an ExportProfile."""
from __future__ import annotations

from pathlib import Path

from ..models import ExportProfile, PathGraph, TraceResult
from ..hygiene.simplify import scale_graph
from .svg import write_svg, svg_string
from .dxf import write_dxf
from .plt import write_plt, plt_string
from .png import write_png

FORMATS = {"dxf", "svg", "plt", "png", "pdf"}


def prepare_graph(graph: PathGraph, profile: ExportProfile) -> PathGraph:
    """Scale to the requested size (no-op when already right)."""
    if profile.width_mm or profile.height_mm:
        return scale_graph(graph, profile.width_mm, profile.height_mm)
    return graph


def export(result: TraceResult, profile: ExportProfile, out_path: str | Path, fmt: str | None = None, job_json: str | None = None,
           height=None, job=None) -> Path:
    """`height` + `job`: pass the depth pipeline's height field so a PNG export
    of a depth result writes the height map (LightBurn 3D Sliced input)
    instead of the 1-bit mask."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or out.suffix.lstrip(".") or profile.format).lower()
    graph = prepare_graph(result.graph, profile)
    if fmt == "png" and result.depth is not None and height is not None and job is not None:
        from .depth import height_crop, write_height_png
        write_height_png(height_crop(height, graph.px_per_mm or 1.0, graph), job, graph.width, out)
        return out
    if fmt == "dxf":
        write_dxf(graph, profile, out, job_json=job_json)
    elif fmt == "svg":
        write_svg(graph, profile, out, job_json=job_json)
    elif fmt == "plt":
        write_plt(graph, profile, out)
    elif fmt == "png":
        write_png(graph, profile, out)
    elif fmt == "pdf":
        from .pdf import write_pdf
        write_pdf(graph, profile, out)
    else:
        raise ValueError(f"unsupported export format {fmt!r}; use one of {sorted(FORMATS)}")
    return out


__all__ = ["export", "prepare_graph", "write_svg", "svg_string", "write_dxf", "write_plt", "plt_string", "write_png", "FORMATS", "write_depth_pack"]


def write_depth_pack(result: TraceResult, job, out_dir, height=None, stem: str = "depth", job_json: str | None = None, **kw) -> list[Path]:
    from .depth import write_depth_pack as _w
    return _w(result, job, out_dir, height=height, stem=stem, job_json=job_json, **kw)
