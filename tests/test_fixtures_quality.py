"""Quality-bar tests on the generated fixtures (skipped if fixtures are absent)."""
import json

import pytest

from lasertrace.ingest import load_file
from lasertrace.models import Job, Layer
from lasertrace.pipeline import run
from lasertrace.presets import load_preset


def trace(fixtures_dir, name, preset):
    rgb, src = load_file(fixtures_dir / name)
    job = Job.from_preset(load_preset(preset), src)
    return run(rgb, job).result


def test_circle_is_not_a_potato(fixtures_dir):
    r = trace(fixtures_dir, "circle_sharp.png", "logo-fill")
    assert r.stats.paths == 1 and r.stats.nodes <= 24


def test_rounded_rect_is_lines_plus_arcs(fixtures_dir):
    r = trace(fixtures_dir, "rounded_rect.png", "logo-fill")
    segs = r.graph.paths[0].subpaths[0].segments
    kinds = [s.kind for s in segs]
    assert kinds.count("line") == 4 and 4 <= kinds.count("cubic") <= 12


def test_text_keeps_all_counters(fixtures_dir):
    r = trace(fixtures_dir, "text_bold_counters.png", "small-text")
    # A B O B 8 0 -> 1+2+1+2+2+1 = 9 counters
    assert r.stats.holes == 9
    assert not any(w.code == "HOLES_LOST" for w in r.warnings)
    assert r.stats.iou_vs_binary > 0.95


def test_qr_modules_square_and_holes(fixtures_dir):
    r = trace(fixtures_dir, "qr_25_clean.png", "qr-datamatrix")
    assert all(s.kind == "line" for p in r.graph.paths for sp in p.subpaths for s in sp.segments)
    assert r.stats.holes == r.stats.holes_in_binary
    assert r.stats.iou_vs_binary > 0.97


def test_line_art_traces_as_centerlines(fixtures_dir):
    r = trace(fixtures_dir, "lineart_thin_3px.png", "thin-line-art")
    assert r.graph.paths and all(p.layer == Layer.ENGRAVE_LINE for p in r.graph.paths)
    assert r.stats.nodes < 150


def test_overlapping_shapes_union_into_one(fixtures_dir):
    r = trace(fixtures_dir, "overlapping_shapes_union.png", "logo-fill")
    assert r.stats.paths == 1 and r.stats.holes == 0


def test_dirty_photo_is_usable(fixtures_dir):
    r = trace(fixtures_dir, "photo_sign_skewed.jpg", "dirty-phone-photo")
    assert r.stats.paths >= 5  # frame + letters
    assert r.stats.open_paths == 0


def test_targets_gate_3x(fixtures_dir):
    targets = json.loads((fixtures_dir / "targets.json").read_text())
    worst = []
    for name, t in targets.items():
        if not (fixtures_dir / name).exists():
            continue
        r = trace(fixtures_dir, name, t["preset"])
        if r.stats.nodes > 3 * t["target_nodes"]:
            worst.append(f"{name}: {r.stats.nodes} > 3x{t['target_nodes']}")
    assert not worst, worst
