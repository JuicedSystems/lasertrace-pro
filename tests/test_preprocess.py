import hashlib

import numpy as np

from lasertrace.ingest import to_gray
from lasertrace.models import PreprocessOp, PreprocessStack
from lasertrace.preprocess import morphology, run_stack, threshold
from lasertrace.preprocess.classify import classify
from lasertrace.preprocess.ops import gray_before_threshold


def test_threshold_is_deterministic(circle_rgb):
    g = to_gray(circle_rgb)
    a = threshold.otsu(g)
    b = threshold.otsu(g)
    assert hashlib.sha256(a.tobytes()).hexdigest() == hashlib.sha256(b.tobytes()).hexdigest()
    assert a.dtype == np.uint8 and set(np.unique(a)) <= {0, 255}


def test_threshold_dark_is_ink(circle_rgb):
    g = to_gray(circle_rgb)
    m = threshold.manual(g, 128)
    assert m[300, 300] == 255  # centre of black circle is ink
    assert m[5, 5] == 0        # white corner is background


def test_all_threshold_methods_return_masks(circle_rgb):
    g = to_gray(circle_rgb)
    for method, params in [("otsu", {}), ("manual", {"value": 100}), ("adaptive", {"block": 31}), ("sauvola", {}), ("niblack", {}), ("hysteresis", {"low": 90, "high": 160})]:
        m = threshold.apply(g, method, **params)
        assert m.shape == g.shape and m.dtype == np.uint8
        assert m[300, 300] == 255, method


def test_despeckle_removes_small_islands(specks_rgb):
    m = threshold.otsu(to_gray(specks_rgb))
    assert morphology.count_components(m) == 6
    cleaned = morphology.despeckle(m, min_area_px=20)
    assert morphology.count_components(cleaned) == 1


def test_despeckle_in_mm(specks_rgb):
    m = threshold.otsu(to_gray(specks_rgb))
    cleaned = morphology.despeckle(m, min_area_mm2=0.05, px_per_mm=10)  # 5 px^2
    assert morphology.count_components(cleaned) == 6  # 3x3 = 9 px specks survive (1 rect + 5 specks)
    cleaned = morphology.despeckle(m, min_area_mm2=0.2, px_per_mm=10)  # 20 px^2
    assert morphology.count_components(cleaned) == 1


def test_fill_holes_keeps_big_counters(letter_b_rgb):
    m = threshold.otsu(to_gray(letter_b_rgb))
    assert morphology.count_holes(m) == 2
    filled = morphology.fill_holes(m, max_area_px=50)
    assert morphology.count_holes(filled) == 2
    filled = morphology.fill_holes(m, max_area_px=1e9)
    assert morphology.count_holes(filled) == 0


def test_knockout_halo_keeps_thin_strokes_and_1bit(lines_rgb):
    g = to_gray(lines_rgb)
    m = threshold.otsu(g)
    out = morphology.knockout_halo(g, m, band_px=2, halo_max_gray=200)
    assert np.array_equal(out, m)


def test_invert(circle_rgb):
    m = threshold.otsu(to_gray(circle_rgb))
    inv = morphology.invert(m)
    assert inv[300, 300] == 0 and inv[5, 5] == 255


def test_stack_runner_orders_stages_and_autothresholds(circle_rgb):
    # binary op listed before threshold and no threshold at all: runner must still produce a mask
    stack = PreprocessStack(ops=[PreprocessOp(op="despeckle", params={"min_area_px": 4}), PreprocessOp(op="levels", params={"black": 10, "white": 240})])
    m = run_stack(circle_rgb, stack, px_per_mm=10)
    assert m.dtype == np.uint8 and m[300, 300] == 255 and m[5, 5] == 0


def test_stack_disabled_ops_are_skipped(circle_rgb):
    stack = PreprocessStack(ops=[PreprocessOp(op="threshold", params={"method": "manual", "value": 128}), PreprocessOp(op="invert", enabled=False)])
    m = run_stack(circle_rgb, stack)
    assert m[300, 300] == 255
    stack.ops[1].enabled = True
    m = run_stack(circle_rgb, stack)
    assert m[300, 300] == 0


def test_gray_before_threshold(circle_rgb):
    stack = PreprocessStack(ops=[PreprocessOp(op="gamma", params={"value": 2.0}), PreprocessOp(op="threshold")])
    g = gray_before_threshold(circle_rgb, stack)
    assert g.ndim == 2 and g.dtype == np.uint8


def test_classifier_returns_known_preset(circle_rgb, lines_rgb):
    from lasertrace.presets import load_preset
    for rgb in (circle_rgb, lines_rgb):
        c = classify(rgb)
        load_preset(c.suggested_preset)  # must resolve
        assert 0 <= c.confidence <= 1


def test_classifier_detects_line_art(lines_rgb):
    assert classify(lines_rgb).kind == "line_art"
