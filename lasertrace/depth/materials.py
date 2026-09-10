"""Starting-point machine numbers for deep / relief engraving on a fiber galvo.

Every row is a *measured shop recipe* (or the median of several) rather than
a vendor marketing figure; see docs/DEPTH_ENGRAVING.md "Sources" for the
threads and guides each one comes from. Removal per pass is normalised to
the source power the recipe was run on (`ref_w`) and scaled sub-linearly by
`DepthSettings.laser_w` (ablation is not proportional to average power).

Always calibrate: engrave a 10 x 10 mm square for 10-20 passes on the real
part, measure the depth, divide by the pass count, and put that number in
`removal_per_pass_um`. Focus, assist air, alloy and pitch move these by 2x.

Field meanings (per single full hatch pass at `spacing_mm`):
    removal_um     material removed per pass (micrometres) at ref_w
    power_pct      laser power
    speed_mm_s     mark speed
    freq_khz       pulse frequency; for depth stay near the source's own
                   maximum-pulse-energy rate (JPT M7 ~60-100 kHz, Raycus
                   Q-switched 20-40 kHz) rather than an absolute "low" value
    pulse_ns       pulse width (MOPA only); 200-250 ns removes most, 500 ns for copper
    spacing_mm     hatch line pitch (pulse overlap ~50 %: 0.02-0.03 mm on a 40-50 um spot)
    clean          finishing pass to lift dross / oxide between roughing slices
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

POWER_SCALING_EXPONENT = 0.75


@dataclass(frozen=True)
class MaterialParams:
    key: str
    name: str
    removal_um: float
    ref_w: float
    power_pct: float
    speed_mm_s: float
    freq_khz: float
    pulse_ns: float | None
    spacing_mm: float
    clean: str
    notes: str = ""
    sources: tuple[str, ...] = field(default_factory=tuple)

    def scaled_removal_um(self, laser_w: float | None) -> float:
        """Removal per pass scaled with (laser_w / ref_w) ** 0.75, clamped to 0.25x..3x."""
        if not laser_w or laser_w <= 0:
            return self.removal_um
        f = max(0.25, min(3.0, (laser_w / self.ref_w) ** POWER_SCALING_EXPONENT))
        return self.removal_um * f

    def as_dict(self) -> dict:
        d = asdict(self)
        d["sources"] = list(self.sources)
        return d


MATERIALS: dict[str, MaterialParams] = {
    "stainless": MaterialParams(
        key="stainless", name="Stainless steel (304/316)", removal_um=15.0, ref_w=60.0,
        power_pct=95, speed_mm_s=950, freq_khz=48, pulse_ns=200, spacing_mm=0.025,
        clean="20% / 2000 mm/s / 100 kHz, 1 pass after every 5 slices",
        notes="Slow and 'grabby': ejected melt re-fuses, so keep air across the part and never skip the clean pass. Reported 10-30 um/pass at 60 W.",
        sources=("omglaser.com 60W MOPA slide guide", "omglaser.com depth-per-pass", "barchlaser.com stainless carving"),
    ),
    "mild_steel": MaterialParams(
        key="mild_steel", name="Mild / carbon steel", removal_um=20.0, ref_w=60.0,
        power_pct=100, speed_mm_s=1500, freq_khz=35, pulse_ns=200, spacing_mm=0.03,
        clean="25% / 2000 mm/s / 100 kHz every 5 slices",
        notes="0.2-0.4 mm after 8-20 cross-hatched passes at full power is typical.",
        sources=("lasermarktech.com engraving guide",),
    ),
    "tool_steel": MaterialParams(
        key="tool_steel", name="Tool / hardened steel", removal_um=10.0, ref_w=60.0,
        power_pct=100, speed_mm_s=800, freq_khz=40, pulse_ns=200, spacing_mm=0.025,
        clean="25% / 2000 mm/s / 100 kHz every 5 slices",
        notes="Hard alloys ablate slowly; budget 1.5x the stainless time.",
    ),
    "brass": MaterialParams(
        key="brass", name="Brass", removal_um=15.0, ref_w=60.0,
        power_pct=90, speed_mm_s=2000, freq_khz=60, pulse_ns=200, spacing_mm=0.025,
        clean="30% / 2000 mm/s / 80 kHz every 5-10 slices (brightens the floor)",
        notes="The classic coin material: 256-512 passes at 2000 mm/s give 0.3-0.5 mm relief in 1.5-2.5 h. Reported frequencies range 45-100 kHz on MOPA sources.",
        sources=("blog.commarker.com brass coin", "omglaser.com settings database"),
    ),
    "aluminum": MaterialParams(
        key="aluminum", name="Aluminium (6061 / 5052)", removal_um=12.0, ref_w=20.0,
        power_pct=70, speed_mm_s=800, freq_khz=43, pulse_ns=200, spacing_mm=0.025,
        clean="30% / 3000 mm/s / 100 kHz every 5 slices",
        notes="Fastest common metal: 3 mm in ~256 passes on 20 W (5.5 h). Soft floor gets rough, so fewer, deeper slices work better.",
        sources=("blog.commarker.com aluminium deep engraving",),
    ),
    "titanium": MaterialParams(
        key="titanium", name="Titanium", removal_um=8.0, ref_w=60.0,
        power_pct=90, speed_mm_s=1000, freq_khz=40, pulse_ns=200, spacing_mm=0.025,
        clean="30% / 2000 mm/s / 100 kHz every 5 slices",
        notes="Oxide colours strongly; the clean pass is what makes the relief readable. Very deep work (2 mm) has been reported at >2500 passes.",
        sources=("forum.lightburnsoftware.com 3D sliced limitations thread",),
    ),
    "copper": MaterialParams(
        key="copper", name="Copper", removal_um=12.5, ref_w=60.0,
        power_pct=90, speed_mm_s=500, freq_khz=30, pulse_ns=500, spacing_mm=0.02,
        clean="30% / 2000 mm/s / 100 kHz every 5 slices",
        notes="Reflective and conductive: long pulses, low frequency, 50 W+; refocus about every 1 mm of depth (3 mm through in 240 passes reported).",
        sources=("omglaser.com settings database (copper)",),
    ),
    "generic": MaterialParams(
        key="generic", name="Generic metal (calibrate on a coupon)", removal_um=15.0, ref_w=60.0,
        power_pct=100, speed_mm_s=1500, freq_khz=35, pulse_ns=200, spacing_mm=0.025,
        clean="25% / 2000 mm/s / 100 kHz every 5 slices",
        notes="Placeholder: engrave a 10 x 10 mm square for 10 passes, measure, divide by 10 and enter that as removal per pass.",
    ),
}


def material_params(key: str) -> MaterialParams:
    return MATERIALS.get(key) or MATERIALS["generic"]


def material_keys() -> list[str]:
    return list(MATERIALS.keys())
