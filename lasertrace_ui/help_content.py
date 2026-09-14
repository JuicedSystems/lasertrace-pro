"""In-app help: the topic library plus a tiny Markdown-subset renderer.

Content lives here as plain text so it can be diffed, searched and (later)
reused by the CLI. `render(topic)` turns a topic body into the HTML subset
QTextBrowser understands. Cross-links use `[label](topic:some-id)`.

The renderer supports exactly what the content uses: `##`/`###` headings,
`-` bullets, `1.` numbered lists, `|` tables, `>` notes, paragraphs, and the
inline forms `**bold**`, `` `code` `` and `[label](topic:id)`.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

ACCENT = "#ff8a1f"


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    section: str
    summary: str
    body: str
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        hay = f"{self.title} {self.summary} {' '.join(self.keywords)} {self.body}".lower()
        return all(word in hay for word in q.split())


# --------------------------------------------------------------------------- content

_OVERVIEW = """
LaserTrace Pro turns a pasted picture into geometry a **fiber laser** will mark
correctly on the first try. It is not a general-purpose vectorizer: every
default is chosen for marking, not for printing.

## What it is used for

**1. Customer logo to mark file.** Somebody emails a JPEG of their logo. Paste
it, check the preview, export a DXF at the exact millimetre width the part
needs. See [Logo on a tumbler](topic:recipe-logo).

**2. Small text and serial plates.** Counters (the holes in a, e, o, 8) survive
and serifs stay sharp at 2 mm cap height. See [Small text](topic:recipe-text).

**3. Codes that must scan.** QR and Data Matrix modules stay square, separate
and un-rounded. See [QR / Data Matrix](topic:recipe-qr).

**4. Line art and signatures.** A single stroke down the middle of each line
instead of a hollow outline pair, so the laser marks a line rather than a
tube. See [Line art](topic:recipe-lines).

**5. Cut outside, engrave inside.** The silhouette lands on a CUT layer, the
detail on ENGRAVE, in one file. See [Cut + engrave](topic:recipe-cut).

**6. Depth (3D relief) engraving.** A grey height map or a photo becomes a
stack of cumulative depth slices with a real pass plan, in millimetres and
micrometres. See [Depth engraving](topic:depth-what).

**7. Repeatable shop jobs.** Save the settings that worked as a named preset
(`customer-acme-tumbler`) and reuse them, or drive the same pipeline headless
from the CLI for a folder of files.

## Why not Image Trace or Vector Magic

Those tools optimise for how a shape *looks* on screen. A laser cares about
different things, and this app enforces them:

- **Node economy.** A circle is 4 nodes, not 400. EZCAD and LightBurn slow to a
  crawl on dense paths; the [Stats](topic:stats) panel shows exactly what you
  are about to import.
- **Closed fills.** An unclosed fill path leaks when the machine hatches it.
  Unclosed paths are an *error*, not a cosmetic note.
- **Holes preserved.** Counters and inner rings stay holes instead of being
  filled solid or dropped.
- **No double marks.** Touching filled regions are unioned, so the beam never
  burns the same overlap twice and leaves a dark seam.
- **Real millimetres.** Everything is sized in mm from the start, so the
  minimum-feature rule and depth numbers mean something physical.
- **Warnings that predict machine problems** rather than describing the file.
  See [Warnings](topic:warnings).

## Where things go

DXF is the primary output (EZCAD2 / EZCAD3). SVG is the secondary output and
the easier path into LightBurn. See [Exporting](topic:export).
"""

_WORKFLOW = """
## The short version

1. **Get the image in.** `Ctrl+V` pastes from the clipboard, or drop a file on
   the paste target, or `Ctrl+O`. PNG, JPG, WEBP, BMP, TIF, GIF, PDF and SVG
   all load.
2. **Leave the preset on AUTO.** AUTO measures the artwork and writes what it
   decided in the blue note under the preset box. Read that note - it tells you
   whether it saw line art, a photo, a module grid or a height map.
3. **Set the real width.** In the Export box, type the finished width in mm.
   Do this early: the minimum-feature and despeckle rules are in millimetres,
   so they only mean something once the size is right.
4. **Look at the result, not the thumbnail.** Press `2` to see what was
   actually thresholded and `3` to see the vectors. Most bad traces are bad
   binaries - fix the threshold before touching the trace sliders.
5. **Read the warnings.** Red is a problem the machine will show you. Amber is
   a problem the customer will show you.
6. **Export.** `E` writes a DXF. **Copy SVG** puts the vectors on the clipboard
   for a direct paste into LightBurn.

## When AUTO is not right

Switch to the closest shop preset (they are listed under the AUTO modes in the
same dropdown) and tune from there. See [Presets and AUTO modes](topic:presets-auto).

Two adjustments fix most jobs:

- **Darkness threshold** (`[` and `]`) - decides what counts as ink.
- **Detail / Cleanliness** - trades node count against fidelity.

> Every change re-traces after a short pause. `Ctrl+Z` and `Ctrl+Y` step
> through the whole settings history, not just the last slider.

## Make it repeatable

Once a job looks right, **Save preset** stores the whole stack - preprocess
steps, trace settings and export profile - under a name of your choosing. It
shows up in the dropdown for every future job.
"""

_SCREEN = """
## Left panel - input and conditioning

- **Paste target.** Click it, drop a file on it, or press `Ctrl+V`.
- **Preset.** AUTO modes first, then the shop presets, then anything you saved.
  Hover any entry for its description. The blue note underneath is AUTO's
  reasoning for this specific image.
- **Preprocess stack.** The ordered list of image operations that runs before
  tracing. Untick to disable a step, use Up/Down to reorder, **Add step** to
  append one. See [The preprocess stack](topic:preprocess).

## Centre - the four views

| Key | View | Shows |
|---|---|---|
| `1` | Original | The source pixels, untouched. |
| `2` | Binary | What the preprocess stack produced - the black/white image the tracer actually sees. In a depth job this is the conditioned height map. |
| `3` | Vectors | Wireframe of the exported paths, coloured by layer (or by slice in a depth job). |
| `4` | Laser sim | The paths painted as the laser would mark them: fills filled, lines at beam width, depth slices shaded light-to-dark. |

`Space` toggles between the current view and the original - the fastest way to
check fidelity.

## Overlays

- **Nodes** - every vertex. If a straight edge is dotted with nodes, lower
  Detail or raise Smoothness.
- **Open ends** - unclosed path endpoints, drawn as markers. Any open end on a
  fill layer will leak when the machine hatches it.
- **Tiny paths** - paths below the Minimum feature size, highlighted. These
  blob into dots at the machine.
- **mm grid** - a real millimetre grid at the current export width. Use it to
  eyeball stroke widths.

**Split** wipes the original in from the left over the current view.
**Fit** (`F`) reframes; the mouse wheel zooms and double-click fits.

## Right panel - trace, measure, export

**Trace** holds the six everyday sliders, **Depth engraving** the relief
controls, **Advanced** the engine and geometry internals, then
[Stats](topic:stats), [Warnings](topic:warnings) and Export.

The status bar always shows paths, nodes, finished size in mm and the trace
time for the last run.
"""

_SHORTCUTS = """
## Input and output

| Key | Action |
|---|---|
| `Ctrl+V` | Paste artwork from the clipboard |
| `Ctrl+O` | Open an image file |
| `E` | Export DXF |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo a settings change |
| `Enter` | Force a re-trace now |
| `F1` | This help |

## Viewing

| Key | Action |
|---|---|
| `1` `2` `3` `4` | Original / Binary / Vectors / Laser sim |
| `Space` | Toggle the original against the current view |
| `F` | Fit to window |
| `Ctrl+=` / `Ctrl+-` | Zoom in / out |
| Wheel | Zoom at the cursor |
| Double-click | Fit |

## Tuning

| Key | Action |
|---|---|
| `[` | Darkness threshold down 4 |
| `]` | Darkness threshold up 4 |

> Dragging a file onto the paste target works the same as `Ctrl+O`, and
> dropping an image copied from a browser works the same as `Ctrl+V`.
"""

_PRESETS = """
A **preset** is a fixed recipe. An **AUTO mode** measures your image first and
then writes a recipe for it - the sliders it produces are ordinary settings you
can keep tuning.

## AUTO modes

| Mode | Use it for |
|---|---|
| AUTO (detect everything) | The default. Classifies the artwork, picks the closest shop preset, then tunes threshold, invert, despeckle, minimum feature and outline/centerline/hybrid from the image. |
| AUTO Black & White | Art that is already clean black on white (or white on black): hard 50% threshold, no smoothing, sharp corners, auto invert. |
| AUTO Photo / Scan | Phone photos and scans: deskew, denoise and a local (adaptive) threshold sized from the measured noise. |
| AUTO Line Art | Drawings and signatures: centerline everywhere thin, fills only where strokes are genuinely wide. |
| AUTO Depth (3D relief) | Height maps and photos destined for [depth engraving](topic:depth-what). Detects which tone is the surface and picks slice count and smoothing from the tonal range. |

**Read the blue note.** AUTO writes down every decision it made and why -
"strokes 0.31 mm median, widest 0.44 mm, no fills -> centerline", "38 specks ->
despeckle 0.1 mm2", "module grid detected -> square polylines". If the result
is wrong, that note usually says which measurement misled it.

## Shop presets

| Preset | Use it for |
|---|---|
| Logo Fill Mark | High-contrast filled logo: unioned regions, few nodes, holes preserved. |
| Small Text | Protect counters and serifs: hysteresis threshold, minimal smoothing, tiny holes kept. |
| Thin Line Art | Centerline trace of line drawings: one stroke per line, breaks reconnected, no outline pairs. |
| QR / Data Matrix | No smoothing, square corners, modules kept as separate rectangles. |
| Tiny Favicon / Low-res Logo | Upscale 4x, edge-preserving smooth, then trace with generous smoothing so pixel stairs become curves. |
| Dirty Phone Photo | Deskew, flatten uneven lighting, adaptive threshold, despeckle. |
| Photo to Plate | Aggressive binary + denoise for a readable silhouette on a nameplate. Not photoreal. |
| Deep Stamp / Stencil | Bold, closed, slightly dilated shapes with no hairlines; tiny holes filled. |
| Cut Outer + Engrave Inner | Silhouette on the CUT layer (red hairline), interior detail on ENGRAVE. |
| Depth Relief (height map) | Grey height map to cumulative depth slices. 24 slices, 0.5 mm, 8 deg draft. |
| Depth Coin / Medallion (brass) | Coin-style relief: 30 slices, 0.3 mm, light equalisation, feathered rim. |
| Depth from Photo (bas-relief) | Photo to plausible bas-relief: gradients compressed and re-integrated. |

## Your own presets

**Save preset** captures the current preprocess stack, trace settings and
export profile under a name. Name them after the job, not the setting -
`customer-acme-tumbler` beats `threshold-140`. Saved presets appear in the
dropdown alongside the shop ones.
"""

_PREPROCESS = """
The stack is the image pipeline that runs **before** any vector is made. Most
disappointing traces are a preprocess problem: press `2` and look at the binary
before you touch a trace slider.

## How the order works

Steps run in three stages, and the stage always wins over your ordering:

1. **Colour** - everything that works on the greyscale/RGB image.
2. **Threshold** - the single step that turns grey into black and white.
3. **Binary** - everything that cleans up the black-and-white result.

Inside a stage the list order is exactly what you set with Up / Down.

## The steps

**Colour stage**

| Step | What it does |
|---|---|
| Crop | Trim the source before anything else. |
| Rotate | Fixed rotation. |
| Straighten (deskew) | Detects the dominant angle of a photographed or scanned sheet and levels it. |
| Flatten white background | Removes uneven lighting so paper reads as one white. |
| Remove background colour | Samples a background colour and knocks it out - for logos on a coloured field. |
| Upscale tiny logo | Lanczos upscale so a favicon has enough pixels to smooth. AUTO adds this below ~300 px. |
| Denoise | Sensor noise removal, strength scaled to the measured noise. |
| Kill JPEG blocks | Edge-preserving (bilateral) smoothing that removes 8x8 compression artefacts without softening edges. |
| Sharpen soft scan | Unsharp mask for a soft scan. |
| Levels / contrast | Black point / white point stretch. |
| Gamma | Midtone lift or crush. |
| Reduce to few colours | Posterise before thresholding - useful for flat-colour logos. |

**Threshold stage**

| Step | What it does |
|---|---|
| Black / white threshold | The decision. Manual (a fixed 0-255 cutoff), Otsu (global, automatic), Sauvola (local window - the right choice for uneven lighting), or hysteresis (two cutoffs, protects thin detail). |

**Binary stage**

| Step | What it does |
|---|---|
| Invert | Light pixels become ink. AUTO switches this on when it sees a dark border. |
| Knock out grey halo | Removes the soft anti-aliased fringe that would otherwise become a second contour. |
| Remove thin fuzz (open) | Morphological open: deletes hairs and stray pixels. |
| Reconnect breaks (close) | Morphological close: bridges gaps in a stroke that the threshold broke. |
| Bolden | Dilate - thickens everything. |
| Thin | Erode - slims everything. |
| Despeckle | Drops islands below an area. |
| Fill pinholes | Fills tiny holes that are threshold artefacts rather than real counters. |

> Be careful with **Bolden** and **Thin** on artwork with fine gaps: they change
> the shape everywhere, and on text they close counters. Prefer fixing the
> threshold.
"""

_TRACE = """
## The six everyday sliders

**Detail <-> Cleanliness.** The master trade. Right = more nodes, closer to the
binary. Left = fewer nodes, smoother, faster to import. Start in the middle and
move left until the shape starts to lie.

**Darkness threshold** (`[` / `]`). Which greys count as ink, 0-255. This is the
single most useful control. If detail is missing, it is usually here and not in
the trace settings.

**Despeckle (mm²).** Islands smaller than this area are deleted. Because it is
in real square millimetres, set the export width first. 0.02 mm² clears sensor
noise; 0.3 mm² is aggressive and will eat dots on i's.

**Corner sharpness.** Right keeps every corner as a corner - required for QR
codes, module grids and blocky text. Left rounds corners into curves, which
looks better on organic logos.

**Minimum feature (mm).** The smallest thing your machine can actually mark.
Paths smaller than this are dropped and anything near it is flagged as a
[tiny path](topic:warnings). Set it to what your spot size and material really
resolve - typically 0.08-0.15 mm on a fiber galvo.

**Smoothness.** Curve fitting strength. 0 gives straight polylines only, which
is exactly what you want for a module grid and exactly wrong for a signature.

## Advanced

**Engine.** `contour` is the built-in tracer and the default. `potrace` runs the
GPL Potrace binary as a separate sidecar process (it is licensed separately and
never linked into the app); it produces smoother curves on organic art. An
engine that is not installed falls back to `contour` and raises an
`ENGINE_FALLBACK` warning rather than failing the job.

**Mode.** Outline, centerline or hybrid - see [Outline vs centerline](topic:modes).
Mode wins over Engine: choosing centerline uses the centerline tracer whatever
the Engine box says.

**Union touching fills.** On by default, and it should stay on for fills.
Overlapping filled regions are merged into one so the beam never crosses the
same overlap twice and leaves a visibly darker seam.

**Silhouette on CUT layer.** Puts the outer boundary on the CUT layer as a
hairline and keeps the interior on ENGRAVE - one file, two operations.

**Curve fit tolerance (mm).** How far a fitted curve may stray from the traced
pixels. Larger = fewer nodes. This is the node-count control that thinks in
millimetres rather than in a 0-1 slider.

**Gap close (mm).** Bridges endpoints this far apart. Raise it when you get
`UNCLOSED_PATHS` on artwork that looks closed to the eye.

**Node budget.** A hard ceiling. Set it to whatever your controller imports
comfortably and you get a warning before the machine does.

**Centerline max width (mm).** The width below which a stroke is treated as a
line rather than a filled region. Everything wider becomes a fill. This is the
knob that decides what hybrid mode does with each stroke.
"""

_MODES = """
## Outline

Traces the boundary of every black region and hands the machine closed shapes
to hatch. This is what you want for a filled logo, solid text and any solid
mark.

## Centerline

Traces a single stroke down the **middle** of each line. Use it for line
drawings, signatures, technical diagrams, thin lettering - anything drawn with
strokes rather than filled.

The failure it prevents is the classic one: outline-tracing a 0.3 mm line gives
you two parallel contours 0.3 mm apart. The machine marks both edges and leaves
an unmarked strip down the middle - a hollow tube instead of a line. If you see
the `OUTLINE_PAIR_SUSPECT` warning, this is what happened.

Centerline output lands on the **ENGRAVE_LINE** layer, so you can set it to a
single-pass line operation instead of a fill.

## Hybrid

Measures every region: anything narrower than **Centerline max width** becomes
a stroke, everything wider becomes a fill. This is the right answer for real
artwork that mixes a solid wordmark with hairline rules or a thin border.

AUTO picks between the three from the measured stroke-width distribution and
tells you which and why in its note.
"""

_STATS = """
Everything in the Stats box describes the geometry you are about to export.

| Line | What to look for |
|---|---|
| **engine** | Which tracer actually ran. If this is not what you selected, look for an `ENGINE_FALLBACK` warning. |
| **paths / nodes** | The import cost. A clean logo is tens to a few hundred nodes. Thousands means the trace is chasing pixel noise. |
| **closed / open** | Open paths on a fill layer leak. Open is fine and expected on ENGRAVE_LINE (centerline) output. |
| **holes** | Counters and inner rings that survived. The bracketed number is how many the binary had, so `holes 7 (binary 9)` means two were lost - usually to Minimum feature. |
| **size** | The finished mark in millimetres at the current export width. Check this against the part before you export. |
| **fill area** | Total marked area in mm². Roughly proportional to marking time for a fill. |
| **complexity** | Nodes per cm², 0-100. Above ~70 the controller import gets slow. |
| **travel** | Estimated non-marking jump distance. |
| **fidelity** | Overlap (IoU) between the vector fill and the binary it came from. Above 97% is a faithful trace; below ~90% the trace is losing small detail. Not shown for centerline or depth output, where it is meaningless. |
| **time** | How long the last trace took. |

> Fidelity is measured against the **binary**, not the original image. A
> perfect 99% on a bad threshold still gives you a bad mark - which is why the
> `2` view matters.
"""

_WARNINGS = """
Warnings are colour-coded: **red** is something the machine will get wrong,
**amber** is something the customer will notice, **blue** is information.

## Tracing

| Code | Meaning and fix |
|---|---|
| `EMPTY` | No ink found. Check the threshold, and whether the art needs Invert. |
| `UNCLOSED_PATHS` | Fill paths are not closed - EZCAD's fill will leak out of them. Raise **Gap close**. |
| `HOLES_LOST` | Counters or holes present in the binary did not survive tracing. Lower **Minimum feature** or **Despeckle**. |
| `TINY_PATHS` | Paths smaller than the minimum feature will blob into dots. Turn on the Tiny paths overlay to see which ones, then despeckle them away or make the mark bigger. |
| `OUTLINE_PAIR_SUSPECT` | Thin strokes were traced as outline pairs, so the laser will mark hollow tubes. Switch to Thin Line Art / centerline - see [Outline vs centerline](topic:modes). |
| `NODE_BUDGET_EXCEEDED` | More nodes than the budget you set. Lower Detail or raise Curve fit tolerance. |
| `LOW_FIDELITY` | The vector fill matches the binary poorly; small text and detail are degrading. Increase Detail or lower Smoothness. |
| `HIGH_COMPLEXITY` | Many nodes per cm² - the controller import will be slow. Lower Detail or set a node budget. |
| `ENGINE_FALLBACK` | The requested engine was unavailable; contour ran instead. |

## Depth

| Code | Meaning and fix |
|---|---|
| `DEPTH_EMPTY` | The image is flat after conditioning. Check the black/white convention, the Floor and the zero plane. |
| `DEPTH_FEW_TONES` | Too few distinct greys for the slice count - you will get visible terraces. Use a smoother or 16-bit source, raise Smoothing, or cut the slice count. |
| `DEPTH_TERRACED` | The source is posterised or 8-bit stepped. Raise **Smoothing (mm)**. |
| `DEPTH_NARROW_FEATURES` | Some slices contain features narrower than they are deep. The side walls converge and the floor never forms. Reduce **Total depth** or widen the detail. |
| `DEPTH_MANY_LAYERS` | More than 30 slices, which is more than LightBurn's colour layers. Use the per-slice DXF files or the height-map PNG. |
| `DEPTH_LONG_JOB` | The estimated marking time is hours. Worth knowing before you clamp the part. |
| `DEPTH_NO_ZERO_PLANE` | Nothing was treated as untouched surface - the whole plate is inside slice 1. Set Zero plane to `border` or raise the Floor. |
| `DEPTH_HATCH_CAPPED` | Hatch generation hit its line cap, so deep slices have no hatch. Widen the spacing. |
| `DEPTH_HATCH_ANGLE` | The hatch angle step divides into 180, so the same lines repeat every few slices and cut grooves. Use 31-37 deg. |
"""

_DEPTH_WHAT = """
Depth engraving cuts a **3D relief** into metal - a coin, a medallion, a tool
mark, a bas-relief portrait - instead of a flat mark.

## The one thing that matters

**Depth comes from pass count, never from power.** Turning the power down does
not make a shallower cut on a fiber laser, it makes a duller one. Depth is
built up by marking the same area many times.

So a height map has to become a **stack of passes**, and that is what this
panel does.

## Cumulative slices

The image is read as a height map: one tone = one height. It is split into N
slices, and **slice i contains everything deeper than i/N** - not just the band
between i/N and (i+1)/N.

That nesting is the whole trick. The deepest point is inside every slice, so it
gets every pass and ends up at full depth. A point halfway down is inside half
the slices and ends up halfway. Band slicing - where each pass marks only its
own band - under-engraves the deep areas badly, and it is the most common way
people get this wrong. LightBurn's 3D Sliced mode and EZCAD3 are both
cumulative for the same reason.

## The convention

By default **black = deepest, white = untouched surface**. This matches
LightBurn and EZCAD depth maps. Height/bump maps from 3D software usually use
the opposite convention - flip it with the **Convention** dropdown, or let AUTO
Depth read the border tone and decide.

## Getting started

1. Choose **AUTO Depth (3D relief)** in the preset list, or tick **Depth
   engraving** in the right panel.
2. Set the finished **width** and the **Total depth**.
3. Pick the **Material** and enter your **Laser power**. The removal-per-pass
   table gives you a starting pass plan.
4. Press `2` to check the conditioned height map, `4` to see the slices shaded.
5. **Export depth pack...** writes everything the machine needs.

Then: [every depth setting](topic:depth-settings) ·
[what is in the pack](topic:depth-pack) ·
[LightBurn](topic:depth-lightburn) · [EZCAD](topic:depth-ezcad) ·
[calibrate first](topic:depth-calibrate)

> Depth jobs ignore the ordinary fill union and the fidelity/hole statistics -
> stacked nested slices make those numbers meaningless by construction.
"""

_DEPTH_SETTINGS = """
## Geometry

**Slices.** How many cumulative layers, which is how many marking passes the
plan is built around. More slices = smoother relief and a longer job. 16-30 is
the normal range; LightBurn maps at most 30 colours to layers, so above 30 use
the per-slice DXF files or the height-map PNG.

**Total depth (mm).** How deep the darkest tone ends up. Slice thickness =
total depth / slices. Be honest here: 0.3 mm is a coin relief, 0.5 mm is deep,
and 2 mm is a job measured in tens of hours.

**Convention.** Black = deepest (LightBurn / EZCAD) or white = deepest (height
and bump maps out of 3D software).

## Machine

**Material** and **Laser power** look up a starting removal rate. Real measured
figures are 10-30 µm per full-power pass at 0.02-0.025 mm pitch - about 12 µm
on aluminium with 20 W, 12.5 µm on copper. Vendor claims of "0.3 mm per pass"
are marketing. The table scales with power^0.75.

**Removal (µm/pass).** Your own measured number, which overrides the table.
Leave it at *from table* until you have measured -
[how to measure](topic:depth-calibrate). This is the single number that makes
the depth come out right.

## Height-map conditioning

**Smoothing (mm).** Edge-preserving blur. Removes the 8-bit terracing and JPEG
noise that would otherwise turn into visible steps in the relief, without
rounding off the walls. Raise it if you get `DEPTH_TERRACED`.

**Depth curve (gamma).** Above 1 keeps more of the image shallow; below 1
deepens the midtones. Use it when a relief looks flat in the middle tones.

**Equalise tones.** Blends towards a histogram-equalised height map so each
slice removes a similar amount of area. Evens out the pass times and stops one
slice from doing almost nothing.

**Photo to bas-relief.** Luminance is not height - a dark shadow is not a deep
pit. This attenuates the large gradients (shadows, hard edges) and
re-integrates, so a photograph becomes a plausible relief instead of a stack of
lighting artefacts. **Relief strength** controls how hard it compresses.

## Slice hygiene

**Wall draft (deg).** Every wall is inset by depth × tan(draft), so slices step
inwards as they go deeper. The result is a chamfered wall instead of a fragile
undercut lip. 8 deg is a good default; 0 gives vertical walls that the beam
cannot actually produce anyway.

**Edge feather (mm).** Softens the silhouette so the relief does not start with
a vertical cliff at its outline.

## Hatch

**Generate hatch lines per slice.** Adds `DEPTH_nn_HATCH` line layers with the
fill lines already computed - serpentine order, inset contour pass, angle
rotated per slice. This is for **EZCAD2**, which has no slicer of its own.
LightBurn and EZCAD3 hatch perfectly well themselves, so leave it off for them.

**Hatch pitch (mm).** Line spacing, typically 0.02-0.025 mm on a fiber galvo.
The removal figures above assume this range.

**Angle step (deg).** How far the hatch rotates between slices. **Never use a
divisor of 180** (0, 30, 45, 60, 90): the same lines repeat every few slices and
cut grooves into the floor. 31-37 deg is field-proven; 37 is the default.

**16-bit height map PNG.** For LightBurn 2.1+ when you need more than 256
passes. EZCAD wants 8-bit.

## Reading the depth report

Under the settings you get the plan: slices × thickness, the removal rate and
loops per slice, the raster pass count for LightBurn 3D Sliced, footprint and
removed volume, the starting power/speed/frequency, and an estimated time.
Treat the time as an order of magnitude until you have calibrated.
"""

_DEPTH_PACK = """
**Export depth pack...** writes a folder containing every form the two
controller families need:

| File | For |
|---|---|
| `<stem>_depth.dxf` | One layer **and colour** per slice. LightBurn keys on entity colour, EZCAD3 on layer name. |
| `slices/<stem>_DEPTH_nn.dxf` | One file per slice, each carrying the same `IGNORE` frame rectangle so they all register on top of each other. This is the EZCAD2 path. |
| `<stem>_heightmap.png` | 8- or 16-bit height map, black = deepest, white = surface, DPI set from the hatch pitch. This is what LightBurn 3D Sliced and EZCAD3 want. |
| `<stem>_preview.png` | Shaded relief preview - what the finished part should look like. Show the customer this one. |
| `<stem>_plan.md` | The human-readable pass plan: slice table, depths, Z offsets, machine settings, clean-pass schedule. Print it and keep it at the machine. |
| `<stem>_plan.csv` | The same table for a spreadsheet. |
| `<stem>_plan.json` | Plan plus the complete job settings plus the warnings, for archiving or scripting. |

The exported DXF also carries the job settings, so a file you shipped six
months ago can be reproduced exactly.

> The `IGNORE` frame in the per-slice files exists only to keep every slice in
> the same coordinate frame. Delete the frames after import, or assign them to
> a pen that does not mark.
"""

_DEPTH_LB = """
## The easy way - 3D Sliced

1. Import `<stem>_heightmap.png`.
2. Image mode **3D Sliced**. Leave *Negative Image* **off** - the PNG is
   already black = deepest.
3. *Number of Passes* = the **raster passes** figure from the plan. (Or set 256
   and trim power/speed so 256 × your removal rate equals the target depth. For
   more than 256 passes you need the 16-bit PNG and LightBurn 2.1+.)
4. **Line Interval** = the pitch from the plan, typically 0.025 mm.
5. **Scan Angle** 0, **Angle Increment 37 deg**, bi-directional on.
6. Enable **Cleanup Pass**: clean after every 5 passes, tighter interval, higher
   frequency, around 25% power. This clears the recast and slag that otherwise
   shields the floor and stalls the cut.
7. LightBurn has no motorised Z for a galvo. **Pause every ~0.1 mm of depth and
   refocus** by the Z offset in the plan - out-of-focus passes stop removing
   material.

## The vector way

Import `<stem>_depth.dxf`. Each `DEPTH_nn` layer arrives on its own colour
(LightBurn ignores DXF layer *names* and keys on colour, with 30 slots). Set
every layer to **Fill**, *Number of Passes* = the loops-per-slice figure from
the plan, and step the Z offset as the plan says.

Above 30 slices, use `slices/*.dxf` and import them individually, or switch to
the height-map route.
"""

_DEPTH_EZ = """
## EZCAD2

EZCAD2 has no slicer and ignores DXF layers on import, so use the per-slice
files in `slices/`.

1. Import `slices/<stem>_DEPTH_01.dxf` (or all of them - each carries the same
   `IGNORE` frame so they register). Delete the frames or put them on a pen
   that does not mark.
2. Select the slice, **Hatch**: line space = the plan's pitch, angle 0, *Auto
   rotate hatch* on with **37 deg**, bidirectional, *Follow edge one time*.
3. *Loop count* = the loops-per-slice figure from the plan.
4. Run `DEPTH_01` first, then each following slice. Drop Z by the plan's offset
   every ~0.1 mm of accumulated depth.
5. Mark a **clean pass** every 5 slices - a second pen at roughly 25% power,
   2000 mm/s, 100 kHz over the current slice.

If you exported the `_HATCH` layers, mark those as **lines** and skip EZCAD's
own hatch entirely.

## EZCAD3

With the licensed 3D board: import `<stem>_heightmap.png` as a depth map (black
= deepest), set height = the total depth, layer thickness = the slice
thickness, turn on **Dynamic Hatch**, and send the Z layer output to the
extension axis.

Without the 3D licence, import `<stem>_depth.dxf` and treat the layers exactly
as in EZCAD2 above - EZCAD3 does honour DXF layer names, so the slices arrive
already labelled.
"""

_DEPTH_CAL = """
The material table is a **starting point**. Focus, assist air, the exact alloy
and the hatch pitch move real removal rates by a factor of two, so the number
that makes your depths come out right is one you measure once per material and
then reuse forever.

## The test

1. Set up the job at the plan's starting parameters - same power, speed,
   frequency and **the same hatch pitch**.
2. Engrave a plain **10 × 10 mm square** for a known pass count: 10 passes on a
   fast material, 20 if it is slow.
3. Measure the depth with a depth gauge or a dial indicator on a surface plate.
4. Divide: depth in µm / pass count = **removal per pass**.
5. Type that into **Removal (µm/pass)** and re-read the plan. Every slice count,
   loop count and time estimate updates.

Redo the test whenever you change material, alloy, lens or pitch.

## What to expect

Real figures from calibrated shops: 10-30 µm per full-power pass at 0.02-0.025
mm pitch on stainless with 60 W MOPA, about 12 µm on aluminium with 20 W, about
12.5 µm on copper. Finished jobs: 0.1-0.5 mm of coin relief in 1.5-2.5 hours,
3 mm in aluminium at 20 W in about 5.5 hours, 2 mm in titanium in over 35 hours.

If a vendor quotes 0.3 mm per pass, they are quoting marketing rather than
metal.

> Calibrate on scrap of the actual material, not on a similar one. "Stainless"
> covers alloys that differ by 40% in removal rate.
"""

_EXPORT = """
## Size and placement

**Width (mm)** is the finished width of the mark, and everything else follows
from it. Set it before you tune despeckle or minimum feature, because those are
in real millimetres.

**DXF origin** decides where 0,0 sits: `bottom_left`, `top_left` or `center`.
Galvo jobs are usually easiest with `center`, since the field is centred on the
lens.

## Geometry

**Curves.** `polyline` writes everything as line segments - universally
readable, more nodes. `spline` keeps fitted curves as splines, which is
smaller and smoother but needs a controller that reads them. When in doubt,
polyline.

**DXF version.** `R2000` is the default. `R12` is the maximum-compatibility
option for old controllers; it cannot carry splines, so pair it with polyline.

**Flatten to one layer** merges CUT, ENGRAVE_FILL and ENGRAVE_LINE into a single
layer. Use it when the receiving software would rather see one thing.

## Layers

| Layer | Meaning |
|---|---|
| `ENGRAVE_FILL` | Closed filled regions - hatch these. |
| `ENGRAVE_LINE` | Single-pass lines from centerline tracing. |
| `CUT` | The silhouette, as a red hairline. |
| `SCORE` | Light scoring, blue. |
| `IGNORE` | Construction geometry (registration frames). Never mark it. |
| `DEPTH_nn` | One per depth slice, with matching colours. |
| `DEPTH_nn_HATCH` | Pre-computed hatch lines for that slice. |

## The buttons

- **Export DXF** (`E`) - the primary output, for EZCAD2 / EZCAD3.
- **Export SVG** - for LightBurn, Inkscape, Illustrator.
- **Copy SVG** - the vectors straight onto the clipboard. Paste into LightBurn
  and skip the file entirely; this is the fastest route.
- **Export depth pack...** - only visible on a depth job. See
  [The depth pack](topic:depth-pack).
- **Save preset** - store these settings under a name.
- **Compare engines...** - traces the same binary four ways side by side
  (built-in contour, Potrace, a dense "Illustrator-like" setting and a smooth
  "Vector Magic-like" one) with node counts and fidelity for each. Use it when
  you cannot decide, not on every job.

Every exported file embeds the job settings, so an old delivery can be
reproduced exactly.
"""

_LIGHTBURN = """
## Getting the geometry in

The fastest route is **Copy SVG** here, then paste directly into LightBurn. No
file, no import dialog. **Export SVG** to a file works identically if you want
to keep it.

DXF also imports, but LightBurn **ignores DXF layer names and keys on entity
colour** - which is why the exporter assigns a distinct colour per layer.

## Setting up the layers

| What you exported | LightBurn setting |
|---|---|
| `ENGRAVE_FILL` | **Fill**. Check the interval against the mm grid you saw in the preview. |
| `ENGRAVE_LINE` (centerline) | **Line**, single pass. Do *not* set it to Fill - there is nothing to fill. |
| `CUT` | **Line** at cut power, or your usual cut layer. |
| `IGNORE` | Turn the layer output off. |

## Sizing

The SVG and DXF are already in millimetres at the width you set, so the object
should land at the right size with no scaling. If it does not, check that
LightBurn's import units are mm and that no "fit to page" option is active.

For depth work see [Depth in LightBurn](topic:depth-lightburn).
"""

_EZCAD = """
## Importing

Export DXF (`E`) and import it. Use **R2000** with polyline curves unless you
have a reason not to; **R12** is there for older controllers and cannot carry
splines.

## What EZCAD needs from you

- **Closed fills.** EZCAD's hatch leaks out of an open path. This is why
  `UNCLOSED_PATHS` is a red error here and not a note - fix it with **Gap
  close** before you export.
- **Few nodes.** Import time and mark time both scale with node count. Watch
  **complexity** in [Stats](topic:stats) and set a **node budget** if your
  controller has a comfortable ceiling.
- **No double marks.** Leave **Union touching fills** on. Overlapping regions
  marked twice leave a visibly darker seam on the part.

## Layers

**EZCAD2 ignores DXF layers on import.** If you need separate operations - cut
outside, engrave inside - export them as separate files, or separate them by
hand after import.

**EZCAD3 honours layer names**, so `ENGRAVE_FILL`, `CUT` and `DEPTH_nn` arrive
labelled.

## Hatching

Set the hatch line space to what your spot size supports (0.02-0.05 mm is
typical on a fiber galvo), turn on *Auto rotate hatch* with an angle that is
**not** a divisor of 180, and use *Follow edge one time* for a crisper boundary.

For depth work see [Depth in EZCAD](topic:depth-ezcad).
"""

_R_LOGO = """
**The job:** a customer emailed a logo. It needs to be a 38 mm filled mark on a
tumbler.

1. Copy the logo and press `Ctrl+V`.
2. Leave the preset on **AUTO**. Read the blue note - you want to see something
   like "classified as logo -> logo-fill".
3. Set **Width** to `38` mm in the Export box.
4. Press `2`. Is the binary the shape you expect? If the logo is light on a
   dark field, AUTO should already have inverted it; if not, add **Invert** to
   the stack. If detail is filling in or dropping out, nudge the **Darkness
   threshold** with `[` and `]`.
5. Press `3` with **Nodes** on. Straight edges should have nodes only at the
   corners. If they are peppered with nodes, drag **Detail** left.
6. Check **holes** in Stats. `holes 4 (binary 4)` is what you want; a lower
   first number means counters were lost - lower **Minimum feature**.
7. Warnings should be clear or blue only.
8. Press `E` and export the DXF, or hit **Copy SVG** and paste into LightBurn.

## If it fights you

| Symptom | Fix |
|---|---|
| Gradients or shadows in the logo trace as noise | Add **Reduce to few colours** before the threshold, or raise the threshold. |
| Soft, blurry edges from a small JPEG | Add **Kill JPEG blocks**, and **Upscale tiny logo** if it is under ~300 px. |
| Thin outlines look hollow | The strokes are lines, not fills - see [Line art](topic:recipe-lines). |
| A dark seam where two shapes overlap | **Union touching fills** got switched off. Turn it back on. |
"""

_R_TEXT = """
**The job:** a serial number or a name plate. Small text that must stay
readable.

1. Paste, then choose **Small Text** (or let AUTO classify it as text).
2. **Set the width first.** Everything below is in millimetres and means
   nothing until the size is right.
3. Set **Minimum feature** to what your machine really resolves - 0.08 mm on a
   good fiber galvo. Do not leave it at a value larger than the thinnest part of
   your letters.
4. Push **Corner sharpness** high. Serifs are corners; rounding them makes the
   text look melted.
5. Keep **Smoothness** low. Curve fitting on 2 mm letters costs you more shape
   than it saves in nodes.
6. Press `2` and zoom into the smallest character. Counters - the holes in a, e,
   o, 8, 6 - must be open in the **binary**. If they are filled there, no trace
   setting can recover them: lower the threshold or add **Thin**.
7. Check `holes` in Stats against the binary count, and watch for `HOLES_LOST`.

> The classic small-text failure is despeckle. At 2 mm cap height a counter can
> be under 0.05 mm², so a 0.1 mm² despeckle deletes every one of them and the
> text marks as solid blobs. Keep despeckle small on text.
"""

_R_QR = """
**The job:** a QR code or Data Matrix that has to scan.

1. Paste and choose **QR / Data Matrix**. AUTO also detects module grids on its
   own and says so ("module grid detected -> square polylines, no morphology").
2. **Smoothness 0** and **Corner sharpness 1**. A rounded module is a module the
   scanner may misread, and rounding also shrinks it.
3. Leave morphology off. **Bolden** and **Thin** change every module's size and
   destroy the quiet-zone ratios the scanner depends on.
4. Modules stay as **separate rectangles** - do not union them into blobs, and
   do not "clean up" the pattern.
5. Set the width from the scanner requirement, not from what looks good, then
   check the mm grid overlay: each module wants to be comfortably above your
   minimum feature size.
6. Test-scan the actual marked part with the actual scanner. Contrast on metal
   is not the same as contrast on paper.

> Never regenerate a code by tracing a picture of it if you have the data.
> Generating a fresh code from the string always beats vectorising a screenshot.
"""

_R_PHOTO = """
**The job:** the only artwork available is a phone photo of a printed logo, a
sign or a business card.

1. Paste it and choose **AUTO Photo / Scan** (or the **Dirty Phone Photo**
   preset).
2. The stack will already have **Straighten (deskew)** and **Flatten white
   background** in it - both matter here. A photo taken at an angle under a
   ceiling light has a brightness gradient that no single global threshold can
   cope with.
3. The threshold step should be **Sauvola** (local). AUTO sizes the window from
   the image; if patches of background come through as ink, make the window
   smaller.
4. Denoise strength is set from the measured noise. Raise it for a dim indoor
   shot, drop it to zero for a clean one - too much denoise eats fine detail.
5. **Crop first** if the photo has background clutter. It is faster than trying
   to threshold it away.
6. Expect to despeckle. Check the Tiny paths overlay before exporting.

> A photo is a last resort. If you can get the original vector, a PDF or even a
> flat-bed scan, every one of those beats the best photo. It is always worth one
> more email to ask.
"""

_R_LINES = """
**The job:** a signature, a line drawing, a technical diagram - artwork drawn
with strokes rather than filled shapes.

1. Paste and choose **Thin Line Art**, or **AUTO Line Art** to have the stroke
   widths measured for you.
2. Confirm the mode is **centerline** (Advanced box, or read AUTO's note:
   "strokes 0.31 mm median, widest 0.44 mm, no fills -> centerline").
3. Output lands on **ENGRAVE_LINE**. In LightBurn set that layer to **Line**,
   single pass - not Fill.
4. If a stroke is broken into dashes, raise **Gap close** or add **Reconnect
   breaks (close)** to the stack.
5. Set **Centerline max width** to roughly twice your median stroke width.
   Anything wider than this becomes a filled region instead of a line, which is
   what you want where the artwork genuinely has a solid blob.

## Mixed artwork

A signature under a solid wordmark should use **hybrid**: strokes become lines,
the wordmark becomes a fill, in one file on two layers. See
[Outline vs centerline](topic:modes).

> If you traced this as outline by mistake you get the `OUTLINE_PAIR_SUSPECT`
> warning and, on the part, a hollow tube: two marked edges with an unmarked
> strip down the middle.
"""

_R_CUT = """
**The job:** one file that cuts the outer profile and engraves the detail
inside it.

1. Paste and choose **Cut Outer + Engrave Inner**, or tick **Silhouette on CUT
   layer** in the Advanced box of any other preset.
2. The outer boundary goes to the **CUT** layer as a red hairline; everything
   inside stays on **ENGRAVE_FILL** / **ENGRAVE_LINE**.
3. Press `3` and check the layer colours: exactly one closed outer path should
   be red.
4. Export DXF, or SVG for LightBurn.

## At the machine

- LightBurn keys on colour, so the CUT geometry arrives on its own layer -
  assign cut power, and put it **after** the engrave in the layer order so you
  do not cut the part loose before you mark it.
- EZCAD2 ignores DXF layers. Export the cut and the engrave as two files, or
  separate them by hand after import.

> Watch for tabs. Nothing here adds holding tabs, so a fully cut profile drops
> free. If the part needs to stay in the sheet, add the tabs in your cam
> software.
"""

_R_DEPTH = """
**The job:** a brass medallion with a 0.3 mm relief, from a grey height map.

1. Paste the height map and choose **AUTO Depth (3D relief)** - or **Depth Coin
   / Medallion (brass)** to start from the coin recipe.
2. Read the note. You want "light border -> white is the surface, black = deep"
   and a sensible slice count. If it says the opposite of what your image means,
   flip **Convention**.
3. Set **Width** to the medallion diameter and **Total depth** to `0.3` mm.
4. Set **Material** to brass and enter your **Laser power**. If you have
   calibrated this material, type your measured **Removal (µm/pass)** -
   [how](topic:depth-calibrate).
5. Press `2` and look at the conditioned height map. Banding or terracing means
   the source has too few tones: raise **Smoothing (mm)** or lower the slice
   count.
6. Press `4` for the shaded slices. The rim should be a clean step and the
   relief should read as a shape, not as a pile of contours.
7. Add **Edge feather** of 0.05-0.1 mm so the rim is not a vertical cliff. Leave
   **Wall draft** at 8 deg.
8. Read the plan under the settings: slices × thickness, loops per slice,
   raster passes, estimated time. If the time is measured in days, reduce the
   depth.
9. Clear the warnings, especially `DEPTH_NARROW_FEATURES` (detail narrower than
   it is deep will never form a floor).
10. **Export depth pack...**, then follow [LightBurn](topic:depth-lightburn) or
    [EZCAD](topic:depth-ezcad).

> Do a calibration square in the same brass first. Everything in the plan is
> built on removal per pass, and an uncalibrated guess can be out by 2×.
"""

_R_RELIEF = """
**The job:** a photograph - a face, a pet, a building - as a bas-relief.

The hard part is that **luminance is not height**. A dark shadow under a chin is
not a deep pit; it is a lit surface facing away from the light. Slicing a photo
straight into depth gives you a relief of the lighting, not of the subject.

1. Paste and choose **Depth from Photo (bas-relief)** or **AUTO Depth** (which
   detects photo texture and switches this on itself).
2. **Photo to bas-relief** must be ticked. It compresses the large luminance
   gradients and re-integrates the result, so broad shapes survive and hard
   shadow edges stop being cliffs.
3. Tune **Relief strength**: higher compresses harder, which flattens the
   lighting but also flattens genuine form. Around 0.6 is a good start.
4. Expect **heavier smoothing** than a synthetic height map needs - a photo
   carries sensor noise and JPEG artefacts that would otherwise become texture
   in the metal.
5. Keep it **shallow**. 0.2-0.3 mm reads better than 0.5 mm on a face: deep
   photo reliefs exaggerate every artefact.
6. Use **Depth curve (gamma)** if the midtones look flat, and **Equalise tones**
   if one slice is doing almost all the work.
7. Judge it in view `4` and from the `_preview.png` in the pack, not from the
   height map - shading is the only way to see whether the face reads.

## Before you promise it to a customer

Prepare the photo first: crop tight, remove the background, even out the
lighting. Ten minutes of retouching beats every setting in this panel. Many
shops run an AI depth-estimation pass on the photo first and bring *that* in as
the height map, which sidesteps the luminance problem entirely.
"""

_TROUBLE = """
## The trace is wrong

| Symptom | Cause and fix |
|---|---|
| Nothing traced, `EMPTY` warning | Threshold is on the wrong side, or the art is white-on-dark. Add **Invert**, or nudge the threshold with `[` / `]`. |
| Detail missing that is clearly in the image | Look at view `2`. If it is missing from the **binary**, no trace setting can bring it back - fix the threshold. |
| Thousands of nodes on a simple shape | The tracer is chasing anti-aliasing or JPEG noise. Add **Kill JPEG blocks** or **Knock out grey halo**, then drag **Detail** left. |
| Wobbly edges on a low-res logo | **Upscale tiny logo** (4×), then raise **Smoothness**. |
| Counters filled in on text | Threshold too dark, or **Despeckle** / **Minimum feature** too large - see [Small text](topic:recipe-text). |
| Corners rounded off a QR code | **Smoothness** above 0 - see [QR](topic:recipe-qr). |
| Lines came out hollow | Outline instead of centerline - see [Line art](topic:recipe-lines). |

## The machine is unhappy

| Symptom | Cause and fix |
|---|---|
| The fill leaks past the outline in EZCAD | Open fill paths. Raise **Gap close** until `UNCLOSED_PATHS` clears. |
| Import is slow, the controller stutters | Node count. Watch **complexity** in Stats, lower Detail, raise Curve fit tolerance, set a node budget. |
| A dark seam where two shapes overlap | Double marking. Turn **Union touching fills** back on. |
| Tiny dots and blobs across the mark | Paths below the minimum feature. Turn on the **Tiny paths** overlay, then despeckle or scale the mark up. |
| The mark is the wrong size | The **Width** field, or a scale/fit option in the importing software. The export is in real mm. |

## Depth problems

| Symptom | Cause and fix |
|---|---|
| Visible terraces in the relief | Too few tones for the slice count. Raise **Smoothing (mm)**, use a 16-bit source, or cut the slices. |
| The relief comes out too shallow | The removal rate is a guess. [Calibrate it](topic:depth-calibrate) - this is almost always the answer. |
| It stops removing material partway down | Out of focus, or slag shielding the floor. Refocus every ~0.1 mm and add a clean pass every 5 slices. |
| Grooves cut into the floor | The hatch angle step divides into 180. Use **37 deg**. |
| Fine detail has no flat bottom | `DEPTH_NARROW_FEATURES` - the detail is narrower than it is deep and the walls converge. Reduce **Total depth** or enlarge the artwork. |
| The whole plate marks in slice 1 | No zero plane. Set it to `border` or raise the **Floor**. |

## Still stuck

Every export embeds the job settings, and `_plan.json` in a depth pack contains
the settings, the report and the warnings. Save the file that misbehaved
alongside the source image and you can reproduce it exactly.
"""

_ABOUT = """
**LaserTrace Pro 0.1.1** - a laser-first black and white vectorizer.

Built for a fiber laser shop: EZCAD2 / EZCAD3 DXF in millimetres as the primary
output, LightBurn SVG as the secondary one.

## How it is put together

- The tracing core is a plain Python library with no UI dependency. Every
  pipeline stage is a pure function with tests, so the same code runs behind
  this window, behind the command line and in the regression harness.
- The desktop shell is PySide6.
- The regression harness traces a fixture set on every change and checks node
  counts, hole counts and fidelity - the quality bar is a circle in 4 nodes, a
  rounded rectangle in 4 lines and 4 arcs, and bold text keeping every counter.
- **Potrace** is GPL-licensed and therefore runs only as a separate sidecar
  process. It is never linked into the application.

## Documentation

The repository carries the long-form documents this help summarises:
`docs/DEPTH_ENGRAVING.md` (the depth research and its sources),
`docs/LASER_REQUIREMENTS.md`, `docs/TRACE_STRATEGY.md`, `docs/PRESETS.md`,
`docs/EZCAD_VERIFY.md` and `docs/ARCHITECTURE.md`.

Press `F1` anywhere to come back here.
"""


SECTIONS: tuple[str, ...] = ("Getting started", "Core concepts", "Depth engraving", "Export & machine", "Quick guides", "About")

TOPICS: tuple[Topic, ...] = (
    Topic("overview", "What this app is for", "Getting started",
          "The jobs LaserTrace Pro exists to do, and why it is not Image Trace.", _OVERVIEW,
          ("purpose", "why", "logo", "intro", "start")),
    Topic("workflow", "The 60-second workflow", "Getting started",
          "Paste, check, size, export - and what to do when AUTO is wrong.", _WORKFLOW,
          ("steps", "how to", "first", "begin")),
    Topic("screen", "What's on screen", "Getting started",
          "Panels, the four views, the overlays and the split slider.", _SCREEN,
          ("view", "panel", "overlay", "binary", "wireframe", "simulation", "ui")),
    Topic("shortcuts", "Keyboard & mouse", "Getting started",
          "Every shortcut in one table.", _SHORTCUTS,
          ("keys", "hotkey", "shortcut", "mouse", "zoom")),

    Topic("presets-auto", "Presets and AUTO modes", "Core concepts",
          "What each AUTO mode measures, and what each shop preset is for.", _PRESETS,
          ("auto", "preset", "recipe", "classify")),
    Topic("preprocess", "The preprocess stack", "Core concepts",
          "Every image operation that runs before tracing, and the order it runs in.", _PREPROCESS,
          ("threshold", "denoise", "deskew", "invert", "morphology", "despeckle", "stack")),
    Topic("trace-settings", "Trace settings", "Core concepts",
          "The six sliders, plus every Advanced control.", _TRACE,
          ("detail", "smoothness", "corner", "threshold", "node budget", "gap", "engine", "potrace")),
    Topic("modes", "Outline vs centerline vs hybrid", "Core concepts",
          "Why a thin line traced as an outline marks a hollow tube.", _MODES,
          ("centerline", "outline", "hybrid", "stroke", "line art")),
    Topic("stats", "Reading the stats", "Core concepts",
          "Nodes, holes, fidelity, complexity - what each number is telling you.", _STATS,
          ("nodes", "holes", "fidelity", "iou", "complexity", "paths")),
    Topic("warnings", "Warnings and what to do", "Core concepts",
          "Every warning code, its meaning and its fix.", _WARNINGS,
          ("error", "warning", "unclosed", "tiny", "empty", "fallback")),

    Topic("depth-what", "Depth engraving explained", "Depth engraving",
          "Why depth comes from pass count, and how cumulative slices work.", _DEPTH_WHAT,
          ("3d", "relief", "slice", "cumulative", "height map", "depth")),
    Topic("depth-settings", "Every depth setting", "Depth engraving",
          "Slices, depth, material, conditioning, draft, hatch - control by control.", _DEPTH_SETTINGS,
          ("slices", "gamma", "equalise", "draft", "hatch", "pitch", "angle", "feather", "material")),
    Topic("depth-pack", "The depth pack", "Depth engraving",
          "What each exported file is and which controller wants it.", _DEPTH_PACK,
          ("export", "pack", "dxf", "heightmap", "plan", "png")),
    Topic("depth-lightburn", "Depth in LightBurn", "Depth engraving",
          "3D Sliced setup, and the vector-layer alternative.", _DEPTH_LB,
          ("lightburn", "3d sliced", "passes", "interval", "cleanup")),
    Topic("depth-ezcad", "Depth in EZCAD", "Depth engraving",
          "EZCAD2 per-slice hatching and EZCAD3 native depth maps.", _DEPTH_EZ,
          ("ezcad", "hatch", "loop count", "slices")),
    Topic("depth-calibrate", "Calibrate removal per pass", "Depth engraving",
          "The 10x10 mm test that makes every depth number true.", _DEPTH_CAL,
          ("calibrate", "removal", "um", "micron", "test", "measure")),

    Topic("export", "Exporting", "Export & machine",
          "Width, origin, curves, DXF version, layers and every export button.", _EXPORT,
          ("dxf", "svg", "origin", "layer", "r12", "spline", "width", "compare")),
    Topic("lightburn", "Getting vectors into LightBurn", "Export & machine",
          "Copy-paste SVG, and how to set the layers up.", _LIGHTBURN,
          ("lightburn", "svg", "paste", "colour", "fill", "line")),
    Topic("ezcad", "Getting vectors into EZCAD", "Export & machine",
          "What EZCAD needs from the file, and what it ignores.", _EZCAD,
          ("ezcad", "dxf", "hatch", "r2000", "closed")),

    Topic("recipe-logo", "Logo on a tumbler", "Quick guides",
          "Customer JPEG to a 38 mm filled mark.", _R_LOGO,
          ("logo", "fill", "tumbler", "customer")),
    Topic("recipe-text", "Small text and serial plates", "Quick guides",
          "Keeping counters open at 2 mm cap height.", _R_TEXT,
          ("text", "serial", "plate", "counter", "serif", "small")),
    Topic("recipe-qr", "QR and Data Matrix", "Quick guides",
          "Square modules that actually scan.", _R_QR,
          ("qr", "data matrix", "code", "scan", "module")),
    Topic("recipe-photo", "Phone photo of a logo", "Quick guides",
          "Deskew, flatten the lighting, threshold locally.", _R_PHOTO,
          ("photo", "scan", "phone", "sauvola", "deskew")),
    Topic("recipe-lines", "Line art and signatures", "Quick guides",
          "One stroke per line instead of a hollow tube.", _R_LINES,
          ("signature", "line art", "centerline", "drawing", "sketch")),
    Topic("recipe-cut", "Cut outer, engrave inner", "Quick guides",
          "One file, two operations.", _R_CUT,
          ("cut", "engrave", "silhouette", "layer")),
    Topic("recipe-depth", "Brass medallion relief", "Quick guides",
          "Height map to a 0.3 mm coin relief, step by step.", _R_DEPTH,
          ("coin", "medallion", "brass", "relief", "depth", "3d")),
    Topic("recipe-relief", "Photo to bas-relief", "Quick guides",
          "Why luminance is not height, and what to do about it.", _R_RELIEF,
          ("photo", "relief", "face", "portrait", "bas relief", "depth")),
    Topic("troubleshooting", "Troubleshooting", "Quick guides",
          "Symptom, cause, fix - for the trace, the machine and depth jobs.", _TROUBLE,
          ("problem", "fix", "wrong", "broken", "help", "bug")),

    Topic("about", "About LaserTrace Pro", "About",
          "Version, architecture and where the long-form documents live.", _ABOUT,
          ("version", "license", "credits", "docs", "potrace")),
)

TOPICS_BY_ID: dict[str, Topic] = {t.id: t for t in TOPICS}

#: Topics offered as "quick guides" in the Help menu, in menu order.
QUICK_GUIDES: tuple[str, ...] = (
    "recipe-logo", "recipe-text", "recipe-qr", "recipe-photo", "recipe-lines",
    "recipe-cut", "recipe-depth", "recipe-relief", "troubleshooting",
)

#: Which topic a given UI area's "?" button opens.
CONTEXT_HELP: dict[str, str] = {
    "preset": "presets-auto",
    "stack": "preprocess",
    "trace": "trace-settings",
    "depth": "depth-what",
    "stats": "stats",
    "warnings": "warnings",
    "export": "export",
}


def search(query: str) -> list[Topic]:
    return [t for t in TOPICS if t.matches(query)]


# --------------------------------------------------------------------------- rendering

_INLINE_LINK = re.compile(r"\[([^\]]+)\]\((topic:[a-z0-9-]+)\)")
_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_INLINE_ITAL = re.compile(r"(?<![\w*])\*([^*]+?)\*(?!\w)")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Escape, then apply the inline markup. Order matters: escape first."""
    out = html.escape(text)
    out = _INLINE_CODE.sub(r'<code style="background:#2a2c31;color:#e8c08a;">&nbsp;\1&nbsp;</code>', out)
    out = _INLINE_BOLD.sub(r"<b>\1</b>", out)
    out = _INLINE_ITAL.sub(r"<i>\1</i>", out)
    # links were escaped along with everything else; match on the escaped form
    out = _INLINE_LINK.sub(rf'<a href="\2" style="color:{ACCENT};">\1</a>', out)
    return out


def _table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    cells = "".join(f'<th align="left" style="background:#2a2c31;">{_inline(c)}</th>' for c in head)
    out = [f'<table width="100%" border="1" cellspacing="0" cellpadding="6" '
           f'style="border-color:#3a3d42;"><tr>{cells}</tr>']
    for r in body:
        out.append("<tr>" + "".join(f'<td valign="top">{_inline(c)}</td>' for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_divider(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s|:-]+\|", line.strip()))


def to_html(body: str) -> str:
    """Render the Markdown subset used by the topics above."""
    lines = body.strip("\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("### "):
            out.append(f'<h3 style="color:#d8dce4;margin-top:16px;">{_inline(stripped[4:])}</h3>')
            i += 1
        elif stripped.startswith("## "):
            out.append(f'<h2 style="color:{ACCENT};margin-top:20px;">{_inline(stripped[3:])}</h2>')
            i += 1
        elif stripped.startswith("|"):
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                if not _is_divider(lines[i]):
                    rows.append(_split_row(lines[i]))
                i += 1
            out.append(_table(rows))
        elif stripped.startswith("> "):
            note: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                note.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f'<table width="100%" border="0" cellspacing="0" cellpadding="8">'
                       f'<tr><td style="background:#26282d;">'
                       f'<span style="color:{ACCENT};">&#9679;&nbsp;</span>{_inline(" ".join(note))}</td></tr></table>')
        elif stripped.startswith("- "):
            items: list[str] = []
            while i < n and lines[i].strip().startswith("- "):
                items.append(_wrapped_item(lines, i, "- "))
                i = _item_end(lines, i)
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
        elif re.match(r"\d+\.\s", stripped):
            items = []
            while i < n and re.match(r"\d+\.\s", lines[i].strip()):
                items.append(_wrapped_item(lines, i, None))
                i = _item_end(lines, i)
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
        else:
            para: list[str] = [stripped]
            i += 1
            while i < n and lines[i].strip() and not _starts_block(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f'<p>{_inline(" ".join(para))}</p>')
    return "".join(out)


def _starts_block(line: str) -> bool:
    s = line.strip()
    return s.startswith(("#", "|", "> ", "- ")) or bool(re.match(r"\d+\.\s", s))


def _item_end(lines: list[str], start: int) -> int:
    """Index after a list item, swallowing its indented continuation lines."""
    i = start + 1
    while i < len(lines) and lines[i].startswith("  ") and lines[i].strip() and not _starts_block(lines[i]):
        i += 1
    return i


def _wrapped_item(lines: list[str], start: int, marker: str | None) -> str:
    end = _item_end(lines, start)
    first = lines[start].strip()
    first = first[len(marker):] if marker else re.sub(r"^\d+\.\s+", "", first)
    parts = [first] + [lines[j].strip() for j in range(start + 1, end)]
    return _inline(" ".join(parts))


def render(topic: Topic) -> str:
    """Full HTML document for one topic."""
    return (
        f'<body style="font-family:Segoe UI,sans-serif;font-size:13px;color:#e6e6e6;">'
        f'<h1 style="color:#e6e6e6;margin-bottom:2px;">{html.escape(topic.title)}</h1>'
        f'<p style="color:#9aa0aa;margin-top:0;">{html.escape(topic.summary)}</p>'
        f'{to_html(topic.body)}'
        f'</body>'
    )
