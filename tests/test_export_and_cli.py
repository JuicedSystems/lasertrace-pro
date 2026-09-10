import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from PIL import Image

from lasertrace.cli import main as cli_main
from lasertrace.export import export
from lasertrace.export.dxf import dxf_transform
from lasertrace.export.plt import plt_string
from lasertrace.export.svg import svg_string
from lasertrace.models import ExportProfile, Job, Layer
from lasertrace.pipeline import run
from lasertrace.presets import load_preset


@pytest.fixture
def ring_result(ring_rgb):
    job = Job.from_preset(load_preset("logo-fill"))
    job.export.width_mm = 50
    return run(ring_rgb, job).result, job


def test_svg_is_plain_paths_in_mm(ring_result):
    res, job = ring_result
    s = svg_string(res.graph, job.export)
    root = ET.fromstring(s)
    assert root.attrib["width"].endswith("mm") and root.attrib["height"].endswith("mm")
    vb = [float(v) for v in root.attrib["viewBox"].split()]
    assert abs(vb[2] - 50) < 1e-6
    ns = {"s": "http://www.w3.org/2000/svg"}
    paths = root.findall(".//s:path", ns)
    assert len(paths) == 1
    assert "Z" in paths[0].attrib["d"]
    assert root.find(".//s:text", ns) is None and root.find(".//s:filter", ns) is None
    g = root.find(".//s:g", ns)
    assert g.attrib["id"] == "ENGRAVE_FILL" and g.attrib["fill-rule"] == "evenodd"


def test_dxf_units_layers_and_yflip(ring_result, tmp_path):
    res, job = ring_result
    out = tmp_path / "ring.dxf"
    export(res, job.export, out)
    doc = ezdxf.readfile(str(out))
    assert doc.header["$INSUNITS"] == 4
    assert "ENGRAVE_FILL" in doc.layers
    ents = list(doc.modelspace())
    assert len(ents) == 2  # outer + hole as separate closed polylines
    for e in ents:
        assert e.dxftype() == "LWPOLYLINE" and e.closed
        assert e.dxf.layer == "ENGRAVE_FILL"
    xs = [p[0] for e in ents for p in e.get_points("xy")]
    ys = [p[1] for e in ents for p in e.get_points("xy")]
    assert min(xs) >= -0.01 and min(ys) >= -0.01           # bottom-left origin
    assert abs(max(xs) - 50) < 0.05 and abs(max(ys) - 50) < 0.05


def test_dxf_transform_flips_y():
    from lasertrace.models import PathGraph
    g = PathGraph(bbox=(0, 0, 10, 20))
    t = dxf_transform(g, ExportProfile(origin="bottom_left"))
    assert t.apply((0, 0)) == (0, 20)      # top-left of the artwork -> y = H
    assert t.apply((10, 20)) == (10, 0)    # bottom-right -> y = 0
    tc = dxf_transform(g, ExportProfile(origin="center"))
    assert tc.apply((5, 10)) == (0, 0)


def test_dxf_r12_and_spline(ring_result, tmp_path):
    res, job = ring_result
    p12 = job.export.model_copy(update={"dxf_version": "R12"})
    export(res, p12, tmp_path / "r12.dxf")
    doc = ezdxf.readfile(str(tmp_path / "r12.dxf"))
    assert all(e.dxftype() == "POLYLINE" for e in doc.modelspace())
    ps = job.export.model_copy(update={"curves": "spline"})
    export(res, ps, tmp_path / "spline.dxf")
    doc = ezdxf.readfile(str(tmp_path / "spline.dxf"))
    types = {e.dxftype() for e in doc.modelspace()}
    assert "SPLINE" in types


def test_plt_hpgl_basics(ring_result):
    res, job = ring_result
    s = plt_string(res.graph, job.export)
    assert s.startswith("IN;") and "SP1;" in s and "PU" in s and "PD" in s
    # 50 mm wide -> max x 2000 plotter units
    xs = [int(tok.split(",")[0]) for line in s.splitlines() if line.startswith("PU") and "," in line for tok in [line[2:].rstrip(";")]]
    assert max(xs) <= 2001


def test_png_export_1bit(ring_result, tmp_path):
    res, job = ring_result
    out = tmp_path / "prev.png"
    export(res, job.export.model_copy(update={"png_dpi": 300}), out)
    im = Image.open(out)
    assert im.mode == "1"
    assert im.width > 500


def test_pdf_export(ring_result, tmp_path):
    res, job = ring_result
    out = tmp_path / "x.pdf"
    export(res, job.export, out)
    data = out.read_bytes()
    assert data.startswith(b"%PDF-1.4") and b"%%EOF" in data


def test_export_scales_to_requested_width(ring_result, tmp_path):
    res, job = ring_result
    prof = job.export.model_copy(update={"width_mm": 25})
    s = svg_string(res.graph, prof) if False else None  # svg_string does not scale; export() does
    export(res, prof, tmp_path / "s.svg")
    root = ET.fromstring((tmp_path / "s.svg").read_text())
    assert root.attrib["width"] == "25mm"


def test_cli_end_to_end_and_deterministic(fixtures_dir, tmp_path):
    src = fixtures_dir / "ring.png"
    out1 = tmp_path / "a.dxf"
    out2 = tmp_path / "b.dxf"
    stats = tmp_path / "stats.json"
    assert cli_main([str(src), "--preset", "logo-fill", "--width-mm", "50", "--out", str(out1), "--out", str(tmp_path / "a.svg"), "--stats", str(stats), "-q"]) == 0
    assert cli_main([str(src), "--preset", "logo-fill", "--width-mm", "50", "--out", str(out2), "-q"]) == 0
    st = json.loads(stats.read_text())
    assert st["paths"] == 1 and st["holes"] == 1 and abs(st["width_mm"] - 50) < 1e-6
    # DXF text differs only by the embedded job JSON (timestamps/ids) -> compare geometry
    d1 = ezdxf.readfile(str(out1)); d2 = ezdxf.readfile(str(out2))
    p1 = [tuple(map(tuple, e.get_points("xy"))) for e in d1.modelspace()]
    p2 = [tuple(map(tuple, e.get_points("xy"))) for e in d2.modelspace()]
    assert p1 == p2


def test_cli_batch(fixtures_dir, tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for n in ("ring.png", "circle_sharp.png"):
        (in_dir / n).write_bytes((fixtures_dir / n).read_bytes())
    out_dir = tmp_path / "out"
    assert cli_main(["--batch", str(in_dir), "--out-dir", str(out_dir), "--preset", "logo-fill", "--format", "dxf,svg", "-q"]) == 0
    assert (out_dir / "ring.dxf").exists() and (out_dir / "circle_sharp.svg").exists()
    log = (out_dir / "lasertrace_log.csv").read_text()
    assert "ring.png" in log and "circle_sharp.png" in log


def test_cli_list_and_classify(fixtures_dir, capsys):
    assert cli_main(["--list-presets"]) == 0
    assert "logo-fill" in capsys.readouterr().out
    assert cli_main([str(fixtures_dir / "qr_25_clean.png"), "--classify"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["suggested_preset"] == "qr-datamatrix"
