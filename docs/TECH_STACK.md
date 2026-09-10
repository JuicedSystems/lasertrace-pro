# Tech stack and license audit

## Decision

**Python 3.11 core + PySide6 desktop shell**, packaged with the CLI.

Why not Tauri/Rust first: the whole CV stack (OpenCV, scikit-image, shapely,
ezdxf) is Python-native and mature, so the complete pipeline, tests and
regression harness shipped in one pass. The core has no Qt dependency and is
driven by JSON models, so a Tauri/React shell can later host it as a sidecar
process, and the numeric hotspots (`geometry/bezier.py`, `geometry/polygons.py`)
are pure functions that can be ported to Rust one at a time without touching
the rest.

## Packages

| Package | License | Role | How it ships |
|---|---|---|---|
| numpy | BSD-3 | arrays | in-process |
| opencv-python-headless | Apache-2.0 | contours, morphology, filters, raster | in-process |
| scikit-image | BSD-3 | skeletonize (centerline) | in-process, optional (centerline engine reports unavailable without it) |
| scipy | BSD-3 | scikit-image dependency | in-process |
| shapely (GEOS) | BSD-3 / LGPL-2.1 (GEOS, dynamically linked) | planar polygons, union, validity | in-process |
| Pillow | MIT-CMU | image IO, EXIF, PNG export | in-process |
| ezdxf | MIT | DXF R12/R2000 writer | in-process |
| pydantic | MIT | models, JSON | in-process |
| pypdfium2 (PDFium) | Apache-2.0 / BSD-3 | PDF first page | in-process |
| PySide6 (Qt 6) | LGPL-3 | desktop UI only | in-process, dynamically linked; LGPL obligations satisfied by shipping unmodified Qt binaries and allowing relinking |
| pytest | MIT | tests | dev only |
| **potracer** (Potrace port) | **GPL-3** | engine `potrace` | **separate process** `sidecars/potrace_sidecar/sidecar.py`; the core never imports it; PBM in / JSON out over pipes |
| vtracer (planned) | MIT | engine `vtracer` | in-process, optional |
| AutoTrace (considered) | GPL | centerline fallback | would go in a sidecar like Potrace; not needed now |
| **PyMuPDF** | **AGPL-3** | (not used) | rejected; pypdfium2 chosen instead |
| cairosvg (optional) | LGPL-3 | SVG input rasterisation | optional import; absent = clear error message |

## GPL isolation rationale

Potrace is the strongest classical logo tracer and worth having for
side-by-side comparison, but its GPL would extend to any program that links
it. LaserTrace Pro therefore treats it as an external tool:

* Lives in its own directory with its own `LICENSE` (GPL-3) and `README`.
* Communicates only via stdin/stdout with a documented data format (PBM in,
  JSON curves out). This is the "separate programs communicating at arm's
  length" arrangement the FSF describes as not forming a combined work.
* The application is complete without it: `PotraceEngine.available()` returns
  false and the pipeline falls back to the MIT `contour` engine with an
  `ENGINE_FALLBACK` info warning.
* It can be swapped for the native `potrace.exe` binary with the same
  protocol, or removed from a proprietary build entirely.

If the product is ever sold as closed source, ship the sidecar as an optional
separately-downloaded add-on with its source, or drop it.

## Packaging (next step)

* `pip install -e .` provides `lasertrace` (CLI) and `lasertrace-ui`.
* For a Windows installer: PyInstaller one-folder build of `lasertrace_ui.main`
  with `opencv-python-headless`, exclude the sidecar from the main bundle and
  ship it as `sidecar/` beside the exe with `LASERTRACE_SIDECAR_PYTHON`
  pointing at a bundled interpreter, or simply omit it.
