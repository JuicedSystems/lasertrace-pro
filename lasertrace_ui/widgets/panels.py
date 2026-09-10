"""Left (input + preprocess stack) and right (trace, stats, export) panels."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from lasertrace.depth.materials import MATERIALS
from lasertrace.models import DepthReport, Job, PreprocessOp, Warning
from lasertrace.preprocess.ops import OP_LABELS
from lasertrace.presets import list_presets

from .help import help_corner


class PasteTarget(QLabel):
    dropped = Signal(str)
    pasted = Signal()

    def __init__(self, parent=None):
        super().__init__("Paste artwork\nCtrl+V\n\nor drop a file here", parent)
        self.setObjectName("paste")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(150)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls() or ev.mimeData().hasImage():
            ev.acceptProposedAction()
            self.setProperty("armed", "true"); self.style().polish(self)

    def dragLeaveEvent(self, ev):
        self.setProperty("armed", "false"); self.style().polish(self)

    def dropEvent(self, ev):
        self.setProperty("armed", "false"); self.style().polish(self)
        md = ev.mimeData()
        if md.hasUrls():
            self.dropped.emit(md.urls()[0].toLocalFile())
        elif md.hasImage():
            self.pasted.emit()

    def mousePressEvent(self, ev):
        self.pasted.emit()


class SliderRow(QWidget):
    changed = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, value: float, step: float = 0.01, fmt: str = "{:.2f}", tip: str = "", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        top = QHBoxLayout()
        self.label = QLabel(label); self.value_label = QLabel(); self.value_label.setObjectName("stat")
        top.addWidget(self.label); top.addStretch(); top.addWidget(self.value_label)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.slider.valueChanged.connect(self._on)
        lay.addWidget(self.slider)
        if tip:
            self.setToolTip(tip)
        self.set(value)

    def value(self) -> float:
        return self.lo + self.slider.value() * self.step

    def set(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self.value_label.setText(self.fmt.format(self.value()))

    def _on(self, _):
        self.value_label.setText(self.fmt.format(self.value()))
        self.changed.emit(self.value())


class LeftPanel(QWidget):
    open_clicked = Signal()
    paste_clicked = Signal()
    file_dropped = Signal(str)
    preset_changed = Signal(str)
    stack_changed = Signal()
    help_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        lay = QVBoxLayout(self)
        self.paste = PasteTarget()
        self.paste.pasted.connect(self.paste_clicked)
        self.paste.dropped.connect(self.file_dropped)
        lay.addWidget(self.paste)
        row = QHBoxLayout()
        b = QPushButton("Open..."); b.clicked.connect(self.open_clicked); row.addWidget(b)
        b2 = QPushButton("Paste"); b2.clicked.connect(self.paste_clicked); row.addWidget(b2)
        lay.addLayout(row)

        gb = QGroupBox("Preset")
        gl = QVBoxLayout(gb)
        gl.addWidget(help_corner("presets-auto", self))
        self.preset = QComboBox()
        self.preset.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(16)
        from lasertrace.auto import AUTO_MODES
        for name, (disp, desc) in AUTO_MODES.items():
            self.preset.addItem(disp, name)
            self.preset.setItemData(self.preset.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        self.preset.insertSeparator(self.preset.count())
        for p in list_presets():
            self.preset.addItem(p.display_name, p.name)
            self.preset.setItemData(self.preset.count() - 1, p.description, Qt.ItemDataRole.ToolTipRole)
        self.preset.currentIndexChanged.connect(lambda i: self.preset.itemData(i) and self.preset_changed.emit(self.preset.itemData(i)))
        gl.addWidget(self.preset)
        self.auto_label = QLabel(""); self.auto_label.setObjectName("info"); self.auto_label.setWordWrap(True)
        gl.addWidget(self.auto_label)
        lay.addWidget(gb)

        gb2 = QGroupBox("Preprocess stack (toggle / reorder)")
        gl2 = QVBoxLayout(gb2)
        gl2.addWidget(help_corner("preprocess", self))
        self.stack_list = QListWidget()
        self.stack_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.stack_list.itemChanged.connect(lambda _: self.stack_changed.emit())
        gl2.addWidget(self.stack_list)
        row2 = QHBoxLayout(); row2.setSpacing(4)
        up = QPushButton("Up"); dn = QPushButton("Down"); add = QComboBox(); add.addItem("Add step...")
        add.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        add.setMinimumContentsLength(12)
        for b in (up, dn):
            b.setMinimumWidth(0)
        for k, v in OP_LABELS.items():
            add.addItem(v, k)
        rm = QPushButton("Remove")
        up.clicked.connect(lambda: self._move(-1)); dn.clicked.connect(lambda: self._move(1)); rm.clicked.connect(self._remove)
        add.currentIndexChanged.connect(lambda i: self._add(add, i))
        row2.addWidget(up); row2.addWidget(dn); row2.addWidget(rm)
        gl2.addLayout(row2); gl2.addWidget(add)
        lay.addWidget(gb2, 1)
        self._job: Job | None = None

    def set_preset_name(self, name: str) -> None:
        i = self.preset.findData(name)
        if i >= 0:
            self.preset.blockSignals(True); self.preset.setCurrentIndex(i); self.preset.blockSignals(False)

    def show_stack(self, job: Job) -> None:
        self._job = job
        self.stack_list.blockSignals(True)
        self.stack_list.clear()
        for op in job.stack.ops:
            params = ", ".join(f"{k}={v}" for k, v in op.params.items())
            it = QListWidgetItem(OP_LABELS.get(op.op, op.op) + (f"   {params}" if params else ""))
            it.setToolTip(f"{op.op}: {params or 'defaults'}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if op.enabled else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, op.op)
            self.stack_list.addItem(it)
        self.stack_list.blockSignals(False)

    def read_stack_into(self, job: Job) -> None:
        for i in range(self.stack_list.count()):
            it = self.stack_list.item(i)
            if i < len(job.stack.ops):
                job.stack.ops[i].enabled = it.checkState() == Qt.CheckState.Checked

    def _move(self, d: int) -> None:
        if self._job is None:
            return
        i = self.stack_list.currentRow()
        j = i + d
        if i < 0 or j < 0 or j >= len(self._job.stack.ops):
            return
        ops = self._job.stack.ops
        ops[i], ops[j] = ops[j], ops[i]
        self.show_stack(self._job)
        self.stack_list.setCurrentRow(j)
        self.stack_changed.emit()

    def _remove(self) -> None:
        if self._job is None:
            return
        i = self.stack_list.currentRow()
        if 0 <= i < len(self._job.stack.ops):
            del self._job.stack.ops[i]
            self.show_stack(self._job)
            self.stack_changed.emit()

    def _add(self, combo: QComboBox, i: int) -> None:
        if i <= 0 or self._job is None:
            return
        name = combo.itemData(i)
        combo.blockSignals(True); combo.setCurrentIndex(0); combo.blockSignals(False)
        self._job.stack.ops.append(PreprocessOp(op=name))
        self.show_stack(self._job)
        self.stack_changed.emit()


def _fit_form(fl: QFormLayout) -> None:
    """Forms shrink gracefully: fields stretch, long rows wrap label-over-field."""
    fl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    fl.setHorizontalSpacing(8)


class RightPanel(QWidget):
    settings_changed = Signal()
    export_dxf = Signal()
    export_svg = Signal()
    export_pack = Signal()
    copy_svg = Signal()
    save_preset = Signal()
    compare = Signal()
    help_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(340)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # vertical only, content fits the width
        body = QWidget(); lay = QVBoxLayout(body)
        body.setMinimumWidth(0)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        gb = QGroupBox("Trace")
        gl = QVBoxLayout(gb)
        gl.addWidget(help_corner("trace-settings", self))
        self.detail = SliderRow("Detail  <->  Cleanliness", 0, 1, 0.5, tip="Right = more nodes, more faithful. Left = fewer nodes.")
        self.threshold = SliderRow("Darkness threshold", 0, 255, 128, 1, "{:.0f}", "Which grays count as ink. [ and ] keys.")
        self.despeckle = SliderRow("Despeckle (mm2)", 0, 1.0, 0.02, 0.005, "{:.3f}", "Remove islands smaller than this area.")
        self.corner = SliderRow("Corner sharpness", 0, 1, 0.6, tip="Right keeps every corner (QR, text); left rounds.")
        self.minfeat = SliderRow("Minimum feature (mm)", 0.02, 1.0, 0.1, 0.01, "{:.2f}", "Smallest detail the laser can mark. Smaller paths are dropped.")
        self.smooth = SliderRow("Smoothness", 0, 1, 0.5, tip="Curve fitting. 0 = straight polylines only.")
        for s in (self.detail, self.threshold, self.despeckle, self.corner, self.minfeat, self.smooth):
            s.changed.connect(lambda _: self.settings_changed.emit())
            gl.addWidget(s)
        lay.addWidget(gb)

        # ---------------------------------------------------------- depth (3D relief)
        self.depth_box = QGroupBox("Depth engraving (3D relief)")
        dl = QFormLayout(self.depth_box)
        _fit_form(dl)
        self.depth_enable = QCheckBox("Slice a height map into depth layers")
        self.depth_enable.setToolTip("Treat the image as a height map: N cumulative slices, each a closed fill on its own DEPTH_nn layer. Pass i marks everything deeper than i/N.")
        self.depth_levels = QSpinBox(); self.depth_levels.setRange(1, 60); self.depth_levels.setValue(24)
        self.depth_levels.setToolTip("Number of slices = passes. LightBurn maps at most 30 colours; use the per-slice files or the height map PNG above that.")
        self.depth_total = QDoubleSpinBox(); self.depth_total.setRange(0.01, 10.0); self.depth_total.setSingleStep(0.05); self.depth_total.setDecimals(3); self.depth_total.setValue(0.5); self.depth_total.setSuffix(" mm")
        self.depth_total.setToolTip("Depth at the darkest (deepest) tone. Slice thickness = depth / slices.")
        self.depth_material = QComboBox()
        for k, m in MATERIALS.items():
            self.depth_material.addItem(m.name, k)
        self.depth_laser = QDoubleSpinBox(); self.depth_laser.setRange(5, 500); self.depth_laser.setValue(50); self.depth_laser.setSuffix(" W")
        self.depth_removal = QDoubleSpinBox(); self.depth_removal.setRange(0, 200); self.depth_removal.setDecimals(1); self.depth_removal.setSpecialValueText("from table"); self.depth_removal.setSuffix(" um/pass")
        self.depth_removal.setToolTip("Measured removal per pass (engrave a 10x10 mm square for 10 passes, measure, divide). 0 = use the material table.")
        self.depth_convention = QComboBox(); self.depth_convention.addItem("Black = deepest (LightBurn / EZCAD)", True); self.depth_convention.addItem("White = deepest (height / bump map)", False)
        self.depth_smooth = SliderRow("Smoothing (mm)", 0.0, 0.5, 0.05, 0.005, "{:.3f}", "Edge-preserving blur of the height map: removes 8-bit terraces and JPEG noise without rounding the walls.")
        self.depth_gamma = SliderRow("Depth curve (gamma)", 0.3, 3.0, 1.0, 0.05, "{:.2f}", "> 1 keeps more of the image shallow, < 1 deepens the midtones.")
        self.depth_draft = QDoubleSpinBox(); self.depth_draft.setRange(0, 30); self.depth_draft.setDecimals(1); self.depth_draft.setValue(8.0); self.depth_draft.setSuffix(" deg")
        self.depth_draft.setToolTip("Every wall is inset by depth x tan(draft): chamfered walls instead of a fragile undercut lip.")
        self.depth_feather = QDoubleSpinBox(); self.depth_feather.setRange(0, 2.0); self.depth_feather.setDecimals(2); self.depth_feather.setSingleStep(0.05); self.depth_feather.setSuffix(" mm")
        self.depth_equalize = SliderRow("Equalise tones", 0.0, 1.0, 0.0, 0.05, "{:.2f}", "Blend towards histogram-equalised heights so every slice removes a similar area.")
        self.depth_photo = QCheckBox("Photo to bas-relief (compress gradients)")
        self.depth_photo.setToolTip("Luminance is not height: attenuate the big gradients (shadows, edges) and re-integrate so a photo becomes a plausible relief.")
        self.depth_relief = SliderRow("Relief strength", 0.0, 1.0, 0.6, 0.05, "{:.2f}")
        self.depth_hatch = QCheckBox("Generate hatch lines per slice (rotating angle)")
        self.depth_hatch.setToolTip("Adds DEPTH_nn_HATCH line layers: pitch below, angle rotated per slice, serpentine order, inset contour pass. For EZCAD2 layer stacks; LightBurn / EZCAD3 hatch themselves.")
        self.depth_pitch = QDoubleSpinBox(); self.depth_pitch.setRange(0.005, 0.5); self.depth_pitch.setDecimals(3); self.depth_pitch.setSingleStep(0.005); self.depth_pitch.setValue(0.025); self.depth_pitch.setSuffix(" mm")
        self.depth_angle_step = QDoubleSpinBox(); self.depth_angle_step.setRange(0, 179); self.depth_angle_step.setDecimals(1); self.depth_angle_step.setValue(37.0); self.depth_angle_step.setSuffix(" deg")
        self.depth_angle_step.setToolTip("Never a divisor of 180 (0/30/45/60/90): the same lines would repeat and cut grooves. 31-37 deg is the field-proven choice.")
        self.depth_png16 = QCheckBox("16-bit height map PNG")
        self.depth_header = help_corner("depth-what", self, self.depth_enable)
        dl.addRow(self.depth_header)
        dl.addRow("Slices", self.depth_levels); dl.addRow("Total depth", self.depth_total)
        dl.addRow("Material", self.depth_material); dl.addRow("Laser power", self.depth_laser); dl.addRow("Removal", self.depth_removal)
        dl.addRow("Convention", self.depth_convention)
        dl.addRow(self.depth_smooth); dl.addRow(self.depth_gamma); dl.addRow(self.depth_equalize)
        dl.addRow("Wall draft", self.depth_draft); dl.addRow("Edge feather", self.depth_feather)
        dl.addRow(self.depth_photo); dl.addRow(self.depth_relief)
        dl.addRow(self.depth_hatch); dl.addRow("Hatch pitch", self.depth_pitch); dl.addRow("Angle step", self.depth_angle_step)
        dl.addRow(self.depth_png16)
        self.depth_stats = QLabel("-"); self.depth_stats.setObjectName("stat"); self.depth_stats.setWordWrap(True)
        self.depth_stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addRow(self.depth_stats)
        for w in (self.depth_enable, self.depth_photo, self.depth_hatch, self.depth_png16):
            w.toggled.connect(lambda _: self.settings_changed.emit())
        for w in (self.depth_levels, self.depth_total, self.depth_laser, self.depth_removal, self.depth_draft, self.depth_feather, self.depth_pitch, self.depth_angle_step):
            w.valueChanged.connect(lambda _: self.settings_changed.emit())
        for w in (self.depth_material, self.depth_convention):
            w.currentIndexChanged.connect(lambda _: self.settings_changed.emit())
        for w in (self.depth_smooth, self.depth_gamma, self.depth_equalize, self.depth_relief):
            w.changed.connect(lambda _: self.settings_changed.emit())
        self.depth_enable.toggled.connect(self._depth_visibility)
        lay.addWidget(self.depth_box)

        self.adv_btn = QToolButton(); self.adv_btn.setText("Advanced"); self.adv_btn.setCheckable(True)
        lay.addWidget(self.adv_btn)
        self.adv = QGroupBox("Advanced"); self.adv.setVisible(False)
        self.adv_btn.toggled.connect(self.adv.setVisible)
        fl = QFormLayout(self.adv)
        _fit_form(fl)
        self.engine = QComboBox(); self.engine.addItems(["contour", "potrace", "vtracer"])
        self.mode = QComboBox(); self.mode.addItems(["outline", "centerline", "hybrid"])
        self.union = QCheckBox("Union touching fills (no double mark)"); self.union.setChecked(True)
        self.cut_outer = QCheckBox("Silhouette on CUT layer")
        self.curve_tol = QDoubleSpinBox(); self.curve_tol.setRange(0.005, 1.0); self.curve_tol.setSingleStep(0.005); self.curve_tol.setDecimals(3); self.curve_tol.setValue(0.03); self.curve_tol.setSuffix(" mm")
        self.gap = QDoubleSpinBox(); self.gap.setRange(0.0, 2.0); self.gap.setSingleStep(0.01); self.gap.setDecimals(2); self.gap.setValue(0.05); self.gap.setSuffix(" mm")
        self.budget = QSpinBox(); self.budget.setRange(0, 200000); self.budget.setSpecialValueText("no cap"); self.budget.setValue(0)
        self.cl_width = QDoubleSpinBox(); self.cl_width.setRange(0.05, 5.0); self.cl_width.setSingleStep(0.05); self.cl_width.setValue(0.6); self.cl_width.setSuffix(" mm")
        fl.addRow("Engine", self.engine); fl.addRow("Mode", self.mode); fl.addRow(self.union); fl.addRow(self.cut_outer)
        fl.addRow("Curve fit tolerance", self.curve_tol); fl.addRow("Gap close", self.gap); fl.addRow("Node budget", self.budget); fl.addRow("Centerline max width", self.cl_width)
        for w in (self.engine, self.mode):
            w.currentIndexChanged.connect(lambda _: self.settings_changed.emit())
        for w in (self.union, self.cut_outer):
            w.toggled.connect(lambda _: self.settings_changed.emit())
        for w in (self.curve_tol, self.gap, self.budget, self.cl_width):
            w.valueChanged.connect(lambda _: self.settings_changed.emit())
        lay.addWidget(self.adv)

        gb3 = QGroupBox("Stats")
        self.stats = QLabel("-"); self.stats.setObjectName("stat"); self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stats.setMinimumWidth(0)
        sl3 = QVBoxLayout(gb3)
        sl3.addWidget(help_corner("stats", self))
        sl3.addWidget(self.stats)
        lay.addWidget(gb3)

        gb4 = QGroupBox("Warnings")
        wl4 = QVBoxLayout(gb4)
        wl4.addWidget(help_corner("warnings", self))
        self.warn_box = QVBoxLayout()
        wl4.addLayout(self.warn_box)
        self.warn_none = QLabel("none"); self.warn_none.setObjectName("info")
        self.warn_box.addWidget(self.warn_none)
        lay.addWidget(gb4)

        gb5 = QGroupBox("Export")
        el = QFormLayout(gb5)
        _fit_form(el)
        el.addRow(help_corner("export", self))
        self.width = QDoubleSpinBox(); self.width.setRange(1, 2000); self.width.setValue(50); self.width.setSuffix(" mm"); self.width.setDecimals(2)
        self.width.valueChanged.connect(lambda _: self.settings_changed.emit())
        self.origin = QComboBox(); self.origin.addItems(["bottom_left", "top_left", "center"])
        self.curves = QComboBox(); self.curves.addItems(["polyline", "spline"])
        self.dxfver = QComboBox(); self.dxfver.addItems(["R2000", "R12"])
        self.flatten = QCheckBox("Flatten to one layer")
        el.addRow("Width", self.width); el.addRow("DXF origin", self.origin); el.addRow("Curves", self.curves); el.addRow("DXF version", self.dxfver); el.addRow(self.flatten)
        self.btn_dxf = QPushButton("Export DXF  (E)"); self.btn_dxf.setObjectName("primary"); self.btn_dxf.clicked.connect(self.export_dxf)
        el.addRow(self.btn_dxf)
        self.btn_pack = QPushButton("Export depth pack..."); self.btn_pack.clicked.connect(self.export_pack); self.btn_pack.setVisible(False)
        self.btn_pack.setToolTip("Folder with: multi-layer DXF, one DXF per slice (EZCAD2), height map PNG (LightBurn 3D Sliced / EZCAD3), shaded preview, pass plan (md/csv/json).")
        el.addRow(self.btn_pack)
        from .flow import FlowLayout
        roww = QWidget(); row = FlowLayout(roww, margin=0, h_spacing=4, v_spacing=4)
        b = QPushButton("Export SVG"); b.clicked.connect(self.export_svg); row.addWidget(b)
        b = QPushButton("Copy SVG"); b.clicked.connect(self.copy_svg); row.addWidget(b)
        b = QPushButton("Save preset"); b.clicked.connect(self.save_preset); row.addWidget(b)
        el.addRow(roww)
        b = QPushButton("Compare engines..."); b.clicked.connect(self.compare); el.addRow(b)
        lay.addWidget(gb5)
        lay.addStretch()

    def _depth_visibility(self, on: bool) -> None:
        for i in range(self.depth_box.layout().count()):
            it = self.depth_box.layout().itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None and w is not self.depth_header:
                w.setVisible(on)
        self.btn_pack.setVisible(on)

    # ------------------------------------------------------------ job <-> ui
    def show_job(self, job: Job) -> None:
        self._show_depth(job)
        t = job.trace
        for w in (self.detail, self.threshold, self.despeckle, self.corner, self.minfeat, self.smooth):
            w.blockSignals(True)
        self.detail.set(t.detail); self.threshold.set(t.threshold); self.despeckle.set(t.despeckle_mm2)
        self.corner.set(t.corner_sharpness); self.minfeat.set(t.min_feature_mm); self.smooth.set(t.smoothness)
        for w in (self.detail, self.threshold, self.despeckle, self.corner, self.minfeat, self.smooth):
            w.blockSignals(False)
        widgets = (self.engine, self.mode, self.union, self.cut_outer, self.curve_tol, self.gap, self.budget, self.cl_width, self.width, self.origin, self.curves, self.dxfver, self.flatten)
        for w in widgets:
            w.blockSignals(True)
        self.engine.setCurrentText(t.engine); self.mode.setCurrentText(t.mode); self.union.setChecked(t.union_fills); self.cut_outer.setChecked(t.cut_outer)
        self.curve_tol.setValue(t.curve_tol_mm); self.gap.setValue(t.gap_close_mm); self.budget.setValue(t.node_budget or 0); self.cl_width.setValue(t.centerline_max_width_mm)
        e = job.export
        self.width.setValue(e.width_mm or 50); self.origin.setCurrentText(e.origin); self.curves.setCurrentText(e.curves if e.curves != "arcs" else "polyline"); self.dxfver.setCurrentText(e.dxf_version); self.flatten.setChecked(e.flatten_layers)
        for w in widgets:
            w.blockSignals(False)

    def _show_depth(self, job: Job) -> None:
        d = job.depth
        ws = (self.depth_enable, self.depth_levels, self.depth_total, self.depth_material, self.depth_laser, self.depth_removal, self.depth_convention,
              self.depth_smooth, self.depth_gamma, self.depth_equalize, self.depth_draft, self.depth_feather, self.depth_photo, self.depth_relief,
              self.depth_hatch, self.depth_pitch, self.depth_angle_step, self.depth_png16)
        for w in ws:
            w.blockSignals(True)
        self.depth_enable.setChecked(d.enabled)
        self.depth_levels.setValue(d.levels); self.depth_total.setValue(d.total_depth_mm)
        i = self.depth_material.findData(d.material); self.depth_material.setCurrentIndex(i if i >= 0 else self.depth_material.count() - 1)
        self.depth_laser.setValue(d.laser_w); self.depth_removal.setValue(d.removal_per_pass_um or 0)
        self.depth_convention.setCurrentIndex(0 if d.dark_is_deep else 1)
        self.depth_smooth.set(d.smoothing_mm); self.depth_gamma.set(d.depth_gamma); self.depth_equalize.set(d.equalize)
        self.depth_draft.setValue(d.draft_angle_deg); self.depth_feather.setValue(d.feather_mm)
        self.depth_photo.setChecked(d.photo_to_relief); self.depth_relief.set(d.relief_strength)
        self.depth_hatch.setChecked(d.hatch.enabled); self.depth_pitch.setValue(d.hatch.spacing_mm); self.depth_angle_step.setValue(d.hatch.angle_step_deg)
        self.depth_png16.setChecked(d.png_bits == 16)
        for w in ws:
            w.blockSignals(False)
        self._depth_visibility(d.enabled)

    def show_depth(self, r: DepthReport | None) -> None:
        if r is None:
            self.depth_stats.setText("-")
            return
        m = r.machine
        self.depth_stats.setText(
            f"{r.levels} slices x {r.slice_thickness_mm} mm = {r.total_depth_mm} mm\n"
            f"{m.get('material')}: ~{r.removal_per_pass_um} um/pass -> {m.get('passes_per_slice')} loop(s)/slice\n"
            f"raster passes (LightBurn 3D Sliced): {m.get('raster_passes')}\n"
            f"footprint {r.footprint_mm2:.1f} mm2, volume {r.volume_mm3:.1f} mm3\n"
            f"start: {m.get('power_pct')}% / {m.get('speed_mm_s')} mm/s / {m.get('freq_khz')} kHz" + (f" / {m.get('pulse_ns')} ns" if m.get('pulse_ns') else "") + f", pitch {m.get('hatch_spacing_mm')} mm\n"
            f"est. time ~{r.est_time_s / 60:.0f} min"
        )

    def read_into(self, job: Job) -> None:
        d = job.depth
        d.enabled = self.depth_enable.isChecked()
        d.levels = self.depth_levels.value(); d.total_depth_mm = self.depth_total.value()
        d.material = self.depth_material.currentData() or "generic"; d.laser_w = self.depth_laser.value()
        d.removal_per_pass_um = self.depth_removal.value() or None
        d.dark_is_deep = bool(self.depth_convention.currentData())
        d.smoothing_mm = self.depth_smooth.value(); d.depth_gamma = self.depth_gamma.value(); d.equalize = self.depth_equalize.value()
        d.draft_angle_deg = self.depth_draft.value(); d.feather_mm = self.depth_feather.value()
        d.photo_to_relief = self.depth_photo.isChecked(); d.relief_strength = self.depth_relief.value()
        d.hatch.enabled = self.depth_hatch.isChecked(); d.hatch.spacing_mm = self.depth_pitch.value(); d.hatch.angle_step_deg = self.depth_angle_step.value()
        d.png_bits = 16 if self.depth_png16.isChecked() else 8
        t = job.trace
        t.detail = self.detail.value(); t.threshold = int(self.threshold.value()); t.despeckle_mm2 = self.despeckle.value()
        t.corner_sharpness = self.corner.value(); t.min_feature_mm = self.minfeat.value(); t.smoothness = self.smooth.value()
        t.engine = self.engine.currentText(); t.mode = self.mode.currentText(); t.union_fills = self.union.isChecked(); t.cut_outer = self.cut_outer.isChecked()
        t.curve_tol_mm = self.curve_tol.value(); t.gap_close_mm = self.gap.value(); t.node_budget = self.budget.value() or None; t.centerline_max_width_mm = self.cl_width.value()
        e = job.export
        e.width_mm = self.width.value(); e.origin = self.origin.currentText(); e.curves = self.curves.currentText(); e.dxf_version = self.dxfver.currentText(); e.flatten_layers = self.flatten.isChecked()

    def show_stats(self, st, engine: str, elapsed_ms: float) -> None:
        self.stats.setText(
            f"engine      {engine}\n"
            f"paths       {st.paths}   nodes {st.nodes}\n"
            f"closed/open {st.closed_paths} / {st.open_paths}\n"
            f"holes       {st.holes}" + (f" (binary {st.holes_in_binary})" if st.holes_in_binary is not None else "") + "\n"
            f"size        {st.width_mm:.2f} x {st.height_mm:.2f} mm\n"
            f"fill area   {st.fill_area_mm2:.1f} mm2\n"
            f"complexity  {st.complexity:.0f}/100   travel {st.est_travel_mm:.0f} mm\n"
            + (f"fidelity    {st.iou_vs_binary * 100:.1f}%\n" if st.iou_vs_binary is not None else "")
            + f"time        {elapsed_ms:.0f} ms"
        )

    def show_warnings(self, warnings: list[Warning]) -> None:
        while self.warn_box.count():
            it = self.warn_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not warnings:
            lbl = QLabel("none"); lbl.setObjectName("info"); self.warn_box.addWidget(lbl)
            return
        for w in warnings:
            lbl = QLabel(f"{w.code}: {w.message}"); lbl.setWordWrap(True)
            lbl.setObjectName({"error": "error", "warn": "warn"}.get(w.severity, "info"))
            self.warn_box.addWidget(lbl)
