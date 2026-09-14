"""Data models for LaserTrace Pro.

All models are pydantic v2 and JSON-serializable so a Job (image + preprocess
stack + trace settings + export profile) can be saved next to the customer
artwork and replayed deterministically.

Geometry units: after the trace stage every coordinate is in **millimetres**,
y-down, origin at the top-left of the traced bounding box (see
docs/ARCHITECTURE.md "Coordinate systems").
"""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

Point = tuple[float, float]


# --------------------------------------------------------------------------- #
# Layers / roles
# --------------------------------------------------------------------------- #
class Layer(str, Enum):
    ENGRAVE_FILL = "ENGRAVE_FILL"   # black filled region -> laser hatch/fill
    ENGRAVE_LINE = "ENGRAVE_LINE"   # single-pass line (centerline output)
    CUT = "CUT"                     # red hairline
    SCORE = "SCORE"                 # blue
    IGNORE = "IGNORE"               # gray construction


DEFAULT_LAYER_COLORS: dict[str, str] = {
    Layer.ENGRAVE_FILL.value: "#000000",
    Layer.ENGRAVE_LINE.value: "#00A000",
    Layer.CUT.value: "#FF0000",
    Layer.SCORE.value: "#0000FF",
    Layer.IGNORE.value: "#808080",
}

# DXF ACI colour indices per layer (ACI 1=red, 3=green, 5=blue, 7=white/black, 8=gray)
DEFAULT_LAYER_ACI: dict[str, int] = {
    Layer.ENGRAVE_FILL.value: 7,
    Layer.ENGRAVE_LINE.value: 3,
    Layer.CUT.value: 1,
    Layer.SCORE.value: 5,
    Layer.IGNORE.value: 8,
}

# Depth (3D relief) slices are exported as DEPTH_01 .. DEPTH_NN layers, one per
# cumulative pass. LightBurn maps DXF layers by name/colour (up to 30 layers),
# EZCAD imports layer names as object names. ACI colours cycle through a
# palette that stays visually distinct in both programs (never 7 = black/white).
DEPTH_LAYER_PREFIX = "DEPTH_"
DEPTH_HATCH_SUFFIX = "_HATCH"
DEPTH_ACI_PALETTE = [1, 2, 3, 4, 5, 6, 30, 40, 50, 70, 90, 110, 130, 150, 170, 190, 210, 230, 12, 22, 32, 42, 52, 62, 72, 82, 92, 102, 112, 122]
MAX_DEPTH_LEVELS = 60


def depth_layer_name(index: int, hatch: bool = False) -> str:
    """Layer name for depth slice `index` (1 = shallowest / first pass)."""
    return f"{DEPTH_LAYER_PREFIX}{index:02d}{DEPTH_HATCH_SUFFIX if hatch else ''}"


def depth_layer_aci(index: int) -> int:
    return DEPTH_ACI_PALETTE[(index - 1) % len(DEPTH_ACI_PALETTE)]


# --------------------------------------------------------------------------- #
# Source image
# --------------------------------------------------------------------------- #
class SourceImage(BaseModel):
    path: Optional[str] = None
    sha256: str
    width_px: int
    height_px: int
    dpi: Optional[float] = None
    mode: str = "RGB"
    exif_orientation: int = 1
    origin: Literal["file", "clipboard", "bytes"] = "file"
    bit_depth: int = 8                      # bits per channel in the file (16 = 16-bit grayscale)
    # how transparency was flattened: none (opaque), on_white (composited on white),
    # alpha_ink (one-colour art: shape taken from alpha),
    # light_inverted (light flat-colour art with no dark detail -> inverted to dark ink)
    alpha_policy: Literal["none", "on_white", "alpha_ink", "light_inverted"] = "none"

    @property
    def est_width_mm(self) -> Optional[float]:
        if not self.dpi:
            return None
        return self.width_px / self.dpi * 25.4


# --------------------------------------------------------------------------- #
# Preprocess stack
# --------------------------------------------------------------------------- #
PreprocessOpName = Literal[
    "crop", "rotate", "deskew",
    "background_white", "background_sample",
    "upscale", "denoise", "bilateral", "unsharp", "levels", "gamma",
    "threshold", "invert", "knockout_halo",
    "morph_open", "morph_close", "dilate", "erode",
    "despeckle", "fill_holes",
    "quantize",
]


class PreprocessOp(BaseModel):
    op: PreprocessOpName
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class PreprocessStack(BaseModel):
    ops: list[PreprocessOp] = Field(default_factory=list)

    def enabled_ops(self) -> list[PreprocessOp]:
        return [o for o in self.ops if o.enabled]

    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]

    def find(self, op: str) -> Optional[PreprocessOp]:
        for o in self.ops:
            if o.op == op:
                return o
        return None


# --------------------------------------------------------------------------- #
# Trace settings
# --------------------------------------------------------------------------- #
class SnapSettings(BaseModel):
    enabled: bool = False
    lines_hv45: bool = True
    circles: bool = True
    rounded_rects: bool = True
    parallel: bool = False
    dedupe_shapes: bool = False
    angle_tol_deg: float = 2.0
    radius_tol_mm: float = 0.05


class TraceSettings(BaseModel):
    engine: Literal["contour", "potrace", "vtracer", "centerline"] = "contour"
    mode: Literal["outline", "centerline", "hybrid"] = "outline"

    # --- primary shop sliders (0..1 unless noted) ---
    detail: float = 0.5            # detail vs cleanliness
    threshold: int = 128           # darkness (0..255); used by manual threshold op
    despeckle_mm2: float = 0.02    # remove islands smaller than this area (mm^2)
    corner_sharpness: float = 0.6  # 0 = round everything, 1 = keep every corner
    min_feature_mm: float = 0.1    # smallest feature the laser can reproduce
    smoothness: float = 0.5        # curve fit aggressiveness

    # --- advanced ---
    curve_tol_mm: float = 0.03     # max fit error in mm
    gap_close_mm: float = 0.05     # close paths whose endpoints are closer than this
    node_budget: Optional[int] = None
    union_fills: bool = True
    preserve_holes: bool = True
    centerline_max_width_mm: float = 0.6  # strokes thinner than this become centerlines in hybrid
    snap: SnapSettings = Field(default_factory=SnapSettings)
    cut_outer: bool = False        # silhouette -> CUT layer, interior -> ENGRAVE

    @field_validator("threshold")
    @classmethod
    def _t(cls, v: int) -> int:
        return max(0, min(255, int(v)))


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
class Line(BaseModel):
    kind: Literal["line"] = "line"
    p1: Point
    p2: Point


class Cubic(BaseModel):
    kind: Literal["cubic"] = "cubic"
    p0: Point
    c1: Point
    c2: Point
    p3: Point


class Arc(BaseModel):
    kind: Literal["arc"] = "arc"
    center: Point
    r: float
    a0: float   # radians
    a1: float   # radians
    ccw: bool = True

    @property
    def p_start(self) -> Point:
        return (self.center[0] + self.r * math.cos(self.a0), self.center[1] + self.r * math.sin(self.a0))

    @property
    def p_end(self) -> Point:
        return (self.center[0] + self.r * math.cos(self.a1), self.center[1] + self.r * math.sin(self.a1))


Segment = Annotated[Union[Line, Cubic, Arc], Field(discriminator="kind")]


def seg_start(s: Line | Cubic | Arc) -> Point:
    if isinstance(s, Line):
        return s.p1
    if isinstance(s, Cubic):
        return s.p0
    return s.p_start


def seg_end(s: Line | Cubic | Arc) -> Point:
    if isinstance(s, Line):
        return s.p2
    if isinstance(s, Cubic):
        return s.p3
    return s.p_end


class Subpath(BaseModel):
    segments: list[Segment] = Field(default_factory=list)
    closed: bool = True
    is_hole: bool = False

    def node_count(self) -> int:
        return len(self.segments)

    def start(self) -> Point:
        return seg_start(self.segments[0])

    def end(self) -> Point:
        return seg_end(self.segments[-1])


class Path(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    subpaths: list[Subpath] = Field(default_factory=list)
    layer: Layer = Layer.ENGRAVE_FILL
    fill_rule: Literal["evenodd", "nonzero"] = "evenodd"
    stroke_width_mm: Optional[float] = None   # for ENGRAVE_LINE from centerline mode
    depth_index: Optional[int] = None         # 1..N for depth (3D relief) slices; None = flat artwork

    def layer_name(self) -> str:
        """Export layer name: the Layer value, or DEPTH_nn(_HATCH) for depth slices."""
        if self.depth_index is not None:
            return depth_layer_name(self.depth_index, hatch=self.layer == Layer.ENGRAVE_LINE)
        return self.layer.value

    def node_count(self) -> int:
        return sum(sp.node_count() for sp in self.subpaths)

    def is_closed(self) -> bool:
        return all(sp.closed for sp in self.subpaths)

    def holes(self) -> int:
        return sum(1 for sp in self.subpaths if sp.is_hole)


class PathGraph(BaseModel):
    paths: list[Path] = Field(default_factory=list)
    units: Literal["mm", "px"] = "mm"
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # minx, miny, maxx, maxy
    px_per_mm: Optional[float] = None

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def node_count(self) -> int:
        return sum(p.node_count() for p in self.paths)

    def depth_levels(self) -> int:
        """Number of depth slices present (0 for flat artwork)."""
        return max((p.depth_index or 0) for p in self.paths) if self.paths else 0


# --------------------------------------------------------------------------- #
# Depth (3D relief) engraving settings
# --------------------------------------------------------------------------- #
class DepthHatchSettings(BaseModel):
    """Optional in-app hatch generation per slice (for controllers that cannot
    rotate the hatch angle per pass themselves, or to lock the fill pattern)."""
    enabled: bool = False
    spacing_mm: float = 0.025           # line pitch: ~50% pulse overlap on a 40-50 um spot (shops converge on 0.02-0.025)
    angle_start_deg: float = 0.0
    angle_step_deg: float = 37.0        # rotate per slice; never a divisor of 180 (0/30/45/60/90) or ridges stack into grooves
    bidirectional: bool = True          # serpentine order (no jump back per line)
    contour_pass: bool = True           # one outline per slice inset by spacing/2: crisp walls
    max_lines: int = 250000             # safety cap for the whole job


class DepthSettings(BaseModel):
    """Grayscale height map -> N cumulative slices.

    Height convention inside the pipeline: 0.0 = untouched surface,
    1.0 = full `total_depth_mm`. Slice i (1..levels) is the region deeper than
    (i - 0.5) / levels, so a pixel's pass count rounds its height and the
    deepest pixels are engraved in every pass (that is what removes material
    correctly: each pass takes roughly the same thickness off wherever it hits)."""
    enabled: bool = False
    levels: int = 16                    # number of slices / passes
    dark_is_deep: bool = True           # image convention: black = deepest (LightBurn default); False = white = deepest (height/bump maps)
    total_depth_mm: float = 0.5         # depth at level 1.0 (deepest pixels)
    material: str = "stainless"         # key in depth.materials.MATERIALS (starting-point machine numbers)
    laser_w: float = 50.0               # average power of the source; scales the material removal rate
    removal_per_pass_um: Optional[float] = None   # override the material's removal per pass (micrometres)
    passes_per_slice: Optional[int] = None        # override: loop count per slice; None = derived from removal rate
    z_step: bool = True                 # focus follow: step the Z axis down per slice (depth is the nominal per-slice thickness)
    z_step_every_mm: float = 0.1        # only issue a Z step once the accumulated depth exceeds this (small focal tolerance)

    # --- height map conditioning ---
    normalize: bool = True              # stretch levels so the darkest/lightest ink hits 0/1
    black_point: int = 0                # levels clamp on the 8-bit gray before normalising
    white_point: int = 255
    depth_gamma: float = 1.0            # h' = h ** gamma (>1 keeps more area shallow; <1 deepens midtones)
    equalize: float = 0.0               # 0..1 blend towards histogram-equalised heights (every slice removes similar area)
    smoothing_mm: float = 0.05          # bilateral/gaussian sigma in mm: removes staircase and JPEG noise
    edge_preserve: bool = True          # bilateral (keeps walls crisp) instead of gaussian
    zero_plane: Literal["none", "border", "min"] = "border"  # what counts as the untouched surface
    floor: float = 0.02                 # heights below this fraction are clamped to 0 (background noise)
    photo_to_relief: bool = False       # convert a photo's luminance into a plausible bas-relief (gradient compression)
    relief_strength: float = 0.6        # 0..1 compression of large gradients when photo_to_relief is on
    feather_mm: float = 0.0             # soften the silhouette edge by this much (avoids a vertical cliff at the outline)

    # --- slice hygiene ---
    min_island_mm2: float = 0.01        # islands / pits smaller than this are dropped per slice (~(2 x hatch pitch)^2)
    draft_angle_deg: float = 8.0        # inset every wall by depth * tan(draft): chamfered walls, no fragile undercut lip
    quantize_png: bool = False          # bake the N slices into the exported height PNG (True = PNG passes must equal `levels`)
    png_bits: Literal[8, 16] = 8        # 16-bit for LightBurn 3D Sliced with > 256 passes; 8-bit for EZCAD
    png_black_is_deep: bool = True      # convention of the exported height image (LightBurn 3D Sliced / EZCAD3 depth map: black = deepest)
    hatch: DepthHatchSettings = Field(default_factory=DepthHatchSettings)

    @field_validator("levels")
    @classmethod
    def _levels(cls, v: int) -> int:
        return max(1, min(MAX_DEPTH_LEVELS, int(v)))


class DepthSlice(BaseModel):
    """Operator-facing description of one cumulative pass."""
    index: int                      # 1 = first / shallowest
    threshold: float                # height fraction (0..1) this slice covers (>=)
    depth_from_mm: float            # depth of the surface this pass starts on
    depth_to_mm: float              # nominal depth after this pass
    z_offset_mm: float              # focus offset to apply before this pass (negative = down into the part)
    passes: int                     # loop count to remove depth_to - depth_from at the material rate
    hatch_angle_deg: float
    area_mm2: float
    paths: int
    nodes: int
    hatch_length_mm: float = 0.0
    est_time_s: float = 0.0


class DepthReport(BaseModel):
    levels: int
    total_depth_mm: float
    slice_thickness_mm: float
    material: str
    removal_per_pass_um: float
    footprint_mm2: float
    volume_mm3: float
    est_time_s: float
    height_range: tuple[float, float] = (0.0, 1.0)   # min/max normalised height inside the footprint
    slices: list[DepthSlice] = Field(default_factory=list)
    machine: dict[str, Any] = Field(default_factory=dict)   # starting-point parameters (power, speed, frequency, ...)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stats / warnings / results
# --------------------------------------------------------------------------- #
class Warning(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warn", "error"] = "warn"
    path_ids: list[str] = Field(default_factory=list)


class Stats(BaseModel):
    paths: int = 0
    nodes: int = 0
    subpaths: int = 0
    open_paths: int = 0
    closed_paths: int = 0
    holes: int = 0
    fill_area_mm2: float = 0.0
    bbox_mm: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    width_mm: float = 0.0
    height_mm: float = 0.0
    complexity: float = 0.0          # 0..100; nodes-per-area proxy for mark time
    est_travel_mm: float = 0.0       # proxy for galvo travel
    iou_vs_binary: Optional[float] = None
    holes_in_binary: Optional[int] = None
    time_ms: dict[str, float] = Field(default_factory=dict)


class TraceResult(BaseModel):
    graph: PathGraph
    stats: Stats
    warnings: list[Warning] = Field(default_factory=list)
    binary_sha: str = ""
    engine: str = "contour"
    preset_name: Optional[str] = None
    depth: Optional[DepthReport] = None


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class ExportProfile(BaseModel):
    format: Literal["dxf", "svg", "plt", "pdf", "eps", "png"] = "dxf"
    dxf_version: Literal["R12", "R2000"] = "R2000"
    width_mm: Optional[float] = 50.0
    height_mm: Optional[float] = None       # None = keep aspect
    origin: Literal["top_left", "bottom_left", "center"] = "bottom_left"
    flatten_layers: bool = False
    curves: Literal["polyline", "spline", "arcs"] = "polyline"
    flatten_tol_mm: float = 0.02
    precision: int = 3
    close_fills: bool = True
    strokes_to_fills: bool = False
    stroke_outline_width_mm: float = 0.1
    layer_colors: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_LAYER_COLORS))
    layer_aci: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_LAYER_ACI))
    cut_hairline_mm: float = 0.01
    png_dpi: int = 600
    embed_preset: bool = True


# --------------------------------------------------------------------------- #
# Job / preset
# --------------------------------------------------------------------------- #
class Preset(BaseModel):
    name: str
    display_name: str
    description: str = ""
    stack: PreprocessStack = Field(default_factory=PreprocessStack)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    export: ExportProfile = Field(default_factory=ExportProfile)
    depth: DepthSettings = Field(default_factory=DepthSettings)
    tags: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: Optional[SourceImage] = None
    preset_name: Optional[str] = None
    stack: PreprocessStack = Field(default_factory=PreprocessStack)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    export: ExportProfile = Field(default_factory=ExportProfile)
    depth: DepthSettings = Field(default_factory=DepthSettings)
    notes: str = ""

    @classmethod
    def from_preset(cls, preset: Preset, source: Optional[SourceImage] = None) -> "Job":
        return cls(
            source=source,
            preset_name=preset.name,
            stack=preset.stack.model_copy(deep=True),
            trace=preset.trace.model_copy(deep=True),
            export=preset.export.model_copy(deep=True),
            depth=preset.depth.model_copy(deep=True),
        )

    def settings_fingerprint(self) -> str:
        payload = self.stack.model_dump_json() + self.trace.model_dump_json() + self.depth.model_dump_json()
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
