"""Depth (3D relief) engraving: gray height map -> cumulative slices -> layered vectors.

    heightmap.py  gray -> conditioned float height field (0 = surface, 1 = deepest)
    slicer.py     height field -> N nested masks -> PathGraph with depth_index per path
    hatch.py      per-slice hatch lines with a rotating angle (optional)
    plan.py       operator pass plan: depth, Z offset, loop count, angle, time
    materials.py  starting-point machine numbers per material
"""
from .heightmap import conditioned_height, quantize_height, height_to_image, photo_to_relief
from .slicer import slice_thresholds, slice_masks, trace_slices
from .hatch import hatch_polygon, add_hatch_paths
from .plan import build_report
from .materials import MATERIALS, material_params

__all__ = [
    "conditioned_height", "quantize_height", "height_to_image", "photo_to_relief",
    "slice_thresholds", "slice_masks", "trace_slices",
    "hatch_polygon", "add_hatch_paths",
    "build_report", "MATERIALS", "material_params",
]
