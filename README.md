# LaserTrace Pro

<img src="lasertrace_ui/assets/icon_128.png" align="right" width="96" alt="LaserTrace Pro icon">

**Paste a messy logo. Get a DXF that EZCAD fills cleanly.**

LaserTrace Pro is a black-and-white image vectorizer built for fiber laser
marking shops (EZCAD2/3, JCZ galvo controllers, LightBurn). It is not a drawing
program and it does not do colour. Its only job is turning artwork that arrives
by email, screenshot, or phone photo into vector files a galvo head marks
cleanly: few nodes, true centerlines for line art, no overlapping fills, holes
preserved, millimetre units, layers for engrave / cut / score.

## Why not Illustrator Image Trace or Vector Magic

| Problem on the laser | LaserTrace Pro |
|---|---|
| 400-node "potato" circles, slow EZCAD import | Straight runs become lines, curves become a handful of Beziers. A circle is ~20 nodes at 50 mm, a rounded rectangle is 4 lines + 4 arcs. |
| Thin strokes traced as hollow outline pairs | **Centerline engine**: skeleton + junction-aware graph walk. One path per stroke, with the measured stroke width. **Hybrid** mode sends fills to outline and thin strokes to centerline automatically. |
| Overlapping shapes double-burn | Planar union of every same-layer fill. Zero overlap area is a quality gate. |
| Counters in A, B, O, 8 fill in | Holes are taken straight from the raster topology and re-checked against the binary: the app warns if one was lost. |
| Speckle, JPEG blocks, gray halos become junk vectors | Non-destructive preprocess stack: despeckle in mm², halo knockout, bilateral, Sauvola with a global guard, deskew. |
| "Paths 50%, Corners 60%" | Controls in shop units: **Minimum feature (mm)**, **Despeckle (mm²)**, **Darkness threshold**, **Detail vs cleanliness**, **Corner sharpness**, **Smoothness**. |
| SVG in px that EZCAD mangles | DXF R2000/R12 in mm with `$INSUNITS=4`, Y-up, closed LWPOLYLINEs, layers `ENGRAVE_FILL / ENGRAVE_LINE / CUT / SCORE`. Also plain SVG, HPGL/PLT, PDF, 1-bit PNG. |
| No idea what will fail on the machine | Live stats (paths, nodes, open paths, holes, fill area, size, complexity) and operator warnings: unclosed fills, holes lost, outline pairs suspected, tiny paths, node budget exceeded. |
| Grayscale "3D" engraving that only changes colour | **Depth engraving**: a height map (or a photo turned into a bas-relief) is sliced into N *cumulative* passes (pass i marks everything deeper than i/N, the way LightBurn 3D Sliced and EZCAD3 work), with a draft angle on every wall, rotating hatch angle, Z steps and loop counts from a per-material removal table. Exports one DXF layer per slice, one DXF per slice for EZCAD2, a full-range height-map PNG at the hatch pitch for LightBurn / EZCAD3, and a pass plan. See [docs/DEPTH_ENGRAVING.md](docs/DEPTH_ENGRAVING.md). |

## Quick start

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements-ui.txt
.\.venv\Scripts\pip install -e .

# generate the fixture set (35 synthetic images) and run the tests
.\.venv\Scripts\python tools\make_fixtures.py
.\.venv\Scripts\python -m pytest

# desktop app
.\.venv\Scripts\python -m lasertrace_ui.main
```

Desktop workflow: **Ctrl+V** to paste, the app auto-picks a preset, tweak the
six sliders, check the four views (**1** original, **2** binary, **3** vectors,
**4** laser simulation), press **E** to export a DXF at the width you set
(default 50 mm). **Space** toggles the original, **[ ]** nudge the threshold,
**Enter** re-traces, **Ctrl+Z/Y** undo/redo.

**Help lives in the app.** Press **F1** (or the *? Help* button on the view
bar) for a searchable handbook: what the app is for, what every control does,
how depth engraving works, and step-by-step quick guides for the jobs the shop
actually runs. Every panel has a small **?** that opens its own topic. The
content is in [lasertrace_ui/help_content.py](lasertrace_ui/help_content.py).

## Headless CLI

```powershell
lasertrace logo.png --preset logo-fill --width-mm 50 --out logo.dxf
lasertrace logo.png --out logo.dxf --out logo.svg --stats logo.json         # auto-classify preset
lasertrace sketch.jpg --preset thin-line-art --out sketch.dxf                # centerline
lasertrace tag.png --preset cut-outer-engrave-inner --out tag.dxf            # CUT silhouette + ENGRAVE
lasertrace qr.png --preset qr --width-mm 20 --out qr.dxf                     # square modules, no smoothing
lasertrace --batch .\incoming --out-dir .\vectors --preset logo-fill --format dxf,svg
lasertrace --list-presets
lasertrace photo.jpg --classify

# depth (3D relief): height map -> DEPTH_01..NN layers + depth pack (per-slice DXFs, height PNG, pass plan)
lasertrace relief.png --preset auto-depth --width-mm 40 --out relief.dxf --depth-pack .\relief_pack
lasertrace coin.png --preset depth-coin --depth-mm 0.3 --material brass --laser-w 60 --removal-um 12 --depth-pack .\coin
lasertrace face.jpg --preset depth-photo-relief --out face_heightmap.png     # LightBurn 3D Sliced input
```

Every option in the UI has a CLI flag (`--detail`, `--smoothness`, `--corner`,
`--min-feature-mm`, `--despeckle-mm2`, `--threshold`, `--node-budget`,
`--engine`, `--mode`, `--dxf-version`, `--curves`, `--origin`). Same image +
same preset = byte-identical geometry, so batch jobs are repeatable.

## Presets (shop names)

**AUTO modes** measure the image and derive the settings: `auto` (detect
everything), `auto-bw` (art that is already black & white, or white on black),
`auto-photo` (photos/scans, tuned from measured noise), `auto-lines` (line
art). They are the default when nothing is chosen; the derived sliders stay
editable and the reasons are shown under the preset box.

Static presets: `logo-fill`, `thin-line-art`, `stamp-stencil`, `photo-to-plate`, `small-text`,
`qr-datamatrix`, `dirty-phone-photo`, `cut-outer-engrave-inner`, `tiny-logo`, and the depth
presets `depth-relief`, `depth-coin`, `depth-photo-relief` (plus `auto-depth`).
Each is a JSON file in `presets/` you can copy and edit; user presets live in
`~/.lasertrace/presets/`. See [docs/PRESETS.md](docs/PRESETS.md).

## Regression harness

```powershell
.\.venv\Scripts\python tools\regress.py
```

Traces all 38 fixtures (35 flat + 3 height maps), writes `reports/metrics.csv`
and red/blue overlays in `reports/overlays/`, and fails if any preset exceeds
3x its hand-set node target, loses a hole, leaves a fill open, drops below 85%
fidelity, or produces overlapping fills. Depth fixtures are gated on slice
count, nesting (slice i+1 inside slice i) and closed paths instead.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data flow, coordinate systems, file formats
- [docs/LASER_REQUIREMENTS.md](docs/LASER_REQUIREMENTS.md): EZCAD / LightBurn constraints, layer colours, DXF rules
- [docs/TRACE_STRATEGY.md](docs/TRACE_STRATEGY.md): outline vs centerline vs hybrid, algorithm notes
- [docs/PRESETS.md](docs/PRESETS.md): every preset and its parameters
- [docs/TECH_STACK.md](docs/TECH_STACK.md): stack and license audit (Potrace is GPL and runs as an isolated sidecar)
- [docs/EZCAD_VERIFY.md](docs/EZCAD_VERIFY.md): how to verify an export in EZCAD
- [docs/DEPTH_ENGRAVING.md](docs/DEPTH_ENGRAVING.md): depth (3D relief) engraving: the physics, the research, the workflow in LightBurn / EZCAD2 / EZCAD3

The in-app help (**F1**) is the operator-facing summary of all of these.

## Status

Vertical slice complete: paste/open → preprocess → contour or centerline or
Potrace-sidecar trace → hygiene → 4 views → DXF/SVG/PLT/PDF/PNG export, with a
CLI, batch mode, 38-fixture regression suite, and a PySide6 desktop shell.
Depth (3D relief) engraving: height map / photo → cumulative slices → layered
DXF + height-map PNG + pass plan.
Geometry snap (circles/arcs/H-V-45° lines) and the vtracer engine are the next
milestones (see docs/TRACE_STRATEGY.md "Roadmap").

## License

**MIT** - free to use, modify and sell, commercially or otherwise. See
[LICENSE](LICENSE).

Two deliberate constraints keep it that way:

- `sidecars/potrace_sidecar/` is **GPL-3**, so Potrace is invoked as a separate
  process and is never imported into the core. Everything works without it.
- PyMuPDF (AGPL) is not used anywhere; PDF input goes through pypdfium2.

See [NOTICE](NOTICE) for third-party terms and
[docs/TECH_STACK.md](docs/TECH_STACK.md) for the full dependency licence audit.

## Contributing

Issues and pull requests are welcome.

- Keep every pipeline stage a pure function with a test.
- `pytest` must stay green. `tests/test_fixtures_quality.py` is the slow one
  (it traces the whole fixture set and checks node counts, hole counts and
  fidelity against `fixtures/targets.json`); the rest run in a couple of
  minutes.
- Quality is measured, not asserted by eye: if you change the tracer, run
  `python tools/regress.py` and say what moved.
