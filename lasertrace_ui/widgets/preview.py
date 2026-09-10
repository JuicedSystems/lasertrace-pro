"""Four synced views on one QGraphicsView: Original | Binary | Wireframe | Laser sim.

Scene units are millimetres. Raster layers are scaled by px_per_mm so vectors
overlay the pixels exactly; the split slider reveals the original underneath.
"""
from __future__ import annotations

import math
from enum import IntEnum

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from lasertrace.models import Arc, Cubic, Layer, Line, PathGraph


class ViewMode(IntEnum):
    ORIGINAL = 0
    BINARY = 1
    WIREFRAME = 2
    SIMULATION = 3


def np_to_qimage(arr: np.ndarray) -> QImage:
    if arr.ndim == 2:
        h, w = arr.shape
        img = QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8)
    else:
        h, w, _ = arr.shape
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()


def graph_to_qpath(graph: PathGraph, layer: Layer | None = None, depth_index: int | None = None, flat_only: bool = False) -> QPainterPath:
    """`depth_index`: only that slice; `flat_only`: skip depth slices."""
    qp = QPainterPath()
    qp.setFillRule(Qt.FillRule.OddEvenFill)
    for p in graph.paths:
        if layer is not None and p.layer != layer:
            continue
        if depth_index is not None and p.depth_index != depth_index:
            continue
        if flat_only and p.depth_index is not None:
            continue
        for sp in p.subpaths:
            if not sp.segments:
                continue
            s = sp.start()
            qp.moveTo(QPointF(*s))
            for seg in sp.segments:
                if isinstance(seg, Line):
                    qp.lineTo(QPointF(*seg.p2))
                elif isinstance(seg, Cubic):
                    qp.cubicTo(QPointF(*seg.c1), QPointF(*seg.c2), QPointF(*seg.p3))
                elif isinstance(seg, Arc):
                    e = seg.p_end
                    qp.lineTo(QPointF(*e))
            if sp.closed:
                qp.closeSubpath()
    return qp


class PreviewView(QGraphicsView):
    mode_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#2a2c31")))
        self.mode = ViewMode.WIREFRAME
        self.show_nodes = True
        self.show_open_ends = True
        self.show_grid = True
        self.show_tiny = True
        self.split = 0.0          # 0..1 fraction of width showing original in vector modes
        self._rgb: np.ndarray | None = None
        self._binary: np.ndarray | None = None
        self._graph: PathGraph | None = None
        self._ppm = 1.0
        self._origin_px = (0.0, 0.0)
        self._tiny_ids: set[str] = set()
        self._min_feature = 0.1
        self._fitted = False
        self._height: np.ndarray | None = None    # depth jobs: conditioned height (float, 1 = deep)
        self._height8: np.ndarray | None = None   # depth jobs: conditioned height as 8-bit (black = deep)

    # ---------------------------------------------------------------- data
    def set_source(self, rgb: np.ndarray | None) -> None:
        self._rgb = rgb
        self._binary = None
        self._graph = None
        self._height = None
        self._height8 = None
        self._fitted = False
        self.redraw()

    def set_depth(self, height: np.ndarray | None) -> None:
        """Height field of a depth job (float 0..1, 1 = deep); None for flat jobs."""
        self._height = height
        if height is None:
            self._height8 = None
        else:
            self._height8 = np.clip(np.round((1.0 - height) * 255.0), 0, 255).astype(np.uint8)

    @staticmethod
    def depth_color(index: int, levels: int) -> QColor:
        """Slice colour ramp for the simulation: shallow = light grey, deep = near black."""
        t = index / max(1, levels)
        v = int(round(215 - 195 * t))
        return QColor(v, v, v)

    @staticmethod
    def depth_wire_color(index: int) -> QColor:
        return QColor.fromHsvF(((index - 1) * 137.5 % 360.0) / 360.0, 0.85, 0.8)

    def set_result(self, binary: np.ndarray, graph: PathGraph, ppm: float, tiny_ids: set[str], min_feature: float) -> None:
        self._binary = binary
        self._graph = graph
        self._ppm = ppm
        # the graph is framed on the relief footprint for a depth job and on the
        # ink bbox for a flat one; the rasters are full frame, so line them up
        # against the same region the pipeline used
        frame = self._height > 0 if self._height is not None else binary
        ys, xs = np.nonzero(frame)
        self._origin_px = (float(xs.min()) - 0.5, float(ys.min()) - 0.5) if xs.size else (0.0, 0.0)
        self._tiny_ids = tiny_ids
        self._min_feature = min_feature
        self.redraw()
        if not self._fitted:
            self.fit()
            self._fitted = True
            self.redraw()  # node/endpoint markers are sized from the zoom level

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._graph is not None and self.mode == ViewMode.WIREFRAME:
            self.redraw()

    def set_mode(self, mode: int) -> None:
        self.mode = ViewMode(mode)
        self.mode_changed.emit(int(self.mode))
        self.redraw()

    def fit(self) -> None:
        r = self.scene().itemsBoundingRect()
        if not r.isEmpty():
            self.fitInView(r.adjusted(-2, -2, 2, 2), Qt.AspectRatioMode.KeepAspectRatio)

    # ---------------------------------------------------------------- drawing
    def _raster_item(self, arr: np.ndarray, opacity: float = 1.0, clip_frac: float | None = None):
        pix = QPixmap.fromImage(np_to_qimage(arr))
        item = self.scene().addPixmap(pix)
        s = 1.0 / self._ppm
        item.setScale(s)
        item.setPos(-self._origin_px[0] * s, -self._origin_px[1] * s)
        item.setOpacity(opacity)
        item.setZValue(-10)
        return item

    def redraw(self) -> None:
        sc = self.scene()
        sc.clear()
        if self._rgb is None:
            t = sc.addText("Paste (Ctrl+V), drop, or open an image")
            t.setDefaultTextColor(QColor("#8a8f99"))
            return
        if self._binary is None or self._graph is None:
            self._raster_item(self._rgb)
            sc.setSceneRect(sc.itemsBoundingRect())
            return

        g = self._graph
        w, h = g.width, g.height
        if self.mode == ViewMode.ORIGINAL:
            self._raster_item(self._rgb)
        elif self.mode == ViewMode.BINARY:
            self._raster_item(self._height8 if self._height8 is not None else 255 - self._binary)
        else:
            # white plate background
            sc.addRect(QRectF(0, 0, w, h), QPen(Qt.PenStyle.NoPen), QBrush(QColor("#f4f4f4"))).setZValue(-20)
            if self.split > 0:
                self._raster_item(self._rgb, 0.9)
            levels = g.depth_levels()
            if self.mode == ViewMode.SIMULATION:
                fills = graph_to_qpath(g, Layer.ENGRAVE_FILL, flat_only=True)
                sc.addPath(fills, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#111111")))
                # depth slices: shallow first, each deeper slice painted darker on top
                for i in range(1, levels + 1):
                    qp = graph_to_qpath(g, Layer.ENGRAVE_FILL, depth_index=i)
                    if not qp.isEmpty():
                        sc.addPath(qp, QPen(Qt.PenStyle.NoPen), QBrush(self.depth_color(i, levels)))
                for p in g.paths:
                    if p.layer == Layer.ENGRAVE_LINE and p.depth_index is None:
                        pen = QPen(QColor("#111111"), max(0.02, p.stroke_width_mm or 0.1))
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        sc.addPath(graph_to_qpath(PathGraph(paths=[p])), pen)
                cut = graph_to_qpath(g, Layer.CUT)
                pen = QPen(QColor("#ff2020"), 0)
                sc.addPath(cut, pen)
                score = graph_to_qpath(g, Layer.SCORE)
                sc.addPath(score, QPen(QColor("#2060ff"), 0))
            else:  # wireframe
                if self.split > 0:
                    pass
                else:
                    self._raster_item(self._rgb, 0.18)
                colors = {Layer.ENGRAVE_FILL: "#1a73e8", Layer.ENGRAVE_LINE: "#12a150", Layer.CUT: "#ff2020", Layer.SCORE: "#2060ff", Layer.IGNORE: "#808080"}
                for layer, col in colors.items():
                    qp = graph_to_qpath(g, layer, flat_only=True)
                    if not qp.isEmpty():
                        sc.addPath(qp, QPen(QColor(col), 0))
                for i in range(1, levels + 1):
                    qp = graph_to_qpath(g, Layer.ENGRAVE_FILL, depth_index=i)
                    if not qp.isEmpty():
                        sc.addPath(qp, QPen(self.depth_wire_color(i), 0))
                    hq = graph_to_qpath(g, Layer.ENGRAVE_LINE, depth_index=i)
                    if not hq.isEmpty():
                        c = self.depth_wire_color(i); c.setAlphaF(0.35)
                        sc.addPath(hq, QPen(c, 0))
                if self.show_nodes:
                    r = 0.9 / max(self.transform().m11(), 1e-6)
                    pen = QPen(Qt.PenStyle.NoPen)
                    brush = QBrush(QColor("#ff8a1f"))
                    for p in g.paths:
                        if p.depth_index is not None and p.layer == Layer.ENGRAVE_LINE:
                            continue  # hatch lines: thousands of nodes, not useful
                        for sp in p.subpaths:
                            for seg in sp.segments:
                                x, y = seg.p1 if isinstance(seg, Line) else (seg.p0 if isinstance(seg, Cubic) else seg.p_start)
                                sc.addEllipse(x - r, y - r, 2 * r, 2 * r, pen, brush)
                if self.show_open_ends:
                    r = 2.2 / max(self.transform().m11(), 1e-6)
                    for p in g.paths:
                        for sp in p.subpaths:
                            if not sp.closed and p.layer != Layer.ENGRAVE_LINE:
                                for x, y in (sp.start(), sp.end()):
                                    sc.addEllipse(x - r, y - r, 2 * r, 2 * r, QPen(QColor("#ff3030"), 0), QBrush(Qt.BrushStyle.NoBrush))
                if self.show_tiny and self._tiny_ids:
                    for p in g.paths:
                        if p.id in self._tiny_ids:
                            qp = graph_to_qpath(PathGraph(paths=[p]))
                            b = qp.boundingRect().adjusted(-self._min_feature, -self._min_feature, self._min_feature, self._min_feature)
                            sc.addRect(b, QPen(QColor("#ff40a0"), 0))
            if self.split > 0:
                # split slider: reveal original on the left part
                cover = QRectF(0, 0, w * self.split, h)
                self._raster_item(self._rgb, 1.0).setZValue(50)
                mask = sc.addRect(QRectF(w * self.split, 0, w * (1 - self.split), h), QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0, 0)))
                mask.setZValue(60)
                # crude but effective: overlay a white rect on the right to hide original there
                hide = sc.addRect(QRectF(w * self.split, 0, w * (1 - self.split) + 0.01, h), QPen(Qt.PenStyle.NoPen), QBrush(QColor("#f4f4f4")))
                hide.setZValue(51)
                # redraw vectors above the hide layer
                qp = graph_to_qpath(g, Layer.ENGRAVE_FILL)
                it = sc.addPath(qp, QPen(QColor("#1a73e8"), 0) if self.mode == ViewMode.WIREFRAME else QPen(Qt.PenStyle.NoPen), QBrush(QColor("#111111")) if self.mode == ViewMode.SIMULATION else QBrush(Qt.BrushStyle.NoBrush))
                it.setZValue(70)
                line = sc.addLine(w * self.split, 0, w * self.split, h, QPen(QColor("#ff8a1f"), 0))
                line.setZValue(80)
            if self.show_grid:
                step = 1.0 if max(w, h) <= 30 else (5.0 if max(w, h) <= 150 else 10.0)
                pen = QPen(QColor(0, 0, 0, 40), 0)
                x = 0.0
                while x <= w + 1e-9:
                    sc.addLine(x, 0, x, h, pen).setZValue(90)
                    x += step
                y = 0.0
                while y <= h + 1e-9:
                    sc.addLine(0, y, w, y, pen).setZValue(90)
                    y += step
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-w * 0.05 - 1, -h * 0.05 - 1, w * 0.05 + 1, h * 0.05 + 1))

    # ---------------------------------------------------------------- interaction
    def wheelEvent(self, ev) -> None:
        f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        if self.mode in (ViewMode.WIREFRAME,) and self.show_nodes:
            self.redraw()

    def mouseDoubleClickEvent(self, ev) -> None:
        self.fit()
