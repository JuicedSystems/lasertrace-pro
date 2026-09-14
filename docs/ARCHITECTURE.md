# Architecture

## Shape of the system

```
                  lasertrace_ui (PySide6)            lasertrace.cli (argparse)
                          |                                   |
                          +-------------- lasertrace.pipeline --------------+
                                                   |
   ingest ─> preprocess stack ─> engine ─> hygiene ─> stats/warnings ─> export
   (PIL,     (numpy/OpenCV,      (contour |  (shapely   (numpy,          (ezdxf,
   pypdfium2) skimage-free)      centerline| numpy)     OpenCV raster)   plain SVG,
                                  potrace                                 HPGL, PDF,
                                  sidecar)                                1-bit PNG)
```

* `lasertrace/` is a pure library: no Qt, no global state, every stage is a
  function `(array or model, settings) -> model`. Tests exercise each stage
  alone.
* `lasertrace_ui/` only marshals between widgets and `Job` models and runs the
  pipeline on a worker thread.
* `sidecars/potrace_sidecar/` is a separate program (GPL). The core talks to
  it over stdin/stdout.

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | Pydantic models: `SourceImage`, `PreprocessOp/Stack`, `TraceSettings`, `Line/Cubic/Arc/Subpath/Path/PathGraph`, `Stats`, `Warning`, `TraceResult`, `ExportProfile`, `Preset`, `Job`. |
| `units.py` | `Transform` (affine), px<->mm helpers. |
| `ingest.py` | File/bytes/clipboard/PDF page 1 -> RGB uint8 + `SourceImage` (EXIF applied; 16/32-bit images scaled to 8-bit from one fixed range per mode - integers 0..65535, floats 0..1 - never from the image's own values; transparency flattened so the art is dark ink - one-colour art on a transparent background uses alpha as the ink, light flat-colour art with no dark detail is inverted, everything else (dark outlines or text, photos) composites on white; `bit_depth` and `alpha_policy` record what happened). |
| `preprocess/threshold.py` | manual, Otsu(+bias), adaptive, Sauvola, Niblack, hysteresis. Local methods carry a global Otsu guard so large flat regions do not fragment. |
| `preprocess/morphology.py` | open/close/dilate/erode (px or mm), despeckle by area, hole fill by area, halo knockout, hole/component counting, stroke-width stats. |
| `preprocess/enhance.py` | crop/rotate/deskew, background flatten, upscale, denoise, bilateral, unsharp, levels/gamma, quantize. |
| `preprocess/ops.py` | Registry + stack runner. Ops are grouped into colour / threshold / binary stages; the runner keeps stage order and inserts Otsu if the user forgot a threshold. |
| `preprocess/classify.py` | Heuristic classifier -> suggested preset. |
| `geometry/bezier.py` | RDP, collinear merge, corner detection (arc-length window), Schneider cubic fit with reparametrisation and bounded handles, flattening. |
| `geometry/polygons.py` | OpenCV contours -> pixel-edge-accurate shapely polygons (buffer 0.5 px, mitre) -> planar union; PathGraph <-> polygons; rasterize (4x supersampled) and IoU. |
| `engines/contour.py` | Default permissive engine. Straight-run detection + corner detection + cubic fit between them + ring validity retry. |
| `engines/centerline.py` | Zhang-Suen thinning -> graph walk (straight-through at junctions) -> spur prune -> chain join -> RDP -> fit -> `ENGRAVE_LINE` paths with stroke width. |
| `engines/potrace_client.py` | Spawns the sidecar with PBM on stdin, reads JSON curves. Reports `available()` false if potracer is missing. |
| `hygiene/` | winding, gap close, micro-path removal, chain join, dedupe, overlap union (refit), cut-outer silhouette, collinear merge, scaling. |
| `stats.py` | Stats + operator warnings (`build_depth_warnings` for depth jobs). |
| `pipeline.py` | `run(rgb, job)`: px/mm estimate -> stack -> slider despeckle -> engine (with node-budget loop and fallback) -> hygiene -> stats. Also `trace_binary` for pre-binarized inputs, `_trace_hybrid`, and `run_depth` (dispatched when `job.depth.enabled`). |
| `depth/heightmap.py` | gray -> float height 0..1 (1 = deep): orientation, photo-to-relief (gradient compression + DCT Poisson), bilateral smoothing, zero plane, floor, normalise, equalise, gamma, feather. |
| `depth/slicer.py` | height -> N cumulative masks (threshold (i-1/2)/N, island/pit removal, sub-pixel draft-angle inset, nesting) -> contour-engine trace per slice -> `PathGraph` with `Path.depth_index`. |
| `depth/hatch.py` | per-slice hatch lines (rotate polygon, one shapely intersection, serpentine order) + inset contour pass as `ENGRAVE_LINE` paths with `depth_index`. |
| `depth/plan.py`, `depth/materials.py` | pass plan (`DepthReport`: depth from/to, Z offset, loops, angle, area, time) from the material removal table. |
| `export/` | `svg.py`, `dxf.py`, `plt.py`, `pdf.py`, `png.py`; `export()` dispatches by extension and scales to the profile size. `depth.py`: depth pack (multi-layer DXF, per-slice DXFs with alignment frame, height-map PNG at the hatch pitch, shaded preview, plan md/csv/json). |
| `presets.py` | Load/list/save presets (built-in dir + `~/.lasertrace/presets`). |
| `cli.py` | Headless entry point, batch mode with CSV log. |

## Data flow and units

1. **Pixel space** (ingest, preprocess): uint8 arrays, y-down, origin top-left.
   Binary masks use **255 = ink**.
2. **px/mm** is fixed *before* tracing from `export.width_mm` and the ink
   bounding box (`px_per_mm = ink_width_px / width_mm`), so every tolerance in
   `TraceSettings` is physical (mm). Preprocess ops that take `radius_mm` or
   `min_area_mm2` use a pass-1 estimate from an Otsu bbox.
3. **Working space** (engine output onward): millimetres, y-down, origin at the
   top-left of the traced bounding box. `PathGraph.bbox = (0, 0, W, H)`.
   Winding: outer rings have **positive** shoelace area, holes negative.
   `Path.fill_rule = evenodd`.
4. **Export space**: SVG keeps working space (`viewBox 0 0 W H`, units mm).
   DXF applies `y' = H - y` (Y-up) and the origin choice (bottom-left,
   top-left, center). PLT is Y-up in 0.025 mm units. PDF is Y-up in points.

Upscaled inputs (`upscale` op) keep tolerances relative to the *source* pixel:
`EngineContext.source_scale` = binary px / source px.

## Tolerance derivation (`engines/base.py`)

| Slider | Effect |
|---|---|
| `detail` | `rdp_tol = curve_tol_mm * (0.5 + (1-detail)*1.5)`, floored at 0.6 source px |
| `smoothness` | `fit_tol = rdp_tol * (0.75 + smoothness*1.5)`, floored at (0.8 + 0.7*smoothness) px. `0` = pure polylines. |
| `corner_sharpness` | corner angle threshold `25 + (1-s)*75` degrees, measured over an arc-length window `max(2*min_feature, 4*rdp_tol, 6 px)` |
| `min_feature_mm` | drop paths/holes below `min_feature^2` area or `min_feature` length; flags tiny paths |
| `despeckle_mm2` | connected-component area filter applied after the stack |
| `node_budget` | pipeline retraces with tolerance x1.6 up to 5 times |

## Depth (3D relief) flow

```
rgb -> colour-stage ops -> gray -> conditioned height (0..1, 1 = deep)
    -> N nested masks (h >= (i-1/2)/N, cleaned, draft inset) -> contour trace per slice
    -> Path.depth_index = i, layer ENGRAVE_FILL (+ ENGRAVE_LINE hatch) -> DepthReport
```

Every slice shares one frame (footprint bbox of slice 1 at 0,0). Slices
overlap by design (cumulative passes), so `detect_overlaps` only compares
paths with the same `depth_index`, `union_overlapping_fills` is not run, and
stats skip IoU. Export layer names come from `Path.layer_name()`:
`DEPTH_nn` / `DEPTH_nn_HATCH`, colours from `depth_layer_aci` (distinct ACI
per slice because LightBurn keys DXF layers on colour, not name).
`transform_graph` / `scale_graph` carry `depth_index` through. See
docs/DEPTH_ENGRAVING.md.

## Contour engine, step by step

1. `findContours(RETR_CCOMP, CHAIN_APPROX_NONE)` on the padded mask -> outer +
   hole contours through pixel centres.
2. Make each contour **rectilinear**: for every diagonal step insert the
   corner pixel that is ink (OpenCV skips it at concave corners and along
   diagonals). Build `Polygon(outer, holes)` and **buffer by 0.5 px with mitre
   joins** *before* validation. Result: the exact pixel-edge boundary (square
   QR modules, exact raster area), and self-touching diagonal contacts resolved
   without losing enclosed holes (`tests/test_geometry.py`).
3. `unary_union` of all regions = planar map with zero overlap. Sort by area
   for determinism.
4. Per ring (mm): corners on the dense ring (arc-length window >= 6 px);
   split into pieces. Per piece detect **straight runs**: RDP at >= 1.5 px (above
   the staircase) and keep chords >= 2.5x the piece's median chord and longer
   than `max(1.5*corner_window, 8*fit_tol)`. Each run becomes a `Line` whose
   endpoints are least-squares snapped to the dense pixel points (so an
   axis-aligned edge lands exactly on the pixel edge). The curved stretches
   between runs are **midpoint-smoothed** (the polyline through the stair-edge
   midpoints, which lie on the true boundary) and fitted with cubics whose end
   tangents match the adjoining lines. A circle is 4 cubics; a rounded
   rectangle is 4 lines + 4 arcs.
5. Validate the ring (shapely `is_valid`); retry at 0.6x and 0.35x tolerance;
   fall back to an RDP polyline. `smoothness = 0` skips fitting entirely and
   emits RDP polylines (QR).
6. Hygiene: winding, gap close, micro removal, dedupe, overlap union (should be
   a no-op for a single mask), collinear merge, optional CUT silhouette,
   round to 4 decimals.

## File formats produced

* **DXF**: `$INSUNITS=4`, `$MEASUREMENT=1`, layers with ACI colours
  (ENGRAVE_FILL 7, ENGRAVE_LINE 3, CUT 1, SCORE 5, IGNORE 8), closed
  `LWPOLYLINE` (R2000) or `POLYLINE` (R12) per ring; optional `SPLINE` from
  the exact cubic Beziers (R2000). Job JSON embedded in `$CUSTOMPROPERTY`
  (first 255 chars) when `embed_preset` is on.
* **SVG**: `width/height` in mm, `viewBox` in mm, `<g id="LAYER">` per layer,
  `<path d>` only, `fill-rule="evenodd"`, hairline `stroke-width` 0.01 for CUT,
  job JSON in `<desc>`.
* **PLT**: HPGL `IN;PA;SP<n>;PU x,y;PD x,y,...;` 40 units/mm, pen per layer.
* **PDF**: single page, media box = artwork size in points, even-odd fills.
* **PNG**: 1-bit, DPI tag, 1 mm margin.

## Determinism

No randomness (k-means quantize uses a fixed seed and `KMEANS_PP_CENTERS`).
Polygons are sorted by area then bounds. Coordinates rounded to 4 decimals.
`tests/test_hygiene_and_pipeline.py::test_pipeline_deterministic` and the CLI
test compare two runs.

## Extending

* New preprocess op: add a pure function, register it in
  `preprocess/ops.py` (`OP_REGISTRY`, `OP_STAGE`, `OP_LABELS`), add the name to
  `PreprocessOpName` in `models.py`.
* New engine: implement `Engine` protocol (`name`, `available()`, `trace(mask,
  ctx) -> PathGraph` in mm with bbox at 0,0), register in
  `engines/registry.py`, add to the `TraceSettings.engine` literal.
* New exporter: add `export/<fmt>.py` and a branch in `export/__init__.py`.
