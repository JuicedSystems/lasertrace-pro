"""In-app help: the topic library renders, every cross-link resolves, and the
'?' buttons in the panels open the topic they promise."""
import os
import re
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lasertrace_ui.help_content import (CONTEXT_HELP, QUICK_GUIDES, SECTIONS, TOPICS, TOPICS_BY_ID, render,
                                        search, to_html)

_HREF = re.compile(r'href="topic:([a-z0-9-]+)"')
_TAG = re.compile(r"<[^>]+>")
_CODE = re.compile(r"<code[^>]*>.*?</code>", re.S)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_topic_ids_are_unique_and_sectioned():
    assert len(TOPICS_BY_ID) == len(TOPICS)
    assert {t.section for t in TOPICS} <= set(SECTIONS)
    for section in SECTIONS:
        assert any(t.section == section for t in TOPICS), f"empty section {section}"


def test_every_cross_link_resolves():
    broken = [(t.id, link) for t in TOPICS for link in _HREF.findall(render(t)) if link not in TOPICS_BY_ID]
    assert broken == []


def test_menu_and_button_topics_exist():
    assert all(t in TOPICS_BY_ID for t in QUICK_GUIDES)
    assert all(t in TOPICS_BY_ID for t in CONTEXT_HELP.values())


def test_markdown_subset_is_fully_rendered():
    """No topic should leak raw markup into the visible text.

    Code spans are dropped first: `slices/*.dxf` is a real path, not an italic.
    """
    for t in TOPICS:
        text = _TAG.sub("", _CODE.sub("", render(t)))
        assert "**" not in text, f"unrendered bold in {t.id}"
        assert not re.search(r"\*\S", text), f"unrendered italic in {t.id}"
        assert "](topic:" not in text, f"unrendered link in {t.id}"
        assert not re.search(r"^\s*\|", text, re.M), f"unrendered table in {t.id}"


def test_renderer_handles_each_block():
    html = to_html("## Head\n\nA *para*.\n\n- one\n- two\n\n1. first\n2. second\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> a note\n")
    for tag in ("<h2", "<p>", "<i>", "<ul>", "<ol>", "<table"):
        assert tag in html
    assert html.count("<li>") == 4


def test_search_narrows_and_matches_body_text():
    assert len(search("")) == len(TOPICS)
    assert TOPICS_BY_ID["depth-calibrate"] in search("removal per pass")
    assert search("zzzznotatopic") == []


def test_panel_help_buttons_open_their_topic(app):
    from PySide6.QtWidgets import QToolButton
    from lasertrace_ui.app import MainWindow

    win = MainWindow()
    try:
        opened = []
        for panel in (win.left, win.right):
            for b in panel.findChildren(QToolButton):
                if b.objectName() == "help":
                    b.click()
                    opened.append(win._help._current)
        assert set(opened) == set(CONTEXT_HELP.values())
    finally:
        win.runner.shutdown()


def test_help_window_walks_every_topic(app):
    from lasertrace_ui.widgets.help import HelpDialog

    dlg = HelpDialog()
    for t in TOPICS:
        dlg.show_topic(t.id)
        assert dlg._current == t.id
        assert dlg.view.toPlainText().strip()
    dlg.go_back()
    assert dlg._current == TOPICS[-2].id
    dlg.go_forward()
    assert dlg._current == TOPICS[-1].id
    dlg.search.setText("depth")
    assert dlg.tree.topLevelItem(0) is not None
