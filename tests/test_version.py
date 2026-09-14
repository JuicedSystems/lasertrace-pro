"""Every place that shows the version agrees, and the release has notes."""
from __future__ import annotations

import re
from pathlib import Path

from lasertrace import __version__

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_version_is_the_same_everywhere():
    found = {
        "pyproject.toml": re.findall(r'^version = "([^"]+)"', _read("pyproject.toml"), re.M),
        "About text": re.findall(r"\*\*LaserTrace Pro (\S+)\*\*", _read("lasertrace_ui/help_content.py")),
        "macOS Info.plist": re.findall(r'"CFBundle(?:ShortVersionString|Version)": "([^"]+)"',
                                       _read("packaging/lasertrace.spec")),
    }
    for where, versions in found.items():
        assert versions and set(versions) == {__version__}, f"{where} says {versions}, package says {__version__}"


def test_changelog_has_a_section_for_this_version():
    # the release job copies this section into the GitHub release notes
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", _read("CHANGELOG.md"), re.M)
