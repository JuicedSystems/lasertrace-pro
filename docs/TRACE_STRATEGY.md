# Trace strategy

## Choosing a mode

| Artwork | Mode | Why |
|---|---|---|
| Filled logo, silhouette, stamp, stencil, bold text | **outline** (`contour` engine) | Regions are what the laser fills. Boundaries carry all the information. |
| Line drawing, sketch, script lettering, schematic, thin-stroke icon | **centerline** | A stroke is one marking pass, not two boundaries around a tube. |
| Logo with filled shapes *and* thin rules/strokes | **hybrid** | Fills -> outline, strokes -> centerline, split by local stroke width (`centerline_max_width_mm`). |
| QR / Data Matrix / barcodes | outline with `smoothness = 0`, `corner_sharpness = 1` | Modules must stay square; the tracer emits axis-aligned polylines only. |

The classifier (`preprocess/classify.py`) picks a preset from cheap signals:
gray-fringe ratio (anti-aliased vs 1-bit), background flatness (photo/scan vs
digital), median stroke width relative to image size (line art), component and
hole counts (text), a module-grid periodicity score (QR), and resolution
(tiny). The operator can always override.

## Outline engine (`engines/contour.py`)

**Principle: fewer, better nodes.** Three ideas do most of the work.

1. **Exact pixel-edge planar polygons first.** OpenCV contours run through
   pixel centres, skip the corner pixel on diagonal steps, and self-touch at
   diagonal contacts. The engine inserts the missing ink corner on every
   diagonal step (rectilinear contour), then buffers by 0.5 px with mitre joins
   *before* any validation. Result: exact raster area, square QR modules, and
   a union that is a true planar map (no overlaps, no gaps, no lost holes).
2. **Corners and straight runs are detected, not fitted.** Corner detection
   uses the turning angle over an arc-length window at least 6 px long so
   staircase noise does not read as corners. Straight runs come from an RDP
   pass at >= 1.5 px (above the staircase): a chord qualifies when it is at
   least 2.5x the piece's median chord. On a circle every chord is about the
   same length and none qualifies; on a rounded rectangle the four edges
   qualify. Each run's line is least-squares fitted through the dense pixel
   points, so an axis-aligned edge lands exactly on the pixel edge. The corner
   arcs are fitted with end tangents matching the lines. That is why a rounded
   rectangle is 4 lines + 4 cubics and a circle is 4 cubics.
3. **Fit on the stair-midpoint polyline, validate the result.** The polyline
   through the midpoints of the rectilinear edges lies on the true boundary
   (RDP on a staircase is biased inward and distorts chord-length
   parametrisation). Schneider's least-squares cubic fit runs on that, with
   Newton reparametrisation before splitting, tangents estimated over a chord
   of ~4 tolerances, and handle lengths bounded by the piece's arc length (the
   classic Schneider failure is a near-straight piece whose normal equations go
   singular and produce control points kilometres away while still passing the
   sample-point error test). Every finished ring is checked with shapely; an
   invalid ring is refitted at 0.6x and 0.35x tolerance, then falls back to a
   polyline.

Tolerances are floored at the source pixel size (an upscaled image does not
carry more information than its source), which keeps node counts sane on
low-res logos without hurting sharp ones.

## Centerline engine (`engines/centerline.py`)

1. Light 3x3 close to bridge 1 px breaks.
2. Zhang-Suen thinning (scikit-image `skeletonize`).
3. Pixel graph: nodes are skeleton pixels with degree != 2 (endpoints and
   junctions). Walk each branch; at a junction prefer the neighbour that
   continues straight, so crossings become two through-strokes rather than
   four stubs.
4. Prune spurs shorter than `2 * min_feature_mm` that end in a free endpoint
   (thinning artefacts at stroke ends and corners).
5. Join branches that meet at a degree-2 point after pruning, and close loops
   whose ends meet within `gap_close_mm`.
6. Stroke width = 2x the distance-transform value along the branch (median),
   reported on the path. Strokes wider than `centerline_max_width_mm` raise
   `WIDE_STROKE_AS_CENTERLINE`.
7. RDP + cubic fit in mm, output `ENGRAVE_LINE` open/closed paths.

Known limits (roadmap): skeletons of thick strokes are offset at T-junctions;
variable-width strokes are reported with one median width.

## Hybrid (`pipeline._trace_hybrid`)

Morphological opening with a disk of diameter `centerline_max_width_mm`
keeps thick regions (grown back to their true edge and intersected with the
ink); everything else is the thin mask. Thick -> contour, thin -> centerline.
Both graphs are shifted into a shared frame.

## Potrace sidecar

Potrace is excellent on logos and is available as engine `potrace` for
comparison. Slider mapping: `despeckle_mm2 -> turdsize`, `corner_sharpness ->
alphamax` (1 - 0.9*s), `smoothness -> opttolerance`. It typically produces
2-3x the nodes of the contour engine on geometric logos because it fits every
edge as a curve (no straight-run detection) and has no mm-aware simplification.

## Hygiene (`hygiene/`)

Order in the pipeline: winding -> gap close -> chain join (lines) -> micro
removal (paths and holes below `min_feature^2`) -> dedupe -> overlap union with
refit (no-op for a single mask; matters when layers are merged) -> collinear
merge -> CUT silhouette (if `cut_outer`) -> rounding.

## Warnings the operator sees

| Code | Trigger | Meaning on the machine |
|---|---|---|
| `EMPTY` | no ink | wrong threshold / needs invert |
| `UNCLOSED_PATHS` | fill subpath not closed | hatch will leak / be skipped |
| `HOLES_LOST` | fewer holes than the binary (outline mode) | counters will fill in |
| `TINY_PATHS` | path smaller than `min_feature_mm` | will blob |
| `OUTLINE_PAIR_SUSPECT` | thin strokes traced in outline mode | hollow tubes; use Thin Line Art |
| `LOW_FIDELITY` | vector fill matches binary < 90% | detail lost; raise Detail |
| `HIGH_COMPLEXITY` | > ~120 nodes/cm² | slow import, ringing |
| `NODE_BUDGET_EXCEEDED` | cap not reachable | loosen tolerance or accept |
| `WIDE_STROKE_AS_CENTERLINE` | centerline on wide strokes | fill instead |
| `ENGINE_FALLBACK` | engine unavailable / failed | contour used |

## Roadmap

* **Geometry snap** (`geometry/snap.py`): recognise circles/arcs (least-squares
  circle fit on cubic chains with low residual), H/V/45° lines (angle snap),
  rounded rectangles (line+arc pattern), and repeated shapes (hash of
  normalised rings). Emit `Arc` segments and export as DXF `ARC`/bulge.
* **vtracer** engine (MIT, in-process) for shared-boundary colour-quantised
  regions.
* **Strokes to fills**: offset `ENGRAVE_LINE` by `stroke_width_mm` (shapely
  buffer) for EZCAD fill mode.
* **Start-point optimisation** (`hygiene/startpoint.py` exists; wire a UI
  toggle) and nearest-neighbour ordering per layer.
* **Text protection**: detect glyph-like components and lock `min_feature`
  lower for their holes.
