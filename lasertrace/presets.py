from __future__ import annotations

import json
from pathlib import Path

from .models import Preset

#: Built-in presets ship *inside* the package, so they survive `pip install`.
#: (They used to live at the repo root, which meant a wheel had none of them.)
PRESET_DIR = Path(__file__).resolve().parent / "preset_data"
USER_PRESET_DIR = Path.home() / ".lasertrace" / "presets"

ALIASES = {
    "logo": "logo-fill", "line": "thin-line-art", "lines": "thin-line-art", "stencil": "stamp-stencil",
    "stamp": "stamp-stencil", "photo": "dirty-phone-photo", "plate": "photo-to-plate", "text": "small-text",
    "qr": "qr-datamatrix", "cut": "cut-outer-engrave-inner", "tiny": "tiny-logo",
    "depth": "depth-relief", "relief": "depth-relief", "coin": "depth-coin", "3d": "depth-relief",
}


def preset_dirs() -> list[Path]:
    dirs = [PRESET_DIR]
    if USER_PRESET_DIR.exists():
        dirs.append(USER_PRESET_DIR)
    return dirs


def list_presets() -> list[Preset]:
    out: dict[str, Preset] = {}
    for d in preset_dirs():
        for f in sorted(d.glob("*.json")):
            try:
                p = Preset.model_validate_json(f.read_text(encoding="utf-8"))
                out[p.name] = p
            except Exception as e:  # pragma: no cover
                raise ValueError(f"bad preset {f}: {e}") from e
    return list(out.values())


def load_preset(name: str) -> Preset:
    name = ALIASES.get(name, name)
    for d in reversed(preset_dirs()):
        f = d / f"{name}.json"
        if f.exists():
            return Preset.model_validate_json(f.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"preset {name!r} not found in {[str(d) for d in preset_dirs()]}")


def save_preset(preset: Preset, directory: Path | None = None) -> Path:
    d = directory or USER_PRESET_DIR
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{preset.name}.json"
    f.write_text(json.dumps(preset.model_dump(mode="json"), indent=2), encoding="utf-8")
    return f
