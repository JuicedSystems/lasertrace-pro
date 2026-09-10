# Presets

## AUTO modes (dynamic)

AUTO modes are not JSON files: `lasertrace/auto.py` measures the pasted
image and derives a concrete job (the sliders show the derived values and can
still be tweaked). The decisions are written to `Job.notes` and shown under
the preset box / printed by the CLI. Measured signals: already 1-bit
(midtone fraction, background noise), white-on-black (solid ink border),
noise std of the paper, stroke width median / 90th percentile / widest solid
region, speck count, module-grid score, pixel size.

| Mode | Base | What it decides |
|---|---|---|
| `auto` **AUTO (detect everything)** | classifier's preset | everything below, plus outline/centerline/hybrid for logos and line art |
| `auto-bw` **AUTO Black & White** | logo-fill (small-text for text) | 1-bit source -> manual 50% threshold, no denoise/bilateral/halo, corner sharpness >= 0.8. Anti-aliased -> Otsu + halo knockout. Mode from stroke widths. |
| `auto-photo` **AUTO Photo / Scan** | dirty-phone-photo | Sauvola window = min(w,h)/12 px; denoise off if noise < 4, else strength = noise (6..15); centerline if everything is thin |
| `auto-lines` **AUTO Line Art** | thin-line-art | centerline, or hybrid when solid regions exist; centerline max width = 2x median stroke |
| `auto-depth` **AUTO Depth (3D relief)** | depth-relief / depth-photo-relief | border tone -> which colour is the untouched surface (black = deep vs white = deep); texture + edge density -> photo (photo-to-relief on, heavier smoothing) vs height map; distinct tones -> 4..30 slices; relief filling the frame -> zero plane = lightest tone |

Common to all: invert when the border is solid ink; despeckle 0.1 mm² above
30 specks, 0.3 mm² above 200; upscale 2x/4x below 300/150 px; minimum feature
raised to 2.5 (upscaled) px, capped at 0.5 mm; module grid -> polylines only,
corner sharpness 1, morphology off.

Mode rule (mm, `cl` = centerline max width, default 0.6): widest solid region
< 1.5 cl and p90 < cl -> **centerline**; median < cl and widest >= 1.5 cl ->
**hybrid**; else **outline**.

## Static presets

Each preset is a JSON file in `lasertrace/preset_data/` (inside the package, so it survives `pip install`) (`Preset` model: `stack`, `trace`,
`export`). Copy one to `~/.lasertrace/presets/<name>.json` to customise; user
presets override built-ins with the same name. The desktop app's **Save
preset** button writes there.

Common defaults unless stated: `engine contour`, `mode outline`,
`union_fills true`, `preserve_holes true`, export DXF R2000, 50 mm wide,
origin bottom-left, polyline curves, flatten tolerance 0.02 mm.

| Preset | Stack (enabled ops in order) | Trace sliders | Notes |
|---|---|---|---|
| **Logo Fill Mark** `logo-fill` | background_white(30) -> threshold otsu -> knockout_halo(2 px, >200) -> despeckle 0.02 mm² -> fill_holes 0.01 mm² | detail 0.55, threshold 128, despeckle 0.02, corner 0.6, min feature 0.10, smooth 0.5, curve tol 0.03, gap 0.05 | Default for pasted digital logos. |
| **Thin Line Art** `thin-line-art` | background_white(40) -> threshold hysteresis(110/170) -> morph_close 0.04 mm -> despeckle 0.03 | engine centerline, mode centerline, detail 0.6, corner 0.5, min feature 0.15, smooth 0.6, curve tol 0.04, gap 0.15, centerline max 0.6 mm | Output is ENGRAVE_LINE with stroke width. Export `close_fills false`. |
| **Deep Stamp / Stencil** `stamp-stencil` | background_white -> otsu +10 -> morph_close 0.06 -> dilate 0.05 -> despeckle 0.1 -> fill_holes 0.05 | detail 0.35, threshold 138, despeckle 0.1, corner 0.7, min feature 0.30, smooth 0.4, curve tol 0.05, gap 0.1 | Boldens by 0.05 mm, kills hairlines and pinholes. Flatten tol 0.03. |
| **Photo to Plate** `photo-to-plate` | denoise 12 -> bilateral(9,60,6) -> levels(30..225) -> otsu -> morph_open 0.05 -> morph_close 0.08 -> despeckle 0.3 -> fill_holes 0.2 | detail 0.3, despeckle 0.3, corner 0.3, min feature 0.35, smooth 0.7, curve tol 0.08, gap 0.1, node budget 1500 | Silhouette, not photoreal. |
| **Small Text** `small-text` | background_white(30) -> upscale 2x lanczos -> unsharp(0.5,1.0) -> hysteresis(120/175) -> despeckle 0.005 | detail 0.85, despeckle 0.005, corner 0.85, min feature 0.05, smooth 0.3, curve tol 0.015, gap 0.03 | Protects counters (fill_holes off). Precision 4, flatten tol 0.01. |
| **QR / Data Matrix** `qr-datamatrix` | background_white(40) -> otsu -> despeckle 0.01 | detail 1.0, corner 1.0, min feature 0.05, **smooth 0.0** (polylines only), curve tol 0.02, gap 0.02 | Export 20 mm default, flatten tol 0.01. Modules stay square. |
| **Dirty Phone Photo** `dirty-phone-photo` | deskew(15°) -> denoise 8 -> bilateral(7,50,5) -> sauvola(41, k 0.25, Otsu guard) -> morph_open 0.03 -> morph_close 0.05 -> despeckle 0.15 -> fill_holes 0.05 | detail 0.4, despeckle 0.15, corner 0.5, min feature 0.25, smooth 0.6, curve tol 0.06, gap 0.1, node budget 2500 | Flattens lighting via local threshold; guard keeps big dark regions solid. |
| **Cut Outer + Engrave Inner** `cut-outer-engrave-inner` | background_white -> otsu -> knockout_halo -> despeckle 0.05 -> fill_holes 0.01 | as logo-fill but min feature 0.15, `cut_outer true` | Adds the silhouette (union of all fills, exterior rings) as red hairline CUT paths. |
| **Tiny Favicon / Low-res Logo** `tiny-logo` | background_white -> upscale 4x -> bilateral(9,60,8) -> otsu -> morph_open 1 px -> despeckle 0.05 -> fill_holes 0.02 | detail 0.35, corner 0.4, min feature 0.2, smooth 0.8, curve tol 0.06 | Tolerances stay relative to the *source* pixel, so the 4x upscale only smooths the staircase. |
| **Depth Relief (height map)** `depth-relief` | (colour stage only; no threshold) | smooth 0.6, corner 0.4, min feature 0.1 | **depth**: 24 slices, 0.5 mm, stainless 50 W, black = deep, smoothing 0.05 mm bilateral, zero plane = border, floor 0.02, draft 8 deg, min island 0.01 mm2, 8-bit PNG |
| **Depth Coin / Medallion** `depth-coin` | none | detail 0.6, min feature 0.08, curve tol 0.025 | 30 slices, 0.3 mm, brass 60 W, equalise 0.2, feather 0.05 mm, smoothing 0.03, min island 0.006, 16-bit PNG, export 40 mm centred |
| **Depth from Photo** `depth-photo-relief` | denoise 6 -> bilateral(7,40,5) | detail 0.4, corner 0.3, min feature 0.15, smooth 0.7 | 16 slices, 0.3 mm, brass, **photo_to_relief** (strength 0.6), equalise 0.3, smoothing 0.12, min island 0.04, feather 0.1, floor 0.03 |

## Slider semantics

| Slider | Model field | Range | What it does |
|---|---|---|---|
| Detail vs cleanliness | `trace.detail` | 0..1 | scales the simplification tolerance (1 = 0.5x base, 0 = 2x base) |
| Darkness threshold | `trace.threshold` | 0..255 | manual threshold value, or Otsu bias (`threshold - 128`) when the stack uses Otsu |
| Despeckle | `trace.despeckle_mm2` | mm² | remove islands below this area after the stack |
| Corner sharpness | `trace.corner_sharpness` | 0..1 | corner angle threshold 100° (0) .. 25° (1) |
| Minimum feature | `trace.min_feature_mm` | mm | drop paths/holes below this size; corner window; tiny-path warning |
| Smoothness | `trace.smoothness` | 0..1 | cubic fit tolerance multiplier; 0 = polylines only |

Advanced: `engine`, `mode`, `union_fills`, `cut_outer`, `curve_tol_mm`,
`gap_close_mm`, `node_budget`, `centerline_max_width_mm`, `snap.*` (roadmap).

## Depth settings (`depth.*`)

| Field | Default | What it does |
|---|---|---|
| `enabled` | false | run the depth pipeline instead of the flat trace |
| `levels` | 24 | slices = cumulative passes (1..60; LightBurn maps 30 colours) |
| `dark_is_deep` | true | black = deepest (LightBurn / EZCAD3); false for white = high bump maps |
| `total_depth_mm` | 0.5 | depth at the deepest tone; slice thickness = depth / levels |
| `material`, `laser_w`, `removal_per_pass_um`, `passes_per_slice` | stainless, 50, table, derived | loop count per slice = thickness / removal per pass |
| `z_step`, `z_step_every_mm` | true, 0.1 | focus-follow Z offsets in the plan |
| `smoothing_mm`, `edge_preserve` | 0.05, true | bilateral (or gaussian) blur of the height map |
| `zero_plane`, `floor` | border, 0.02 | which tone is the untouched surface; noise clamp |
| `normalize`, `black_point`, `white_point`, `depth_gamma`, `equalize` | true, 0, 255, 1.0, 0 | tonal mapping |
| `photo_to_relief`, `relief_strength` | false, 0.6 | gradient-domain bas-relief from a photo |
| `feather_mm` | 0 | ramp at the silhouette edge |
| `min_island_mm2` | 0.01 | drop islands / pits per slice |
| `draft_angle_deg` | 8 | inset every wall by depth x tan(draft) |
| `quantize_png`, `png_bits`, `png_black_is_deep` | false, 8, true | height-map PNG export |
| `hatch.enabled`, `spacing_mm`, `angle_start_deg`, `angle_step_deg`, `bidirectional`, `contour_pass`, `max_lines` | false, 0.025, 0, 37, true, true, 250000 | in-app hatch lines per slice (DEPTH_nn_HATCH) |

## Per-job files

The CLI `--save-job job.json` and the DXF/SVG exports embed the full `Job`
(source hash, stack, trace, export) so a customer's settings can be replayed:

```powershell
lasertrace acme.png --preset logo-fill --save-job acme.job.json --out acme.dxf
```
