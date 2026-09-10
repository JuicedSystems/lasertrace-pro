"""Minimal vector PDF writer (no dependency). Units: 1 pt = 1/72 in; page is the
graph bbox in mm converted to pt. Fills use even-odd, y flipped to PDF y-up."""
from __future__ import annotations

import zlib
from pathlib import Path

from ..geometry.polygons import subpath_to_ring
from ..hygiene.simplify import transform_graph
from ..models import Cubic, ExportProfile, Layer, Line, PathGraph
from ..units import Transform

PT_PER_MM = 72.0 / 25.4


def _hex_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return tuple(int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def pdf_bytes(graph: PathGraph, profile: ExportProfile) -> bytes:
    g = transform_graph(graph, Transform(PT_PER_MM, -PT_PER_MM, -graph.bbox[0] * PT_PER_MM, (graph.height + graph.bbox[1]) * PT_PER_MM))
    W, H = graph.width * PT_PER_MM, graph.height * PT_PER_MM
    ops: list[str] = []
    for layer in (Layer.ENGRAVE_FILL, Layer.ENGRAVE_LINE, Layer.CUT, Layer.SCORE, Layer.IGNORE):
        paths = [p for p in g.paths if p.layer == layer]
        if not paths:
            continue
        r, gg, b = _hex_rgb(profile.layer_colors.get(layer.value, "#000000"))
        fill = layer == Layer.ENGRAVE_FILL
        ops.append(f"{r:.3f} {gg:.3f} {b:.3f} rg {r:.3f} {gg:.3f} {b:.3f} RG")
        ops.append(f"{(profile.cut_hairline_mm if layer == Layer.CUT else 0.05) * PT_PER_MM:.3f} w")
        for p in paths:
            for sp in p.subpaths:
                if not sp.segments:
                    continue
                s = sp.start()
                ops.append(f"{s[0]:.3f} {s[1]:.3f} m")
                for seg in sp.segments:
                    if isinstance(seg, Line):
                        ops.append(f"{seg.p2[0]:.3f} {seg.p2[1]:.3f} l")
                    elif isinstance(seg, Cubic):
                        ops.append(f"{seg.c1[0]:.3f} {seg.c1[1]:.3f} {seg.c2[0]:.3f} {seg.c2[1]:.3f} {seg.p3[0]:.3f} {seg.p3[1]:.3f} c")
                    else:
                        for q in subpath_to_ring(sp, 0.02)[1:]:
                            ops.append(f"{q[0]:.3f} {q[1]:.3f} l")
                if sp.closed:
                    ops.append("h")
            ops.append("f*" if fill else "S")
    content = zlib.compress("\n".join(ops).encode("latin-1"))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W:.3f} {H:.3f}] /Contents 4 0 R /Resources << >> >>".encode(),
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def write_pdf(graph: PathGraph, profile: ExportProfile, out_path: str | Path) -> None:
    Path(out_path).write_bytes(pdf_bytes(graph, profile))
