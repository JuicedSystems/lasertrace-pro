"""Main window: paste -> classify -> trace -> preview -> export."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QSlider, QSplitter, QToolButton, QVBoxLayout, QWidget, QCheckBox, QTextEdit)

from lasertrace.export import export, prepare_graph, write_depth_pack
from lasertrace.export.svg import svg_string
from lasertrace.ingest import load_file, load_pil
from lasertrace.models import Job, Preset
from lasertrace.pipeline import PipelineOutput
from lasertrace.auto import build_job, is_auto_mode
from lasertrace.presets import load_preset, save_preset

from .help_content import QUICK_GUIDES, TOPICS_BY_ID
from .widgets.flow import FlowLayout
from .widgets.help import HelpDialog
from .widgets.panels import LeftPanel, RightPanel
from .widgets.preview import PreviewView, ViewMode
from .worker import History, TraceRunner


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LaserTrace Pro")
        ico = Path(__file__).resolve().parent / "assets" / "icon.ico"
        if ico.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(ico)))
        # fit the available screen (never larger than it), keep a sane minimum
        scr = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        if scr is not None:
            self.resize(min(1500, int(scr.width() * 0.92)), min(900, int(scr.height() * 0.9)))
            self.move(scr.x() + (scr.width() - self.width()) // 2, scr.y() + (scr.height() - self.height()) // 2)
        else:
            self.resize(1500, 900)
        self.setMinimumSize(1000, 560)  # left 260 + center 400 + right 340
        self.rgb: np.ndarray | None = None
        self.source = None
        self.job: Job = Job.from_preset(load_preset("logo-fill"))
        self.output: PipelineOutput | None = None
        self.history = History()
        self._t0 = 0.0
        self._last_dir = str(Path.home())

        self.left = LeftPanel()
        self.right = RightPanel()
        self.preview = PreviewView()

        center = QWidget()
        cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0)
        # wrapping toolbar: never needs horizontal scrolling
        bar_w = QWidget()
        bar = FlowLayout(bar_w, margin=2, h_spacing=6, v_spacing=4)
        views = QWidget(); vl = QHBoxLayout(views); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(2)
        self.view_btns = []
        for i, name in enumerate(["1 Original", "2 Binary", "3 Vectors", "4 Laser sim"]):
            b = QToolButton(); b.setText(name); b.setCheckable(True)
            b.clicked.connect(lambda _, k=i: self.set_view(k))
            vl.addWidget(b); self.view_btns.append(b)
        bar.addWidget(views)
        overlays = QWidget(); ol = QHBoxLayout(overlays); ol.setContentsMargins(8, 0, 0, 0); ol.setSpacing(8)
        self.cb_nodes = QCheckBox("Nodes"); self.cb_nodes.setChecked(True); self.cb_nodes.toggled.connect(self._overlay_changed)
        self.cb_open = QCheckBox("Open ends"); self.cb_open.setChecked(True); self.cb_open.toggled.connect(self._overlay_changed)
        self.cb_tiny = QCheckBox("Tiny paths"); self.cb_tiny.setChecked(True); self.cb_tiny.toggled.connect(self._overlay_changed)
        self.cb_grid = QCheckBox("mm grid"); self.cb_grid.setChecked(True); self.cb_grid.toggled.connect(self._overlay_changed)
        for cb in (self.cb_nodes, self.cb_open, self.cb_tiny, self.cb_grid):
            ol.addWidget(cb)
        bar.addWidget(overlays)
        splitw = QWidget(); sl = QHBoxLayout(splitw); sl.setContentsMargins(8, 0, 0, 0); sl.setSpacing(6)
        sl.addWidget(QLabel("Split"))
        self.split = QSlider(Qt.Orientation.Horizontal); self.split.setRange(0, 100); self.split.setFixedWidth(120)
        self.split.setToolTip("Reveal the original on the left part of the view")
        self.split.valueChanged.connect(self._split_changed)
        sl.addWidget(self.split)
        fit = QToolButton(); fit.setText("Fit (F)"); fit.clicked.connect(self.preview.fit); sl.addWidget(fit)
        helpb = QToolButton(); helpb.setText("? Help (F1)")
        helpb.setToolTip("How this app works, quick guides and troubleshooting")
        helpb.clicked.connect(self.context_help)
        sl.addWidget(helpb)
        bar.addWidget(splitw)
        cl.addWidget(bar_w)
        cl.addWidget(self.preview, 1)

        split = QSplitter()
        split.addWidget(self.left); split.addWidget(center); split.addWidget(self.right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setStretchFactor(2, 0)
        split.setCollapsible(1, False)
        split.setSizes([270, max(500, self.width() - 270 - 360), 360])
        self.setCentralWidget(split)
        self._help: HelpDialog | None = None
        self._help_menu()
        self.statusBar().showMessage("Paste a logo (Ctrl+V) to start.   Press F1 for help.")

        # wiring
        self.left.help_requested.connect(self.show_help)
        self.right.help_requested.connect(self.show_help)
        self.left.open_clicked.connect(self.open_dialog)
        self.left.paste_clicked.connect(self.paste)
        self.left.file_dropped.connect(self.open_path)
        self.left.preset_changed.connect(self.apply_preset)
        self.left.stack_changed.connect(self._stack_changed)
        self.right.settings_changed.connect(self._settings_changed)
        self.right.export_dxf.connect(lambda: self.export_as("dxf"))
        self.right.export_svg.connect(lambda: self.export_as("svg"))
        self.right.export_pack.connect(self.export_depth_pack)
        self.right.copy_svg.connect(self.copy_svg)
        self.right.save_preset.connect(self.save_preset_dialog)
        self.right.compare.connect(self.compare_engines)

        self.runner = TraceRunner(self)
        self.runner.result.connect(self._on_result)
        self.runner.error.connect(self._on_error)
        self.runner.busy.connect(lambda b: self.statusBar().showMessage("Tracing..." if b else "Ready"))
        self.debounce = QTimer(self); self.debounce.setSingleShot(True); self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.retrace)

        self._shortcuts()
        self.right.show_job(self.job)
        self.left.show_stack(self.job)
        self.set_view(ViewMode.WIREFRAME)

    # ------------------------------------------------------------------ help
    def _help_menu(self) -> None:
        m = self.menuBar().addMenu("&Help")

        def item(label: str, topic: str, menu=m) -> None:
            a = QAction(label, self)
            a.setStatusTip(TOPICS_BY_ID[topic].summary)
            a.triggered.connect(lambda _=False, t=topic: self.show_help(t))
            menu.addAction(a)

        contents = QAction("&Help contents", self)
        contents.setShortcut(QKeySequence("F1"))
        contents.setStatusTip("Open the help window on the topic that fits what you are doing")
        contents.triggered.connect(self.context_help)
        m.addAction(contents)
        item("What this app is &for", "overview")
        item("The 60-second &workflow", "workflow")
        item("What's on &screen", "screen")
        item("&Keyboard shortcuts", "shortcuts")
        m.addSeparator()
        guides = m.addMenu("&Quick guides")
        for topic in QUICK_GUIDES:
            item(TOPICS_BY_ID[topic].title, topic, menu=guides)
        m.addSeparator()
        item("&Depth engraving explained", "depth-what")
        item("Depth: &calibrate removal per pass", "depth-calibrate")
        item("&Warnings and what to do", "warnings")
        m.addSeparator()
        item("&About LaserTrace Pro", "about")

    def show_help(self, topic: str | None = None) -> None:
        if self._help is None:
            self._help = HelpDialog(self)
        self._help.pop_up(topic or "overview")

    def context_help(self) -> None:
        """F1: open the topic that matches what the operator is currently doing."""
        if self.job.depth.enabled:
            self.show_help("depth-what")
        elif self.output is not None and any(w.severity in ("error", "warn") for w in self.output.result.warnings):
            self.show_help("warnings")
        elif self.rgb is None:
            self.show_help("overview")
        else:
            self.show_help("workflow")

    # ------------------------------------------------------------------ keys
    def _shortcuts(self):
        def sc(keys, fn):
            s = QShortcut(QKeySequence(keys), self); s.setContext(Qt.ShortcutContext.ApplicationShortcut); s.activated.connect(fn)
        sc("Ctrl+V", self.paste)
        sc("Ctrl+O", self.open_dialog)
        sc("E", lambda: self.export_as("dxf"))
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Return", self.retrace)
        sc("Enter", self.retrace)
        sc("Space", self.toggle_original)
        for k in range(4):
            sc(str(k + 1), lambda k=k: self.set_view(k))
        sc("[", lambda: self.nudge_threshold(-4))
        sc("]", lambda: self.nudge_threshold(4))
        sc("Ctrl+=", lambda: self.preview.scale(1.2, 1.2))
        sc("Ctrl+-", lambda: self.preview.scale(1 / 1.2, 1 / 1.2))
        sc("F", self.preview.fit)

    # ------------------------------------------------------------------ input
    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open artwork", self._last_dir, "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.pdf *.svg)")
        if p:
            self.open_path(p)

    def open_path(self, path: str):
        try:
            rgb, src = load_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Cannot open", str(e)); return
        self._last_dir = str(Path(path).parent)
        self._new_source(rgb, src, Path(path).name)

    def paste(self):
        md = QApplication.clipboard().mimeData()
        if md.hasImage():
            img: QImage = QApplication.clipboard().image().convertToFormat(QImage.Format.Format_RGB888)
            w, h = img.width(), img.height()
            ptr = img.constBits()
            arr = np.frombuffer(ptr, np.uint8, h * img.bytesPerLine()).reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()
            from PIL import Image
            rgb, src = load_pil(Image.fromarray(arr), origin="clipboard")
            self._new_source(rgb, src, "clipboard")
        elif md.hasUrls():
            self.open_path(md.urls()[0].toLocalFile())
        else:
            self.statusBar().showMessage("Clipboard has no image.")

    def _new_source(self, rgb, src, name: str):
        self.rgb = rgb
        self.source = src
        mode = self.left.preset.currentData() if is_auto_mode(self.left.preset.currentData()) else "auto"
        self.job = build_job(rgb, mode, self.right.width.value(), src)
        self.left.set_preset_name(mode)
        self.left.auto_label.setText(self.job.notes)
        self.left.show_stack(self.job)
        self.right.show_job(self.job)
        self.preview.set_source(rgb)
        self.history = History(); self.history.push(self.job)
        self.setWindowTitle(f"LaserTrace Pro - {name}  {rgb.shape[1]}x{rgb.shape[0]} px")
        self.retrace()

    # ------------------------------------------------------------------ settings
    def apply_preset(self, name: str):
        width = self.job.export.width_mm
        if is_auto_mode(name) and self.rgb is not None:
            self.job = build_job(self.rgb, name, width, self.source)
            self.left.auto_label.setText(self.job.notes)
        else:
            self.job = Job.from_preset(load_preset(name if not is_auto_mode(name) else "logo-fill"), self.source)
            self.job.export.width_mm = width
            self.left.auto_label.setText("")
        self.left.show_stack(self.job)
        self.right.show_job(self.job)
        self.history.push(self.job)
        self.retrace()

    def _settings_changed(self):
        self.right.read_into(self.job)
        self.debounce.start()

    def _stack_changed(self):
        self.left.read_stack_into(self.job)
        self.debounce.start()

    def nudge_threshold(self, d: int):
        self.right.threshold.set(max(0, min(255, self.right.threshold.value() + d)))
        self._settings_changed()

    def retrace(self):
        if self.rgb is None:
            return
        self.right.read_into(self.job)
        self.history.push(self.job)
        self._t0 = time.perf_counter()
        self.runner.submit(self.rgb, self.job)

    def undo(self):
        j = self.history.undo(self.job)
        if j is not None:
            self._restore(j)

    def redo(self):
        j = self.history.redo()
        if j is not None:
            self._restore(j)

    def _restore(self, j: Job):
        self.job = j
        self.left.show_stack(j); self.left.set_preset_name(j.preset_name or "")
        self.right.show_job(j)
        if self.rgb is not None:
            self.runner.submit(self.rgb, self.job)

    # ------------------------------------------------------------------ results
    def _on_result(self, out: PipelineOutput):
        self.output = out
        res = out.result
        elapsed = (time.perf_counter() - self._t0) * 1000
        tiny = {pid for w in res.warnings if w.code == "TINY_PATHS" for pid in w.path_ids}
        self.preview.set_depth(out.height)
        self.preview.set_result(out.binary, res.graph, out.px_per_mm, tiny, self.job.trace.min_feature_mm)
        self.right.show_stats(res.stats, res.engine, elapsed)
        self.right.show_depth(res.depth)
        self.right.show_warnings(res.warnings)
        self.statusBar().showMessage(f"{res.stats.paths} paths, {res.stats.nodes} nodes, {res.stats.width_mm:.1f} x {res.stats.height_mm:.1f} mm  -  {elapsed:.0f} ms")

    def _on_error(self, msg: str):
        self.statusBar().showMessage("Trace failed - see dialog")
        QMessageBox.critical(self, "Trace failed", msg[-2000:])

    # ------------------------------------------------------------------ views
    def set_view(self, k: int):
        for i, b in enumerate(self.view_btns):
            b.setChecked(i == k)
        self.preview.set_mode(k)

    def toggle_original(self):
        self.set_view(ViewMode.ORIGINAL if self.preview.mode != ViewMode.ORIGINAL else ViewMode.WIREFRAME)

    def _overlay_changed(self, _=None):
        self.preview.show_nodes = self.cb_nodes.isChecked()
        self.preview.show_open_ends = self.cb_open.isChecked()
        self.preview.show_tiny = self.cb_tiny.isChecked()
        self.preview.show_grid = self.cb_grid.isChecked()
        self.preview.redraw()

    def _split_changed(self, v: int):
        self.preview.split = v / 100.0
        self.preview.redraw()

    # ------------------------------------------------------------------ export
    def _job_json(self) -> str:
        return json.dumps(self.job.model_dump(mode="json"), separators=(",", ":"))

    def export_as(self, fmt: str):
        if self.output is None:
            self.statusBar().showMessage("Nothing to export yet."); return
        self.right.read_into(self.job)
        stem = Path(self.source.path).stem if self.source and self.source.path else "lasertrace"
        default = str(Path(self._last_dir) / f"{stem}_{int(self.job.export.width_mm or 50)}mm.{fmt}")
        filt = {"dxf": "DXF (*.dxf)", "svg": "SVG (*.svg)", "plt": "HPGL (*.plt)", "png": "PNG (*.png)", "pdf": "PDF (*.pdf)"}[fmt]
        p, _ = QFileDialog.getSaveFileName(self, f"Export {fmt.upper()}", default, filt + ";;All (*)")
        if not p:
            return
        try:
            export(self.output.result, self.job.export, p, fmt=fmt, job_json=self._job_json(), height=self.output.height, job=self.job)
            self._last_dir = str(Path(p).parent)
            self.statusBar().showMessage(f"Exported {p}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def export_depth_pack(self, directory: str | None = None):
        if self.output is None or self.output.result.depth is None:
            self.statusBar().showMessage("Run a depth job first (enable Depth engraving)."); return
        self.right.read_into(self.job)
        stem = Path(self.source.path).stem if self.source and self.source.path else "lasertrace"
        d = directory or QFileDialog.getExistingDirectory(self, "Depth pack folder", str(Path(self._last_dir) / f"{stem}_depth"))
        if not d:
            return
        try:
            files = write_depth_pack(self.output.result, self.job, d, height=self.output.height, stem=stem, job_json=self._job_json())
            self._last_dir = str(Path(d).parent)
            self.statusBar().showMessage(f"Depth pack: {len(files)} files in {d}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def copy_svg(self):
        if self.output is None:
            return
        self.right.read_into(self.job)
        g = prepare_graph(self.output.result.graph, self.job.export)
        QApplication.clipboard().setText(svg_string(g, self.job.export, self._job_json()))
        self.statusBar().showMessage("SVG copied to clipboard (paste into LightBurn / Inkscape).")

    def save_preset_dialog(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name (e.g. customer-acme-tumbler):")
        if not ok or not name.strip():
            return
        self.right.read_into(self.job)
        slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in name.strip().lower())
        p = Preset(name=slug, display_name=name.strip(), description="user preset", stack=self.job.stack, trace=self.job.trace, export=self.job.export)
        f = save_preset(p)
        self.left.preset.addItem(p.display_name, p.name)
        self.statusBar().showMessage(f"Saved {f}")

    # ------------------------------------------------------------------ compare
    def compare_engines(self):
        if self.rgb is None:
            return
        from lasertrace.pipeline import run
        dlg = QDialog(self); dlg.setWindowTitle("Engine compare (same binary)"); dlg.resize(900, 500)
        lay = QHBoxLayout(dlg)
        variants = {
            "LaserTrace contour": {},
            "Potrace (sidecar)": {"engine": "potrace"},
            "Illustrator-like (dense)": {"detail": 1.0, "smoothness": 0.2, "corner_sharpness": 0.9, "curve_tol_mm": 0.01, "union_fills": False},
            "Vector Magic-like (smooth)": {"detail": 0.4, "smoothness": 0.9, "corner_sharpness": 0.3, "curve_tol_mm": 0.06},
        }
        for title, upd in variants.items():
            j = self.job.model_copy(deep=True)
            j.trace = j.trace.model_copy(update=upd)
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{title}</b>"))
            try:
                out = run(self.rgb, j)
                st = out.result.stats
                v = PreviewView(); v.set_source(self.rgb)
                v.set_result(out.binary, out.result.graph, out.px_per_mm, set(), j.trace.min_feature_mm)
                v.show_grid = False; v.set_mode(ViewMode.WIREFRAME); v.fit()
                col.addWidget(v, 1)
                lab = QLabel(f"paths {st.paths}  nodes {st.nodes}  holes {st.holes}  fidelity {'-' if st.iou_vs_binary is None else f'{st.iou_vs_binary*100:.1f}%'}  {st.time_ms.get('total', 0):.0f} ms\n" + "; ".join(w.code for w in out.result.warnings))
            except Exception as e:
                lab = QLabel(f"failed: {e}")
            lab.setObjectName("stat"); lab.setWordWrap(True)
            col.addWidget(lab)
            lay.addLayout(col)
        dlg.exec()

    def closeEvent(self, ev):
        self.runner.shutdown()
        super().closeEvent(ev)
