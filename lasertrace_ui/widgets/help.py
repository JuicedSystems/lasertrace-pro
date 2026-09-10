"""The Help window: searchable topic tree on the left, rendered topic on the right."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QTextBrowser,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..help_content import SECTIONS, TOPICS, TOPICS_BY_ID, render

_ID_ROLE = Qt.ItemDataRole.UserRole


def help_button(topic: str, panel) -> QToolButton:
    """A small '?' that asks `panel` (a widget with a `help_requested` signal) for a topic."""
    b = QToolButton()
    b.setText("?")
    b.setObjectName("help")
    b.setAutoRaise(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(f"Help: {TOPICS_BY_ID[topic].title}")
    b.clicked.connect(lambda: panel.help_requested.emit(topic))
    return b


def help_corner(topic: str, panel, *before) -> QWidget:
    """A row that pins a '?' to the right edge of a group box.

    Any widgets passed in `before` are laid out on the left of the same row,
    so a group whose first control is a checkbox keeps them side by side.
    """
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for widget in before:
        lay.addWidget(widget)
    lay.addStretch()
    lay.addWidget(help_button(topic, panel))
    return w


class HelpDialog(QDialog):
    """Non-modal, so it can sit beside the app while you work."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LaserTrace Pro - Help")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(1000, 700)
        self._history: list[str] = []
        self._forward: list[str] = []
        self._current: str | None = None

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.back = QToolButton(); self.back.setText("<"); self.back.setToolTip("Back"); self.back.clicked.connect(self.go_back)
        self.fwd = QToolButton(); self.fwd.setText(">"); self.fwd.setToolTip("Forward"); self.fwd.clicked.connect(self.go_forward)
        top.addWidget(self.back); top.addWidget(self.fwd)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help  (depth, threshold, counters, ezcad, ...)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        outer.addLayout(top)

        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(240)
        self.tree.currentItemChanged.connect(self._tree_changed)
        split.addWidget(self.tree)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._anchor)
        split.addWidget(self.view)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 740])
        outer.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.hint = QLabel("Press F1 anywhere in the app to reopen this window.")
        self.hint.setObjectName("info")
        bottom.addWidget(self.hint)
        bottom.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.close)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        for keys, fn in (("Ctrl+F", self.search.setFocus), ("Alt+Left", self.go_back), ("Alt+Right", self.go_forward)):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)

        self._build_tree()
        self._update_nav()

    # ------------------------------------------------------------------ tree
    def _build_tree(self) -> None:
        self.tree.clear()
        self._items: dict[str, QTreeWidgetItem] = {}
        for section in SECTIONS:
            topics = [t for t in TOPICS if t.section == section]
            if not topics:
                continue
            parent = QTreeWidgetItem(self.tree, [section])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = parent.font(0); f.setBold(True); parent.setFont(0, f)
            for t in topics:
                it = QTreeWidgetItem(parent, [t.title])
                it.setData(0, _ID_ROLE, t.id)
                it.setToolTip(0, t.summary)
                self._items[t.id] = it
        self.tree.expandAll()

    def _filter(self, query: str) -> None:
        q = query.strip()
        hits = 0
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            shown = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                topic = TOPICS_BY_ID[child.data(0, _ID_ROLE)]
                ok = topic.matches(q)
                child.setHidden(not ok)
                shown += int(ok)
            parent.setHidden(shown == 0)
            hits += shown
        self.tree.expandAll()
        if q:
            self.hint.setText(f"{hits} topic(s) match “{q}”." if hits else f"Nothing matches “{q}”.")
        else:
            self.hint.setText("Press F1 anywhere in the app to reopen this window.")

    def _tree_changed(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        tid = item.data(0, _ID_ROLE)
        if tid and tid != self._current:
            self.show_topic(tid, record=True)

    # ------------------------------------------------------------------ navigation
    def show_topic(self, topic_id: str, record: bool = True) -> None:
        topic = TOPICS_BY_ID.get(topic_id)
        if topic is None:
            return
        if record and self._current and self._current != topic_id:
            self._history.append(self._current)
            self._forward.clear()
        self._current = topic_id
        self.view.setHtml(render(topic))
        self.view.verticalScrollBar().setValue(0)
        it = self._items.get(topic_id)
        if it is not None and self.tree.currentItem() is not it:
            self.tree.blockSignals(True)
            it.setHidden(False)
            it.parent().setHidden(False)
            self.tree.setCurrentItem(it)
            self.tree.blockSignals(False)
        self._update_nav()

    def go_back(self) -> None:
        if self._history:
            if self._current:
                self._forward.append(self._current)
            self.show_topic(self._history.pop(), record=False)

    def go_forward(self) -> None:
        if self._forward:
            if self._current:
                self._history.append(self._current)
            self.show_topic(self._forward.pop(), record=False)

    def _update_nav(self) -> None:
        self.back.setEnabled(bool(self._history))
        self.fwd.setEnabled(bool(self._forward))

    def _anchor(self, url: QUrl) -> None:
        s = url.toString()
        if s.startswith("topic:"):
            self.show_topic(s[len("topic:"):])
        elif s.startswith(("http:", "https:")):
            QDesktopServices.openUrl(url)

    # ------------------------------------------------------------------ show
    def pop_up(self, topic_id: str | None = None) -> None:
        if topic_id:
            self.show_topic(topic_id)
        elif self._current is None:
            self.show_topic("overview")
        self.show()
        self.raise_()
        self.activateWindow()
