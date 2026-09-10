# Verifying an export in EZCAD / LightBurn

Automated checks already run on every export (`tests/test_export_and_cli.py`):
DXF parses with ezdxf, `$INSUNITS == 4`, every fill entity is a closed
LWPOLYLINE/POLYLINE on the expected layer, extents match the requested width,
Y is flipped to Y-up with the artwork in the positive quadrant, SVG is plain
paths with an mm viewBox. The remaining checks need the real software.

## EZCAD2 (2.14.x)

1. `File > Import Vector File`, choose the DXF. Leave "Unit: mm" (or set it if
   the dialog asks).
2. Select the imported object and read **Size X / Size Y** in the property
   bar. It must equal the width you exported (e.g. 50.000) and the height shown
   in the LaserTrace stats panel.
3. With the object selected, tick **Hatch** (`Draw > Hatch` or the hatch
   button), enable *Hatch 1*, line distance 0.04 mm, angle 45°, and press OK.
   Expected: solid shapes fill, counters in letters (A, B, O, 8) stay empty,
   no region shows double density.
4. Zoom into a circle: it should look round with no visible facets at 400%.
   If it facets, re-export with `--curves spline` (EZCAD3) or a smaller
   `flatten_tol_mm` (0.01).
5. `View > Mark preview` / red-light preview: the outline should trace once
   per boundary. Two passes over the same edge indicate overlap; re-export with
   `union_fills` on (default) and check the warnings panel for
   `OVERLAPS_UNIONED`.
6. Centerline exports (`ENGRAVE_LINE` layer): do **not** hatch; set the pen to
   the line parameters. The stroke width reported in the SVG
   (`data-stroke-mm`) tells you how thick the original art was.
7. CUT layer (red, ACI 1): assign to a separate pen with cut parameters. It is
   the exterior silhouette only, no holes.

## EZCAD3

Same as above; EZCAD3 imports SPLINE entities correctly, so
`--curves spline` gives exact curves with fewer vertices.

## LightBurn

1. `File > Import`, choose the DXF or SVG. For DXF set units to mm in the
   import dialog if prompted.
2. The layers appear in the Cuts/Layers panel by name (DXF) or by colour (SVG).
   `ENGRAVE_FILL` -> mode *Fill*; `ENGRAVE_LINE` -> *Line*; `CUT` -> *Line* with
   cut power.
3. `Edit > Select all`, check the size in the toolbar (W/H in mm).
4. Preview (Alt+P): fills should render solid with counters open; the
   simulation should show no doubled traversals.
5. Clipboard round-trip: in LaserTrace press **Copy SVG**, then in LightBurn
   `Edit > Paste`. The artwork should land at the same size.

## Depth packs

1. **Height map** (`*_heightmap.png`): open in any viewer, the untouched
   surface must be pure white (255), the deepest point pure black, no
   visible banding at 400%. LightBurn: Image mode 3D Sliced, Negative Image
   off, Number of Passes = the plan's raster passes; the preview should show
   the red area shrinking pass by pass.
2. **Multi-layer DXF**: LightBurn shows one layer per slice colour in the
   cut list (DEPTH_01 first). EZCAD3 lists the layer names. Select all and
   check the size against the plan.
3. **Per-slice DXFs** (EZCAD2): import DEPTH_01 and DEPTH_NN together; the
   IGNORE frames coincide exactly and the deep slice sits inside the
   shallow one. Hatch DEPTH_01 with Auto rotate hatch 37 deg and Loop count
   = loops per slice; the red-light preview must stay inside the shallow
   outline.
4. **Plan** (`*_plan.md`): Z offsets are monotonic and never exceed the
   total depth; loops per slice x removal per pass ~ slice thickness.

## What "good" looks like (quality bar)

* Filled 25-80 mm logo: crisp at 400% zoom, fills once, counters open.
* Thin illustration: single lines, not hollow tubes.
* Circle: round, about 20 nodes at 50 mm; rounded rect: 4 lines + 4 arcs.
* Small text: readable, counters open, no blobs.
* Dirty phone photo: usable with the Dirty Phone Photo preset and the
  threshold slider alone.
* Node count far below Illustrator's default trace on the same binary (use the
  app's **Compare engines** dialog to see the Illustrator-like and Vector
  Magic-like presets side by side).
