"""Check a PyInstaller bundle before shipping it.

    python tools/verify_bundle.py dist/LaserTracePro
    python tools/verify_bundle.py "dist/LaserTrace Pro.app"

A frozen app hits the same trap the wheel did: data loaded by path is simply
absent unless it was explicitly bundled, and the app then fails at runtime on
the user's machine rather than on ours. So this checks the payload is present
and then actually launches the thing offscreen and traces an image.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

FAIL: list[str] = []


def check(label: str, fn):
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001
        FAIL.append(label)
        print(f"  FAIL  {label}\n          {type(e).__name__}: {e}")
    else:
        print(f"  ok    {label}" + (f"  ({detail})" if detail else ""))


def resolve(target: Path) -> tuple[Path, Path]:
    """Return (payload dir holding the bundled data, executable to run)."""
    if target.suffix == ".app":
        macos = target / "Contents" / "MacOS"
        exe = next(p for p in macos.iterdir() if p.is_file() and os.access(p, os.X_OK))
        res = target / "Contents" / "Resources"
        payload = res if (res / "lasertrace").is_dir() else macos / "_internal"
        if not payload.is_dir():
            payload = macos
        return payload, exe
    inner = target / "_internal"
    payload = inner if inner.is_dir() else target
    exes = [p for p in target.iterdir() if p.suffix.lower() in (".exe", "") and p.is_file()]
    exe = next((p for p in exes if p.stem.lower().startswith("lasertrace")), exes[0])
    return payload, exe


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).resolve()
    if not target.exists():
        print(f"no such bundle: {target}")
        return 2

    payload, exe = resolve(target)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"LaserTrace Pro bundle check\n  bundle  {target}\n  exe     {exe.name}\n  size    {size / 1e6:.0f} MB\n")

    def presets():
        d = payload / "lasertrace" / "preset_data"
        files = sorted(d.glob("*.json")) if d.is_dir() else []
        assert len(files) >= 12, f"expected >=12 presets in {d}, found {len(files)}"
        return f"{len(files)} json"

    def icons():
        d = payload / "lasertrace_ui" / "assets"
        assert (d / "icon.ico").exists() or (d / "icon.png").exists(), f"no icon in {d}"
        return "present"

    def no_gpl():
        hits = [p for p in payload.rglob("*") if "potrace" in p.name.lower()]
        assert not hits, f"GPL sidecar leaked into the bundle: {hits}"
        return "no potrace"

    def runs():
        """Launch the real binary offscreen and make it trace something."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "probe.dxf"
            src = Path(d) / "probe.png"
            _write_probe_png(src)
            env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                   "LASERTRACE_SELFTEST": str(out), "LASERTRACE_SELFTEST_SRC": str(src)}
            r = subprocess.run([str(exe), "--selftest"], env=env, capture_output=True,
                               text=True, timeout=300)
            if r.returncode != 0:
                raise AssertionError(f"exit {r.returncode}\nstdout: {r.stdout[-1500:]}\nstderr: {r.stderr[-1500:]}")
            assert out.exists() and out.stat().st_size > 200, f"no DXF written\n{r.stdout[-800:]}"
            return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else f"{out.stat().st_size} bytes"

    check("built-in presets bundled", presets)
    check("window icon bundled", icons)
    check("no GPL sidecar in the bundle", no_gpl)
    check("app launches and traces an image", runs)

    print()
    if FAIL:
        print(f"{len(FAIL)} check(s) FAILED")
        return 1
    print("bundle is shippable")
    return 0


def _write_probe_png(path: Path) -> None:
    """A ring: one path, one hole - written without importing PIL."""
    import struct
    import zlib
    n = 200
    rows = bytearray()
    for y in range(n):
        rows.append(0)
        for x in range(n):
            dx, dy = x - n / 2, y - n / 2
            r = (dx * dx + dy * dy) ** 0.5
            rows.append(0 if 40 < r < 80 else 255)
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows)))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


if __name__ == "__main__":
    raise SystemExit(main())
