"""Optional path ordering to reduce galvo travel (nearest-neighbour)."""
from __future__ import annotations

import math

from ..models import PathGraph


def order_paths_nearest(graph: PathGraph) -> float:
    """Reorder graph.paths greedily from the origin. Returns total jump length (mm)."""
    remaining = list(graph.paths)
    if not remaining:
        return 0.0
    ordered = []
    cur = (0.0, 0.0)
    total = 0.0
    while remaining:
        best_i, best_d = 0, float("inf")
        for i, p in enumerate(remaining):
            if not p.subpaths or not p.subpaths[0].segments:
                continue
            s = p.subpaths[0].start()
            d = math.hypot(s[0] - cur[0], s[1] - cur[1])
            if d < best_d:
                best_i, best_d = i, d
        p = remaining.pop(best_i)
        ordered.append(p)
        if p.subpaths and p.subpaths[0].segments:
            total += best_d if best_d != float("inf") else 0.0
            cur = p.subpaths[-1].end() if not p.subpaths[-1].closed else p.subpaths[0].start()
    graph.paths = ordered
    return total
