"""Client for the GPL-isolated Potrace sidecar.

The core never imports potrace. It writes a PBM to the sidecar process over
stdin and reads JSON paths (pixel units) from stdout. If the sidecar or its
dependency is missing the engine reports unavailable and the pipeline falls
back to the contour engine with a warning.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path as FsPath

import numpy as np

from ..models import Cubic, Layer, Line, Path, PathGraph, Subpath
from ..geometry.bezier import signed_area
from .base import EngineContext

SIDECAR = FsPath(__file__).resolve().parents[2] / "sidecars" / "potrace_sidecar" / "sidecar.py"


def _sidecar_python() -> str:
    return os.environ.get("LASERTRACE_SIDECAR_PYTHON", sys.executable)


def mask_to_pbm(mask: np.ndarray) -> bytes:
    h, w = mask.shape
    bits = np.packbits((mask > 0).astype(np.uint8), axis=1)
    return b"P4\n%d %d\n" % (w, h) + bits.tobytes()


class PotraceEngine:
    name = "potrace"
    _avail: bool | None = None

    def available(self) -> bool:
        if self._avail is None:
            if not SIDECAR.exists():
                self._avail = False
            else:
                try:
                    r = subprocess.run([_sidecar_python(), str(SIDECAR), "--check"], capture_output=True, timeout=30)
                    self._avail = r.returncode == 0
                except Exception:
                    self._avail = False
        return self._avail

    def trace(self, mask: np.ndarray, ctx: EngineContext) -> PathGraph:
        s = ctx.settings
        ppm = ctx.px_per_mm
        t0 = time.perf_counter()
        # potrace params from shop sliders
        turdsize = int(max(0, round(s.despeckle_mm2 * ppm * ppm)))
        alphamax = float(1.0 - s.corner_sharpness * 0.9)  # 1.0 = smooth everything; ~0.1 = keep corners
        opttolerance = float(0.05 + s.smoothness * 0.6)
        args = [_sidecar_python(), str(SIDECAR), "--turdsize", str(turdsize), "--alphamax", str(alphamax), "--opttolerance", str(opttolerance)]
        r = subprocess.run(args, input=mask_to_pbm(mask), capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"potrace sidecar failed: {r.stderr.decode(errors='replace')[:400]}")
        data = json.loads(r.stdout.decode())
        ctx.timings_ms["potrace_sidecar"] = (time.perf_counter() - t0) * 1000

        # bbox of ink for origin
        ys, xs = np.nonzero(mask)
        if xs.size == 0:
            return PathGraph(paths=[], units="mm", bbox=(0, 0, 0, 0), px_per_mm=ppm)
        minx, miny = float(xs.min()), float(ys.min())
        maxx, maxy = float(xs.max() + 1), float(ys.max() + 1)

        def mm(p):
            return ((p[0] - minx) / ppm, (p[1] - miny) / ppm)

        paths: list[Path] = []
        current: Path | None = None
        for curve in data["curves"]:
            segs: list = []
            for seg in curve["segments"]:
                if seg["type"] == "line":
                    segs.append(Line(p1=mm(seg["p1"]), p2=mm(seg["p2"])))
                else:
                    segs.append(Cubic(p0=mm(seg["p0"]), c1=mm(seg["c1"]), c2=mm(seg["c2"]), p3=mm(seg["p3"])))
            if not segs:
                continue
            # potrace signs: '+' outer, '-' hole
            is_hole = curve.get("sign", "+") == "-"
            sp = Subpath(segments=segs, closed=True, is_hole=is_hole)
            if not is_hole:
                current = Path(subpaths=[sp], layer=Layer.ENGRAVE_FILL, fill_rule="evenodd")
                paths.append(current)
            elif current is not None:
                current.subpaths.append(sp)
        bbox = (0.0, 0.0, (maxx - minx) / ppm, (maxy - miny) / ppm)
        return PathGraph(paths=paths, units="mm", bbox=bbox, px_per_mm=ppm)
