"""Packaging: everything loaded by path at runtime must live inside a package.

Regression: the built-in presets used to sit at the repo root, so `pip install`
produced a wheel with zero of them - `load_preset` raised FileNotFoundError for
every name on any machine that had not cloned the repo. Editable installs hid
it, because they keep the source layout.
"""
import tomllib
from pathlib import Path

import pytest

import lasertrace
from lasertrace.presets import PRESET_DIR, list_presets, load_preset

ROOT = Path(__file__).resolve().parents[1]
PKG = Path(lasertrace.__file__).resolve().parent


def test_preset_dir_lives_inside_the_package():
    assert PRESET_DIR.is_relative_to(PKG), f"{PRESET_DIR} escapes the package at {PKG}"
    assert PRESET_DIR.is_dir()


def test_every_builtin_preset_loads():
    presets = list_presets()
    assert len(presets) >= 12
    for p in presets:
        assert load_preset(p.name).name == p.name


def test_ui_assets_live_inside_the_package():
    assets = ROOT / "lasertrace_ui" / "assets"
    assert (assets / "icon.ico").exists()
    assert (assets / "icon_128.png").exists()


def test_package_data_is_declared_for_every_runtime_data_dir():
    """A data directory that setuptools is not told about ships as nothing."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data = cfg["tool"]["setuptools"]["package-data"]
    assert any(g.startswith("preset_data/") for g in data["lasertrace"])
    assert any(g.startswith("assets/") for g in data["lasertrace_ui"])


def test_declared_dependencies_cover_the_core_imports():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower()
                for d in cfg["project"]["dependencies"]}
    for name in ("numpy", "opencv-python-headless", "scikit-image", "scipy",
                 "shapely", "pillow", "ezdxf", "pydantic", "pypdfium2"):
        assert name in declared, f"{name} missing from [project.dependencies]"
    # the core must stay importable without Qt
    assert not any("pyside" in d for d in declared), "PySide6 belongs in the [ui] extra"


def test_core_does_not_import_qt():
    """`lasertrace` must work on a machine with no GUI stack at all."""
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import sys
        import lasertrace, lasertrace.pipeline, lasertrace.cli, lasertrace.auto
        from lasertrace.export import export
        qt = [m for m in sys.modules if m.startswith(("PySide", "PyQt", "shiboken"))]
        print("QT:" + ",".join(qt))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "QT:\n" in r.stdout or r.stdout.strip().endswith("QT:"), f"core pulled in Qt: {r.stdout}"


def test_potrace_sidecar_is_not_shipped_inside_the_package():
    """GPL-3 code must never end up inside the MIT wheel."""
    for pkg in (PKG, ROOT / "lasertrace_ui"):
        hits = list(pkg.rglob("*potrace*"))
        assert not any(h.suffix == ".py" and "sidecar" in h.name for h in hits), hits
