#!/usr/bin/env python
"""Potrace sidecar (GPL-3). Runs as a SEPARATE PROCESS so the GPL engine
does not link into the MIT-licensed LaserTrace core.

Protocol:
  stdin : PBM (P4) bitmap, 1 = ink
  argv  : --turdsize N --alphamax F --opttolerance F [--check]
  stdout: JSON {"curves": [{"sign": "+"|"-", "segments": [...]}]}
          segment: {"type":"line","p1":[x,y],"p2":[x,y]} or
                   {"type":"cubic","p0":..,"c1":..,"c2":..,"p3":..}
Coordinates are pixel units, y-down, matching the input bitmap.
"""
import argparse
import json
import sys


def _read_pbm(data: bytes):
    import numpy as np
    assert data[:2] == b"P4", "expected binary PBM"
    # header tokens
    idx = 2
    tokens = []
    while len(tokens) < 2:
        while data[idx : idx + 1].isspace():
            idx += 1
        if data[idx : idx + 1] == b"#":
            while data[idx : idx + 1] not in (b"\n", b""):
                idx += 1
            continue
        start = idx
        while not data[idx : idx + 1].isspace():
            idx += 1
        tokens.append(int(data[start:idx]))
    idx += 1  # single whitespace after height
    w, h = tokens
    row_bytes = (w + 7) // 8
    raw = np.frombuffer(data[idx : idx + row_bytes * h], dtype=np.uint8).reshape(h, row_bytes)
    bits = np.unpackbits(raw, axis=1)[:, :w]
    return bits.astype(bool)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turdsize", type=int, default=2)
    ap.add_argument("--alphamax", type=float, default=1.0)
    ap.add_argument("--opttolerance", type=float, default=0.2)
    ap.add_argument("--turnpolicy", default="minority")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    try:
        import potrace  # potracer (pure Python, GPL)
    except ImportError:
        sys.stderr.write("potracer not installed: pip install potracer\n")
        return 2
    if a.check:
        return 0

    bits = _read_pbm(sys.stdin.buffer.read())
    bmp = potrace.Bitmap(bits)

    def _tp(name: str):
        # potracer exports POTRACE_TURNPOLICY_*, pypotrace exports TURNPOLICY_*
        return getattr(potrace, f"POTRACE_TURNPOLICY_{name}", getattr(potrace, f"TURNPOLICY_{name}", 4))

    tp = _tp(a.turnpolicy.upper()) if a.turnpolicy.upper() in ("BLACK", "WHITE", "LEFT", "RIGHT", "MINORITY", "MAJORITY") else _tp("MINORITY")
    path = bmp.trace(turdsize=a.turdsize, turnpolicy=tp, alphamax=a.alphamax, opticurve=True, opttolerance=a.opttolerance)

    curves = []
    for curve in path:
        start = curve.start_point
        segs = []
        prev = (float(start.x), float(start.y)) if hasattr(start, "x") else (float(start[0]), float(start[1]))
        for seg in curve:
            end = seg.end_point
            end_t = (float(end.x), float(end.y)) if hasattr(end, "x") else (float(end[0]), float(end[1]))
            if seg.is_corner:
                c = seg.c
                c_t = (float(c.x), float(c.y)) if hasattr(c, "x") else (float(c[0]), float(c[1]))
                segs.append({"type": "line", "p1": prev, "p2": c_t})
                segs.append({"type": "line", "p1": c_t, "p2": end_t})
            else:
                c1, c2 = seg.c1, seg.c2
                c1_t = (float(c1.x), float(c1.y)) if hasattr(c1, "x") else (float(c1[0]), float(c1[1]))
                c2_t = (float(c2.x), float(c2.y)) if hasattr(c2, "x") else (float(c2[0]), float(c2[1]))
                segs.append({"type": "cubic", "p0": prev, "c1": c1_t, "c2": c2_t, "p3": end_t})
            prev = end_t
        # potracer exposes curve orientation via signed area of the polygon it traced
        sign = "+"
        try:
            pts = [(float(p[0]), float(p[1])) if not hasattr(p, "x") else (float(p.x), float(p.y)) for p in [curve.start_point] + [s.end_point for s in curve]]
            area = 0.0
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                area += x1 * y2 - x2 * y1
            # potrace: outer curves are traced with the ink on the left (positive area in y-up),
            # which is negative in y-down pixel space.
            sign = "+" if area < 0 else "-"
        except Exception:
            pass
        curves.append({"sign": sign, "segments": segs})
    sys.stdout.write(json.dumps({"curves": curves}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
