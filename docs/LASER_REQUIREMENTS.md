# Laser requirements (EZCAD2 / EZCAD3 / LightBurn)

What the machine side actually needs, and how LaserTrace Pro satisfies it.

## Units and scale

* Fiber galvo work is specified in millimetres. Every export is in mm. DXF
  sets `$INSUNITS = 4` (mm) and `$MEASUREMENT = 1` so EZCAD and LightBurn do
  not apply an inch guess.
* Source DPI is only used to *estimate* physical size. The operator sets the
  target width (default 50 mm). Height follows the aspect ratio unless both are
  given.
* EZCAD2 ignores DXF units in some builds and assumes mm: exporting in mm is
  therefore mandatory, and the file's extents equal the requested size so the
  operator can sanity-check the bounding box in the EZCAD property panel.

## Coordinate system

* DXF is Y-up. Working space is Y-down (image convention). The DXF exporter
  flips `y' = H - y` and puts the origin at bottom-left by default so the
  artwork lands in the positive quadrant. `center` origin is available for
  shops that mark around the galvo centre.
* SVG stays Y-down (its native convention); LightBurn handles it.

## Path rules that keep fills clean

| Rule | Reason | Implementation |
|---|---|---|
| Closed polylines for every fill region | EZCAD hatch fill only fills closed entities; an open contour leaks or is silently skipped | `close_fills` forces closure on export; `UNCLOSED_PATHS` is an error-level warning |
| One closed ring per boundary, holes as separate closed rings on the same layer | EZCAD and LightBurn use even-odd (or nesting) to leave counters open | `Subpath.is_hole`, exported as separate LWPOLYLINE/POLYLINE |
| No overlapping fills on the same layer | Overlap = area marked twice = deeper burn, discolouration | `union_fills` planar union; `detect_overlaps` gate in the regression |
| No duplicate paths | Same as above, and doubles galvo time | `dedupe_subpaths` |
| No micro segments / micro paths | Galvo ringing, EZCAD import lag, file bloat | `merge_collinear`, `min_feature_mm` removal, node budget |
| Consistent winding | Some importers derive fill direction from winding | outer positive, holes negative (`ensure_winding`) |
| Simple entities (LWPOLYLINE / POLYLINE) by default | EZCAD2 splines and ellipses import inconsistently | `curves: polyline` default, `spline` optional for LightBurn/EZCAD3 |
| Flatten tolerance in mm | Curve approximation must be below the spot size (~0.03-0.05 mm on fiber) | `flatten_tol_mm` default 0.02 |
| Hairline for CUT | LightBurn/EZCAD treat thin red strokes as cut lines | `cut_hairline_mm` = 0.01 |
| No embedded raster | Keeps DXF small and avoids EZCAD bitmap objects | never embedded; a companion 1-bit PNG export exists for raster engraving |
| No text objects | Fonts do not travel | text is always outlined geometry |

## Layer conventions

| Layer | Meaning | DXF ACI | SVG colour | PLT pen |
|---|---|---|---|---|
| `ENGRAVE_FILL` | filled regions to hatch/fill | 7 (white/black) | `#000000` fill | SP1 |
| `ENGRAVE_LINE` | single-pass line (centerline output) | 3 (green) | `#00A000` stroke | SP2 |
| `CUT` | through-cut / silhouette | 1 (red) | `#FF0000` stroke 0.01 mm | SP3 |
| `SCORE` | light scoring | 5 (blue) | `#0000FF` stroke | SP4 |
| `IGNORE` | construction / reference | 8 (gray) | `#808080` stroke | SP5 |

Colours are per-`ExportProfile` and editable. `flatten_layers` merges all into
`ENGRAVE` (depth slices are never flattened).

### Depth (3D relief) layers

| Layer | Meaning | DXF ACI | SVG colour |
|---|---|---|---|
| `DEPTH_01` .. `DEPTH_NN` | cumulative slice fills; run in order, pass *i* re-marks everything deeper than *i* / N | distinct palette entry per slice (1,2,3,4,5,6,30,40,...) | hue ramp (137.5 deg steps) |
| `DEPTH_nn_HATCH` | optional pre-generated hatch lines + inset contour for slice nn | palette entry 15 steps on | same hue, stroke = pitch |
| `IGNORE` frame | alignment rectangle in every per-slice file | 8 | gray |

LightBurn ignores DXF layer *names* and keys on entity colour (30 palette
slots), so slice colours must be distinct; EZCAD2 ignores layers entirely
(use the per-slice files); EZCAD3 honours the names. The height-map PNG is
written full-range, black = deepest, white = untouched, at DPI = 25.4 /
hatch pitch, 8-bit (EZCAD) or 16-bit (LightBurn 2.1+, > 256 passes).

## EZCAD-specific notes

* EZCAD2 DXF import: R12 and R2000 both work; R2000 with LWPOLYLINE is the
  default. Use `--dxf-version R12` for very old builds (POLYLINE entities).
* EZCAD hatch: select all imported objects, enable Hatch, choose line spacing
  ~0.03-0.05 mm for fiber. Nested rings are recognised as holes when the
  objects are grouped or hatched together.
* EZCAD does not like thousands of tiny objects: the `HIGH_COMPLEXITY`
  warning fires at 60/100 complexity (roughly 120 nodes/cm²).
* EZCAD3 handles splines better; `--curves spline` produces exact cubic
  B-splines from the fitted Beziers.
* Very small text: EZCAD renders fills by hatch lines, so any feature narrower
  than 2x the line spacing disappears. `min_feature_mm` (default 0.1) and the
  `TINY_PATHS` warning are calibrated for a 0.03-0.05 mm hatch.

## LightBurn notes

* SVG import maps stroke/fill colour to layers; the exported colours are
  chosen to land on distinct LightBurn layers.
* LightBurn reads DXF layers by name; `ENGRAVE_FILL` etc. show up in the cut
  list.
* `Copy SVG` in the app puts plain SVG text on the clipboard; LightBurn and
  Inkscape paste it directly.

## Centerline vs fill on metal

* A 0.3 mm stroke traced as an outline pair marks as a hollow tube (two hairlines).
  Traced as a centerline it marks as one clean line. The `OUTLINE_PAIR_SUSPECT`
  warning fires when the median stroke width in the binary is below
  `centerline_max_width_mm` (default 0.6) and the trace mode is outline.
* `ENGRAVE_LINE` paths carry `stroke_width_mm` so the operator can set the
  laser line width or expand to a fill (`strokes_to_fills`, roadmap).
