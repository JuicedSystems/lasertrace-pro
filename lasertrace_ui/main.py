from __future__ import annotations

import os
import sys


def _assets_dir():
    """Locate the bundled assets.

    NOT `Path(__file__).parent`: PyInstaller runs this file as `__main__` from
    its own staging path, so `__file__` no longer sits next to the package.
    Going through the imported package works frozen and unfrozen alike.
    """
    from pathlib import Path

    import lasertrace_ui
    return Path(lasertrace_ui.__file__).resolve().parent / "assets"


ASSETS = _assets_dir()


def selftest() -> int:
    """Headless smoke test: trace an image, write a DXF, print the stats, exit.

    A packaged app cannot be poked at from the outside — if the built-in
    presets or an engine failed to make it into the bundle, the only symptom is
    a window that dies on the user's machine. So `--selftest` runs the whole
    pipeline with no window and a real exit code, which is what
    `tools/verify_bundle.py` (and CI) check before a build is published.

    Reads `LASERTRACE_SELFTEST_SRC` / `LASERTRACE_SELFTEST` for the input and
    output paths; generates a ring and writes to a temp file otherwise.
    """
    import tempfile
    from pathlib import Path

    import numpy as np

    from lasertrace import __version__
    from lasertrace.auto import build_job
    from lasertrace.export import export
    from lasertrace.ingest import load_file
    from lasertrace.pipeline import run
    from lasertrace.presets import PRESET_DIR, list_presets

    print(f"LaserTrace Pro {__version__} selftest")
    print(f"  frozen      {getattr(sys, 'frozen', False)}")
    print(f"  presets     {len(list_presets())} from {PRESET_DIR}")

    src = os.environ.get("LASERTRACE_SELFTEST_SRC")
    if src:
        rgb, source = load_file(src)
    else:
        n = 200
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        r = np.hypot(xx - n / 2, yy - n / 2)
        g = np.where((r > 40) & (r < 80), 0, 255).astype(np.uint8)
        rgb, source = np.stack([g] * 3, axis=-1), None

    job = build_job(rgb, "auto", 40.0, source)
    res = run(rgb, job).result
    st = res.stats
    print(f"  traced      {st.paths} paths, {st.nodes} nodes, {st.holes} holes, "
          f"{st.width_mm:.1f} x {st.height_mm:.1f} mm")

    out = Path(os.environ.get("LASERTRACE_SELFTEST") or Path(tempfile.gettempdir()) / "lasertrace_selftest.dxf")
    export(res, job.export, out)
    size = out.stat().st_size
    print(f"  exported    {out} ({size} bytes)")

    if st.paths < 1 or size < 200:
        print("  FAILED: nothing traced or nothing written")
        return 1

    # the GUI stack has to import too, or the app is a dead icon
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from lasertrace_ui.app import MainWindow  # noqa: F401
    print("  gui         importable")
    print("selftest ok")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    if argv and argv[0] in ("-V", "--version"):
        from lasertrace import __version__
        print(f"LaserTrace Pro {__version__}")
        return 0

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from lasertrace_ui.app import MainWindow
    from lasertrace_ui.theme import DARK_QSS

    if sys.platform == "win32":
        # give the process its own taskbar identity so Windows shows our icon, not python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JuicedSystems.LaserTracePro")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("LaserTrace Pro")
    app.setOrganizationName("Juiced Systems")
    ico = ASSETS / "icon.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    opened = [a for a in argv if not a.startswith("-")]
    if opened:
        win.open_path(opened[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
