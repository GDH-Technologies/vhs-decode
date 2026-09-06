"""Per-field picture metrics measured on the written TBC field.

numpy only and import-light: shared by the ld-decode, vhs-decode and
cvbs-decode writers and by their tests. The formulas are a port of
tbc-tools' tbc-segments ``fieldmetrics.cpp`` (``measureField`` and
``sameParityDifferenceIre``) so a value the decoder wrote and a value a
later ``--tbc`` walk measures agree to rounding:

- IRE = (sample16 - black16) / ((white16 - black16) / 100), with the three
  levels rounded to integers the way the library reads them.
- Every region is subsampled: every ``line_step`` active line and every
  ``sample_step`` sample (the burst reads every sample; it is only a few
  cycles wide).
- Standard deviations are population (sqrt(E[x^2] - m^2)).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# tbc-tools' library defaults for the active field lines (tbcmetadata.cpp):
# 1-based, half-open [first, last). They are not decoder output, so the
# decoder applies the same table the library would apply to its JSON.
ACTIVE_FIELD_LINES = {"NTSC": (20, 263), "PAL": (22, 308)}

# Line-count family per system name (decoder spellings included). Systems
# missing here have no library default and get no metrics.
_LINE_FAMILY = {
    "NTSC": "NTSC",
    "PAL-M": "NTSC",
    "PAL_M": "NTSC",
    "PALM": "NTSC",
    "MPAL": "NTSC",
    "NLINHA": "NTSC",
    "PAL": "PAL",
    "SECAM": "PAL",
    "MESECAM": "PAL",
}

# Non-standard line counts: no active-line default, metrics disabled.
DISABLED_SYSTEMS = {"405", "819"}

# Nominal sync tip level relative to blanking, per line family.
SYNC_TIP_IRE = {"NTSC": -40.0, "PAL": -300.0 / 7.0}

SAMPLE_STEP = 4
LINE_STEP = 2

METRIC_KEYS = (
    "blankingDevIre",
    "burstAmpIre",
    "fieldDiffIre",
    "lumaMeanIre",
    "noiseIre",
    "syncTipDevIre",
)


@dataclass(frozen=True)
class Geometry:
    """The geometry a field measurement needs, lifted from videoParameters once."""

    field_width: int
    field_height: int
    active_video_start: int  # samples, half-open [start, end)
    active_video_end: int
    first_active_field_line: int  # 1-based field lines, half-open [first, last)
    last_active_field_line: int
    colour_burst_start: int  # samples
    colour_burst_end: int
    black16: float
    white16: float
    blanking16: float
    sync_tip_ire: float
    sample_step: int = SAMPLE_STEP
    line_step: int = LINE_STEP

    @property
    def ire_scale(self) -> float:
        return (self.white16 - self.black16) / 100.0

    @property
    def samples(self) -> int:
        return self.field_width * self.field_height

    def valid(self) -> bool:
        return (
            self.field_width > 0
            and self.field_height > 0
            and self.active_video_end > self.active_video_start
            and self.last_active_field_line > self.first_active_field_line
            and self.white16 > self.black16
        )


def _qround(value) -> int:
    """Round half away from zero, as the library's integer levels are read."""
    value = float(value)
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


def line_family(system):
    """'NTSC' or 'PAL' for a system name the decoders use, else None."""
    if system is None:
        return None
    return _LINE_FAMILY.get(str(system).upper())


def geometry_from_video_parameters(vp, system=None):
    """Build the measurement geometry from a decoder's ``videoParameters``.

    ``system`` is the decoder's own system name (e.g. ``rf.color_system`` on
    vhs-decode, which distinguishes SECAM/405/819 from their PAL parent);
    it defaults to ``vp["system"]``. Returns None when the system has no
    active-line default (405/819-line) and the parameters carry none, so
    the caller can skip metrics; otherwise a :class:`Geometry`, which may
    still be invalid (``valid()``) for degenerate parameters.
    """
    if system is None:
        system = vp.get("system")
    family = line_family(system)
    if family is None and str(system) not in DISABLED_SYSTEMS:
        # An unfamiliar spelling: fall back to the parent system the JSON
        # carries rather than silently losing the metrics.
        family = line_family(vp.get("system"))

    first = vp.get("firstActiveFieldLine")
    last = vp.get("lastActiveFieldLine")
    if first is None or last is None:
        if family is None:
            return None
        first, last = ACTIVE_FIELD_LINES[family]

    field_height = int(vp.get("fieldHeight", 0))
    first = max(1, int(first))
    last = min(field_height + 1, max(first, int(last)))

    black16 = _qround(vp.get("black16bIre", 0))
    white16 = _qround(vp.get("white16bIre", 0))
    blanking = vp.get("blanking16bIre")
    blanking16 = _qround(blanking) if blanking is not None and blanking >= 0 else black16

    return Geometry(
        field_width=int(vp.get("fieldWidth", 0)),
        field_height=field_height,
        active_video_start=int(vp.get("activeVideoStart", 0)),
        active_video_end=int(vp.get("activeVideoEnd", 0)),
        first_active_field_line=first,
        last_active_field_line=last,
        colour_burst_start=int(vp.get("colourBurstStart", 0)),
        colour_burst_end=int(vp.get("colourBurstEnd", 0)),
        black16=float(black16),
        white16=float(white16),
        blanking16=float(blanking16),
        sync_tip_ire=SYNC_TIP_IRE.get(family, SYNC_TIP_IRE["NTSC"]),
    )


def _as_field(samples, g):
    """A (field_height, field_width) uint16 view of a field, or None if short."""
    if samples is None:
        return None
    if isinstance(samples, (bytes, bytearray, memoryview)):
        samples = np.frombuffer(samples, dtype=np.uint16)
    samples = np.asarray(samples)
    if samples.ndim != 1:
        samples = samples.reshape(-1)
    if samples.size < g.samples:
        return None
    return samples[: g.samples].reshape(g.field_height, g.field_width)


def _active_rows(g):
    # Field line L (1-based) is row L - 1.
    return slice(g.first_active_field_line - 1, g.last_active_field_line - 1, g.line_step)


def _region_stats(field, g, x0, x1):
    """Mean and population std of a sample region over the strided active lines."""
    x0 = max(0, x0)
    x1 = min(g.field_width, x1)
    if x1 <= x0:
        return None, None
    block = field[_active_rows(g), x0:x1:g.sample_step].astype(np.float64)
    if block.size == 0:
        return None, None
    mean = float(block.mean())
    variance = float((block * block).mean()) - mean * mean
    return mean, math.sqrt(max(0.0, variance))


def _region_peak_to_peak(field, g, x0, x1):
    """Mean per-line peak-to-peak of a region (every sample), in raw units."""
    x0 = max(0, x0)
    x1 = min(g.field_width, x1)
    if x1 - x0 < 4:
        return None
    block = field[_active_rows(g), x0:x1]
    if block.size == 0:
        return None
    spans = block.max(axis=1).astype(np.float64) - block.min(axis=1).astype(np.float64)
    return float(spans.mean())


def measure_field(luma, chroma, prev_luma, g):
    """Measure one written field.

    ``luma`` is the field as written to the luma TBC (uint16 samples,
    ``field_height`` x ``field_width``); ``chroma`` (same geometry) supplies
    the burst on a separated S-Video stream, None measures it from
    ``luma``; ``prev_luma`` is the field written two before this one (same
    parity by output index) for ``fieldDiffIre``, None skips it.

    Returns a dict with only the finite metrics, each a plain ``float``
    rounded to two decimals, keys in alphabetical order. Empty when the
    geometry is missing/invalid or the field is shorter than it.
    """
    if g is None or not g.valid():
        return {}
    field = _as_field(luma, g)
    if field is None:
        return {}

    scale = g.ire_scale
    out = {}

    # Active picture.
    mean, _dev = _region_stats(field, g, g.active_video_start, g.active_video_end)
    if mean is not None:
        out["lumaMeanIre"] = (mean - g.black16) / scale

    # Back porch: between the end of the burst and the start of active
    # video. Flat on real video, so its deviation is the noise floor and
    # its level the blanking error.
    noise = None
    porch_start = max(0, g.colour_burst_end + 2)
    porch_end = g.active_video_start - 2
    if porch_end - porch_start >= 4:
        mean, dev = _region_stats(field, g, porch_start, porch_end)
        if mean is not None:
            out["blankingDevIre"] = (mean - g.blanking16) / scale
        if dev is not None:
            noise = dev / scale

    # Sync tip: the middle of the stretch before the burst. Also the noise
    # fallback when the back porch is too narrow to measure.
    pre_burst = max(0, g.colour_burst_start)
    if pre_burst >= 6:
        mean, dev = _region_stats(field, g, pre_burst // 6, pre_burst // 2)
        if mean is not None:
            out["syncTipDevIre"] = (mean - g.blanking16) / scale - g.sync_tip_ire
        if noise is None and dev is not None:
            noise = dev / scale
    if noise is not None:
        out["noiseIre"] = noise

    # Burst amplitude, from the chroma field when there is one.
    if g.colour_burst_end - g.colour_burst_start >= 4:
        source = _as_field(chroma, g)
        if source is None:
            source = field
        pp = _region_peak_to_peak(source, g, g.colour_burst_start, g.colour_burst_end)
        if pp is not None:
            out["burstAmpIre"] = pp / scale

    # Same-parity difference against the field written two before.
    previous = _as_field(prev_luma, g)
    if previous is not None:
        x0 = max(0, g.active_video_start)
        x1 = min(g.field_width, g.active_video_end)
        if x1 > x0:
            rows = _active_rows(g)
            current = field[rows, x0:x1:g.sample_step].astype(np.float64)
            before = previous[rows, x0:x1:g.sample_step].astype(np.float64)
            if current.size:
                out["fieldDiffIre"] = float(np.mean(np.abs(current - before))) / scale

    return {
        key: round(float(value), 2)
        for key, value in sorted(out.items())
        if math.isfinite(value)
    }
