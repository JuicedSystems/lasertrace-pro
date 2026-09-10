"""Prove an *installed* copy of LaserTrace Pro actually works.

Run this from a directory that is NOT the source tree — otherwise `lasertrace/`
in the current directory shadows the installed package and everything passes
for the wrong reason:

    cd /tmp && python /path/to/repo/tools/verify_install.py

It exists because an editable install hides packaging bugs: the built-in
presets used to live at the repo root, so every real `pip install` produced a
wheel with none of them and `load_preset` raised for every name.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []


def check(label: str, fn):
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001 - this script reports, it does not raise
        FAILURES.append(f"{label}: {type(e).__name__}: {e}")
        print(f"  FAIL  {label}\n          {type(e).__name__}: {e}")
    else:
        print(f"  ok    {label}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    print("LaserTrace Pro install check")
    print(f"  python  {sys.version.split()[0]}")
    print(f"  cwd     {Path.cwd()}")
    print()

    import lasertrace

    pkg = Path(lasertrace.__file__).resolve().parent
    print(f"  package {pkg}")
    if (Path.cwd() / "lasertrace" / "__init__.py").exists():
        print("  WARNING: running inside the source tree - this proves nothing about an install")
    print()

    def presets():
        from lasertrace.presets import PRESET_DIR, list_presets, load_preset
        assert PRESET_DIR.exists(), f"preset dir missing: {PRESET_DIR}"
        found = list_presets()
        assert len(found) >= 12, f"only {len(found)} presets"
        load_preset("logo-fill")
        load_preset("depth-relief")
        return f"{len(found)} presets from {PRESET_DIR.name}/"

    def entry_points():
        from importlib.metadata import entry_points as eps
        names = {e.name for e in eps(group="console_scripts") if "lasertrace" in e.name}
        assert {"lasertrace", "lasertrace-ui"} <= names, f"console scripts: {names or 'none'}"
        return ", ".join(sorted(names))

    def trace():
        import numpy as np
        from lasertrace.auto import build_job
        from lasertrace.pipeline import run
        rgb = np.full((240, 240, 3), 255, np.uint8)
        rgb[40:200, 40:200] = 0
        rgb[90:150, 90:150] = 255            # a hole, so we can check it survives
        job = build_job(rgb, "auto", 30.0)
        res = run(rgb, job).result
        assert res.stats.paths >= 1, "no paths traced"
        assert res.stats.holes >= 1, "the hole was lost"
        assert abs(res.stats.width_mm - 30.0) < 0.5, res.stats.width_mm
        return f"{res.stats.paths} paths, {res.stats.nodes} nodes, {res.stats.holes} hole"

    def export_dxf():
        import numpy as np
        import ezdxf
        from lasertrace.auto import build_job
        from lasertrace.export import export
        from lasertrace.pipeline import run
        rgb = np.full((200, 200, 3), 255, np.uint8)
        rgb[40:160, 40:160] = 0
        job = build_job(rgb, "auto", 25.0)
        res = run(rgb, job).result
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "probe.dxf"
            export(res, job.export, out)
            assert out.stat().st_size > 200, "dxf suspiciously small"
            doc = ezdxf.readfile(str(out))
            layers = {e.dxf.layer for e in doc.modelspace()}
            assert layers, "no entities in the dxf"
            return f"{out.stat().st_size} bytes, layers {sorted(layers)}"

    def depth():
        import numpy as np
        from lasertrace.models import Job
        from lasertrace.pipeline import run
        from lasertrace.presets import load_preset
        yy, xx = np.mgrid[0:200, 0:200].astype(np.float32)
        z = np.clip(1 - np.hypot(xx - 100, yy - 100) / 90.0, 0, 1)
        rgb = np.stack([(255 * (1 - z)).astype(np.uint8)] * 3, axis=-1)
        job = Job.from_preset(load_preset("depth-relief"))
        job.export.width_mm = 30.0
        job.depth.levels = 6
        res = run(rgb, job).result
        assert res.depth is not None and res.depth.levels == 6
        assert res.graph.depth_levels() == 6, res.graph.depth_levels()
        return f"{res.depth.levels} slices, {res.depth.total_depth_mm} mm"

    def no_qt_in_core():
        assert not [m for m in sys.modules if m.startswith(("PySide", "PyQt"))], "core imported Qt"
        return "core is Qt-free"

    check("built-in presets are installed", presets)
    check("console scripts registered", entry_points)
    check("trace a shape, keep its hole", trace)
    check("export a readable DXF", export_dxf)
    check("depth pipeline runs", depth)
    check("core did not import Qt", no_qt_in_core)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        return 1
    print("all checks passed - this install is usable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
