"""Laser-critical path hygiene. Runs on a PathGraph in mm after tracing."""
from .clean import remove_micro_paths, close_gaps, dedupe_subpaths, join_open_chains
from .simplify import merge_collinear_lines, transform_graph, scale_graph
from .topology import ensure_winding, union_overlapping_fills, add_cut_outer, detect_overlaps

__all__ = [
    "remove_micro_paths", "close_gaps", "dedupe_subpaths", "join_open_chains",
    "merge_collinear_lines", "transform_graph", "scale_graph",
    "ensure_winding", "union_overlapping_fills", "add_cut_outer", "detect_overlaps",
]
