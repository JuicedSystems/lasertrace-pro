from __future__ import annotations

from .base import Engine
from .contour import ContourEngine
from .potrace_client import PotraceEngine
from .centerline import CenterlineEngine

_ENGINES: dict[str, Engine] = {
    "contour": ContourEngine(),
    "potrace": PotraceEngine(),
    "centerline": CenterlineEngine(),
}


def get_engine(name: str) -> Engine:
    try:
        return _ENGINES[name]
    except KeyError:
        raise ValueError(f"unknown engine {name!r}; available: {', '.join(_ENGINES)}")


def available_engines() -> dict[str, bool]:
    return {k: v.available() for k, v in _ENGINES.items()}
