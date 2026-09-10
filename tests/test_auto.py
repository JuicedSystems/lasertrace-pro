import numpy as np

from lasertrace.auto import AUTO_MODES, analyze, build_job
from lasertrace.pipeline import run


def test_auto_bw_detects_hard_1bit(circle_rgb):
    a = analyze(circle_rgb, 50)
    assert a.hard_bw and not a.inverted
    job = build_job(circle_rgb, "auto-bw", 50)
    t = job.stack.find("threshold")
    assert t.params["method"] == "manual" and t.params["value"] == 128
    assert job.stack.find("knockout_halo") is None or not job.stack.find("knockout_halo").enabled
    assert job.trace.corner_sharpness >= 0.8
    assert "1-bit" in job.notes


def test_auto_detects_white_on_black(circle_rgb):
    inv = (255 - circle_rgb).astype(np.uint8)
    a = analyze(inv, 50)
    assert a.inverted
    job = build_job(inv, "auto", 50)
    assert job.stack.find("invert") is not None and job.stack.find("invert").enabled
    r = run(inv, job).result
    assert r.stats.paths == 1 and r.stats.nodes <= 24


def test_auto_picks_centerline_for_thin_lines(lines_rgb):
    job = build_job(lines_rgb, "auto-bw", 50)
    assert job.trace.mode == "centerline"
    job2 = build_job(lines_rgb, "auto-lines", 50)
    assert job2.trace.mode == "centerline" and job2.trace.engine == "centerline"


def test_auto_bw_outline_for_fills(ring_rgb):
    job = build_job(ring_rgb, "auto-bw", 50)
    assert job.trace.mode == "outline"


def test_auto_upscales_tiny_sources():
    from PIL import Image, ImageDraw
    im = Image.new("L", (64, 48), 255)
    ImageDraw.Draw(im).ellipse((8, 8, 40, 40), fill=0)
    rgb = np.stack([np.asarray(im)] * 3, axis=-1)
    job = build_job(rgb, "auto", 20)
    up = job.stack.find("upscale")
    assert up is not None and up.enabled and up.params["factor"] == 4.0


def test_auto_photo_tunes_from_noise(circle_rgb):
    rng = np.random.default_rng(0)
    noisy = np.clip(circle_rgb.astype(np.float32) + rng.normal(0, 12, circle_rgb.shape), 0, 255).astype(np.uint8)
    job = build_job(noisy, "auto-photo", 50)
    assert job.stack.find("denoise").enabled
    assert job.stack.find("threshold").params["method"] == "sauvola"
    clean = build_job(circle_rgb, "auto-photo", 50)
    assert not clean.stack.find("denoise").enabled


def test_all_auto_modes_run_end_to_end(ring_rgb):
    for mode in AUTO_MODES:
        job = build_job(ring_rgb, mode, 40)
        assert job.preset_name == mode and job.notes
        r = run(ring_rgb, job).result
        assert r.stats.paths >= 1
        assert not [w for w in r.warnings if w.severity == "error"]


def test_auto_qr_uses_square_polylines(fixtures_dir):
    from lasertrace.ingest import load_file
    rgb, _ = load_file(fixtures_dir / "qr_25_clean.png")
    job = build_job(rgb, "auto", 20)
    assert job.trace.smoothness == 0.0 and job.trace.corner_sharpness == 1.0
