# Depth (3D relief) engraving

How LaserTrace Pro turns a grayscale height map or a photo into something a
fiber galvo can engrave *with real depth*, what the research behind each
decision says, and how to run the result in LightBurn, EZCAD2 and EZCAD3.

## 1. What actually makes depth on a fiber laser

A fiber pulse ablates a thin skin of metal wherever it lands. Reported
removal per full-power hatch pass clusters at **10-30 µm** at 0.02-0.025 mm
line pitch (60 W MOPA on stainless 0.01-0.03 mm/pass; 20 W on aluminium
≈12 µm/pass over 256 passes; 60 W on copper ≈12.5 µm/pass; brass coins
0.3-0.5 mm in 256-512 passes). Anything advertised as "0.3 mm per pass" is
marketing. Because every pass removes about the same thickness, **depth is
encoded as pass count**, not as power:

* **Grayscale-by-power ("3D by power", EZCAD "Adjust power", LightBurn
  Grayscale)** changes oxide colour and texture on metal but not depth: the
  source has an 8-bit power setting, ablation is thresholded and thermal
  effects dominate. Use it for tonal marks, never for relief.
* **Sliced / cumulative passes (LightBurn "3D Sliced", EZCAD3 3D, per-layer
  vector stacks)** remove material where the image is dark, again and again.
  This is the only method that yields measurable relief, and it is what the
  app implements.

**Cumulative, not banded.** Pass *i* marks every pixel whose height is
≥ (i − ½)/N, so a pixel at height *h* is hit round(h·N) times and its final
depth follows the map. LightBurn's developer describes exactly this
("each pass is thresholded to the current threshold value, and the result is
run as a 1-bit image"); band slicing (only the pixels *between* two levels)
would leave the deep areas under-engraved by every pass they were skipped in.

Other physics that shaped the defaults:

| Effect | What shops do | LaserTrace default |
|---|---|---|
| Stacked parallel hatch lines dig grooves / moiré | rotate the hatch angle every pass (EZCAD *Auto rotate hatch*, LightBurn *Angle Increment*); avoid divisors of 180° | `angle_step_deg = 37` (31° also good); warning `DEPTH_HATCH_ANGLE` if the step repeats within 8 slices |
| Ejected melt re-fuses, hot metal ablates worse (stainless is "grabby") | low-power, high-frequency **clean pass** every 2-10 roughing passes; air across the part | plan suggests a clean pass every 5 slices with material-specific numbers |
| Focus depth of an F-theta is only a few mm; ±2 mm off focus gives blurry walls | step Z down as the floor drops (LaserEmboss 0.01 mm per layer; refocus every ~1 mm on copper) | `z_step` on, issued in `z_step_every_mm = 0.1` increments in the plan |
| Vertical walls undercut and leave a fragile lip; features narrower than their depth never form a floor | draft of 5-10° | `draft_angle_deg = 8`: every wall of slice *i* is inset by depth·tan(8°); warning `DEPTH_NARROW_FEATURES` |
| Pulse overlap ~50 % gives the best removal-to-roughness ratio | 0.02-0.03 mm pitch on a 40-50 µm spot | hatch pitch 0.025 mm; height-map PNG is written at that pitch (1016 DPI) |
| Low frequency = more energy per pulse = depth; high frequency = surface colour | run near the source's maximum-pulse-energy rate (JPT MOPA 60-100 kHz at 200 ns, Q-switched Raycus 20-40 kHz) | material table stores each recipe's own kHz / ns |

## 2. Height-map preparation (what the pipeline does)

`lasertrace/depth/heightmap.py`, in order, all in millimetres:

1. **Orientation.** LightBurn / EZCAD3 depth maps use *black = deepest,
   white = untouched*. Bump/height maps from STL tools use *white = high*.
   `dark_is_deep` (AUTO Depth reads it from the border tone) normalises to
   "1 = deep" internally and re-orients on export.
2. **Photo → bas-relief** (`photo_to_relief`). Luminance is not height: a
   photo sliced directly puts pits where the shadows are. The app compresses
   large gradients logarithmically and re-integrates the field (gradient
   domain, Poisson solve via DCT), a one-knob version of Weyrich et al.
   2007 *Digital Bas-Relief*. Small shading gradients survive, shadows and
   hard edges flatten. Shops otherwise use AI depth estimation (Depth
   Anything, Marigold) and then the same clean-up; feed that output to the
   height-map path instead.
3. **Smoothing** (`smoothing_mm`, bilateral by default). 8-bit steps (1/255)
   and JPEG blocks turn into visible terraces once sliced 0.03 mm thin; an
   edge-preserving blur removes them without rounding the design's walls.
   Dithering is never used: a slicer would read every dot as full depth.
4. **Zero plane** (`zero_plane = border`). The untouched surface must be
   *exactly* 0 or slice 1 covers the whole plate. Border median is the
   surface; `min` for maps that fill the frame; `floor` clamps noise a few
   levels above it.
5. **Normalise, equalise, gamma.** The deepest tone reaches
   `total_depth_mm`; LightBurn assumes the full 0-255 range and never marks
   pure white. `equalize` blends towards histogram-equalised heights so
   every slice removes similar area; `depth_gamma` is the "depth curve".
6. **Feather** (`feather_mm`) ramps the silhouette edge instead of a cliff.

`slicer.py` then thresholds at (i − ½)/N, removes islands and pits smaller
than `min_island_mm2` (≈ (2 × pitch)², the beam cannot resolve them),
applies the draft-angle inset with a sub-pixel distance transform, and
enforces nesting so slice *i + 1* ⊆ slice *i*. Each slice is traced by the
contour engine (same corner / straight-run / cubic fit as flat artwork) into
closed fills tagged `depth_index`, exported as layers `DEPTH_01 … DEPTH_NN`.

`hatch.py` (optional, `hatch.enabled`) adds `DEPTH_nn_HATCH` line layers:
parallel lines at the pitch, angle rotated per slice, serpentine order
(no return jump), plus an inset contour pass at half a pitch for crisp
walls. LightBurn and EZCAD3 hatch imported fills themselves, so this is for
EZCAD2 layer stacks or when the pattern must be locked.

`plan.py` writes the pass plan: per slice the depth before/after, the Z
offset to dial in, the loop count at the material's removal rate, the hatch
angle, area, and a time estimate; plus the raster alternative (LightBurn
*Number of Passes* = total depth / removal per pass).

## 3. Running it

### Desktop / CLI

Choose **AUTO Depth (3D relief)** or a `depth-*` preset (or tick *Depth
engraving* in the right panel). Views: **2** shows the conditioned height
map, **3** wireframe colours each slice, **4** paints the slices from light
(shallow) to dark (deep). **Export depth pack…** writes a folder:

```
<stem>_depth.dxf            one layer + colour per slice (LightBurn keys on colour, EZCAD on name)
slices/<stem>_DEPTH_nn.dxf  one file per slice, all with the same IGNORE frame (EZCAD2)
<stem>_heightmap.png        8/16-bit, black = deepest, white = surface, DPI = 25.4 / pitch
<stem>_preview.png          shaded relief preview
<stem>_plan.md / .csv / .json   pass plan + job settings + warnings
```

```powershell
lasertrace relief.png --preset auto-depth --width-mm 40 --out relief.dxf --depth-pack .\relief_pack
lasertrace coin.png --preset depth-coin --depth-mm 0.3 --material brass --laser-w 60 --removal-um 12 --depth-pack .\coin
lasertrace face.jpg --preset depth-photo-relief --depth-levels 16 --out face.png     # height map PNG for LightBurn
lasertrace relief.png --depth --depth-hatch --hatch-spacing 0.025 --angle-step 37 --out relief.dxf   # EZCAD2 hatch layers
```

### LightBurn (galvo) - 3D Sliced

1. Import `<stem>_heightmap.png`. Image mode **3D Sliced**, *Negative
   Image* off (black = deepest already), *Number of Passes* = the plan's
   *raster passes* (or 256 with power/speed trimmed so 256 × removal = depth;
   16-bit PNG for > 256 passes, LightBurn 2.1+).
2. Line Interval = the pitch in the plan (0.025 mm), Scan Angle 0, **Angle
   Increment 37°**, bi-directional on.
3. Enable Cleanup Pass: clean after 5 passes, tighter interval, higher
   frequency, ~25 % power.
4. LightBurn has no motorised Z for galvo: pause every ~0.1 mm of depth and
   refocus by the plan's Z offset.

For vector layers import `<stem>_depth.dxf`: each DEPTH layer lands on its
own colour (≤ 30). Set every layer to *Fill*, Number of Passes = loops per
slice, and step the Z offset as in the plan. Above 30 slices use the
per-slice files.

### EZCAD2

EZCAD2 has no slicer and ignores DXF layers on import, so use
`slices/*.dxf`, one at a time or all at once (each carries the same
`IGNORE` frame rectangle so they register; delete the frames or put them on
a no-mark pen). Per slice: select, **Hatch**, line space = pitch, angle 0,
*Auto rotate hatch* on with 37°, bidirectional, *Follow edge one time*,
*Loop count* = loops per slice. Run DEPTH_01 first; drop Z by the plan's
offset every 0.1 mm. If you exported `_HATCH` layers, mark those as lines
instead and skip the EZCAD hatch. Mark a clean pass (pen 2: 25 % / 2000
mm/s / 100 kHz over the current slice) every 5 slices.

### EZCAD3

Native 3D needs the licensed 3D board: import `<stem>_heightmap.png` as a
depth map (black = deepest), set the height to `total_depth_mm`, layer
thickness = slice thickness, *Dynamic Hatch* on, Z layer output to the
extension axis. Without the licence, import `<stem>_depth.dxf` and treat the
layers exactly as in EZCAD2 (EZCAD3 does honour layer names).

## 4. Calibrate before the real part

Engrave a 10 × 10 mm square for 10-20 passes at the plan's starting
parameters, measure the depth, divide by the pass count and enter it as
**Removal (µm/pass)**. The material table is a starting point; focus, air,
alloy and pitch move it by 2×. Typical outcomes reported: 0.1-0.5 mm coin
relief in 1.5-2.5 h; 3 mm in 5.5 h on 20 W aluminium; 2 mm titanium in
> 35 h.

## 5. Warnings you will see

| Code | Meaning |
|---|---|
| `DEPTH_EMPTY` | nothing above the zero plane: check convention / floor |
| `DEPTH_FEW_TONES` | source has fewer tones than slices; terraces |
| `DEPTH_TERRACED` | posterised source: raise Smoothing |
| `DEPTH_NARROW_FEATURES` | features narrower than their depth from slice *n* on |
| `DEPTH_MANY_LAYERS` | > 30 slices: LightBurn colour limit, use per-slice files or PNG |
| `DEPTH_HATCH_ANGLE` | angle step repeats within a few slices |
| `DEPTH_NO_ZERO_PLANE` | whole plate in slice 1 |
| `DEPTH_LONG_JOB` | > 2 h estimated |

## 6. Sources

Machine practice: LightBurn 3D Sliced docs (2.x and legacy galvo) and forum
threads by the developer on the threshold-per-pass algorithm, 16-bit maps
and cleanup passes; OMG Laser depth-per-pass guide and settings database;
ComMarker deep-engraving, brass coin and aluminium guides; Barch Laser
stainless carving recipe (EZCAD2, 3 hatches); Laser Marking Technologies
engraving guide; ns-fiber ablation study on 316L / CuZn37 (PMC8779350:
removal peaks near the source's characteristic repetition rate, ~50 % pulse
overlap best removal-to-roughness, multi-angle hatching removes scan
anisotropy); JPT focus notes; MoldMaking Technology on draft angle and
width-vs-depth; EZCAD2 manual (hatch and bitmap dialogs), EZCAD3 manual and
Linxuan EZCAD3 3D workflow; LightBurn DXF colour-mapping threads.

Algorithms: Weyrich et al. 2007 *Digital Bas-Relief from 3D Scenes*;
Frankot-Chellappa / DCT Poisson integration; scikit-image rolling ball;
`hatched`, `vpype`, Eggbot hatch fill; bCNC heightmap and dmap2gcode
(CNC-side slicers); omnvert / 1laser / makerforums height-map preparation
guides (full-range normalise, 0.5-1 px blur, no unsharp, no dither, 16-bit).
