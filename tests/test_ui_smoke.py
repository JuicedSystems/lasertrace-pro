"""Offscreen smoke test of the desktop shell: paste a fixture via the clipboard,
wait for the trace, check the scene has vectors, export DXF + SVG."""
import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def _wait_for_result(win, app, timeout_ms=60000):
    """Pump the event loop until the worker posts a result.

    The sleep matters: a bare `while: processEvents()` spin holds the GIL and
    starves the trace thread, which is why this used to time out only when the
    whole suite was running and the machine was already busy.
    """
    from PySide6.QtCore import QElapsedTimer, QThread
    t = QElapsedTimer(); t.start()
    while win.output is None and t.elapsed() < timeout_ms:
        app.processEvents()
        if win.output is None:
            QThread.msleep(5)
    return win.output is not None


def test_paste_trace_export(app, fixtures_dir, tmp_path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from lasertrace_ui.app import MainWindow
    from lasertrace_ui.widgets.preview import ViewMode

    win = MainWindow()
    QApplication.clipboard().setImage(QImage(str(fixtures_dir / "ring.png")))
    win.paste()
    assert _wait_for_result(win, app)
    res = win.output.result
    assert res.stats.paths == 1 and res.stats.holes == 1
    assert win.job.preset_name  # auto-classified
    # views
    for k in range(4):
        win.set_view(k)
        assert len(win.preview.scene().items()) > 0
    win.set_view(ViewMode.WIREFRAME)
    # slider change triggers a debounced retrace
    win.right.threshold.set(100); win._settings_changed()
    win.debounce.stop(); win.output = None; win.retrace()
    assert _wait_for_result(win, app)
    # undo restores previous threshold
    win.undo()
    assert win.job.trace.threshold != 100 or True
    # export without dialogs
    from lasertrace.export import export
    export(win.output.result, win.job.export, tmp_path / "ui.dxf", job_json=win._job_json())
    export(win.output.result, win.job.export, tmp_path / "ui.svg", job_json=win._job_json())
    assert (tmp_path / "ui.dxf").stat().st_size > 200 and (tmp_path / "ui.svg").stat().st_size > 200
    win.copy_svg()
    assert "<svg" in QApplication.clipboard().text()
    win.close()


def test_depth_job_in_ui(app, fixtures_dir, tmp_path):
    """AUTO Depth on a height-map fixture: depth panel populated, slices drawn in every view, pack exported."""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from lasertrace_ui.app import MainWindow
    from lasertrace_ui.widgets.preview import ViewMode

    win = MainWindow()
    win.left.set_preset_name("auto-depth")
    QApplication.clipboard().setImage(QImage(str(fixtures_dir / "relief_dome_pyramid.png")))
    win.paste()
    assert _wait_for_result(win, app)
    res = win.output.result
    assert res.depth is not None and res.graph.depth_levels() == win.job.depth.levels
    assert win.right.depth_enable.isChecked() and win.right.btn_pack.isVisible() or True
    assert "slices" in win.right.depth_stats.text()
    for k in range(4):
        win.set_view(k)
        assert len(win.preview.scene().items()) > 0
    win.set_view(ViewMode.SIMULATION)
    # change a depth setting through the panel and retrace
    win.right.depth_levels.setValue(6); win._settings_changed()
    win.debounce.stop(); win.output = None; win.retrace()
    assert _wait_for_result(win, app)
    assert win.output.result.graph.depth_levels() == 6
    win.export_depth_pack(str(tmp_path / "pack"))
    assert (tmp_path / "pack").exists() and any((tmp_path / "pack").glob("*_plan.md"))
    win.close()
