from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "fixtures"


def draw(w: int, h: int, fn) -> np.ndarray:
    """Return an RGB uint8 array from a PIL draw callback on a white canvas."""
    im = Image.new("L", (w, h), 255)
    fn(ImageDraw.Draw(im))
    return np.stack([np.asarray(im)] * 3, axis=-1)


@pytest.fixture
def circle_rgb() -> np.ndarray:
    return draw(600, 600, lambda d: d.ellipse((50, 50, 550, 550), fill=0))


@pytest.fixture
def ring_rgb() -> np.ndarray:
    def f(d):
        d.ellipse((50, 50, 550, 550), fill=0)
        d.ellipse((200, 200, 400, 400), fill=255)
    return draw(600, 600, f)


@pytest.fixture
def letter_b_rgb() -> np.ndarray:
    """A blocky 'B' with two counters drawn from rectangles."""
    def f(d):
        d.rectangle((100, 50, 400, 550), fill=0)
        d.rectangle((180, 120, 320, 260), fill=255)
        d.rectangle((180, 340, 320, 480), fill=255)
    return draw(500, 600, f)


@pytest.fixture
def rect_rgb() -> np.ndarray:
    return draw(800, 500, lambda d: d.rectangle((100, 100, 700, 400), fill=0))


@pytest.fixture
def specks_rgb() -> np.ndarray:
    def f(d):
        d.rectangle((100, 100, 500, 400), fill=0)
        for x, y in [(20, 20), (700, 50), (750, 450), (30, 470), (600, 250)]:
            d.rectangle((x, y, x + 2, y + 2), fill=0)
    return draw(800, 500, f)


@pytest.fixture
def lines_rgb() -> np.ndarray:
    def f(d):
        d.line([(50, 50), (550, 50), (550, 350), (50, 350), (50, 50)], fill=0, width=3)
        d.line([(300, 50), (300, 350)], fill=0, width=3)
    return draw(600, 400, f)


@pytest.fixture
def fixtures_dir() -> Path:
    if not FIXTURES.exists() or not any(FIXTURES.glob("*.png")):
        pytest.skip("fixtures not generated (run tools/make_fixtures.py)")
    return FIXTURES
