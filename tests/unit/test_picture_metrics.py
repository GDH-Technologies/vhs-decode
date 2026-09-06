"""Unit tests for lddecode.picture_metrics (numpy only, no decoder wiring).

The synthetic 910x263 NTSC field below is the shared vector: tbc-tools pins
the same numbers against its fieldmetrics.cpp so a decoder-written value
and a later --tbc walk agree. The 16-bit levels are chosen so one IRE is
exactly 328 sample counts (near the real 333.82 of an ld/vhs-decode NTSC
decode: black 17818, white 51200, blanking 15111) and every region lands
on an integer sample value.
"""
import json
import math

import numpy as np
import pytest

from lddecode.picture_metrics import (
    ACTIVE_FIELD_LINES,
    METRIC_KEYS,
    Geometry,
    geometry_from_video_parameters,
    measure_field,
)

WIDTH, HEIGHT = 910, 263
BLACK, WHITE, BLANKING = 16384, 49184, 13924
SCALE = (WHITE - BLACK) / 100  # 328.0 counts per IRE
BURST_START, BURST_END = 74, 110
ACTIVE_START, ACTIVE_END = 134, 894

# What vhs-decode's build_json() reports for an NTSC decode (the active
# field lines are the library defaults; the decoder does not write them).
NTSC_VP = {
    "system": "NTSC",
    "fieldWidth": WIDTH,
    "fieldHeight": HEIGHT,
    "sampleRate": 14318181.81818182,
    "rfSourceSampleRateHz": 40e6,
    "colourBurstStart": BURST_START,
    "colourBurstEnd": BURST_END,
    "activeVideoStart": ACTIVE_START,
    "activeVideoEnd": ACTIVE_END,
    "black16bIre": float(BLACK),
    "white16bIre": float(WHITE),
    "blanking16bIre": float(BLANKING),
}


def _row_pair_sign():
    """+1 / -1 alternating per pair of rows: the strided line walk (every
    second line from line 20) then sees equal numbers of each sign."""
    rows = np.arange(HEIGHT)
    return np.where((rows // 2) % 2 == 0, 1, -1)[:, None]


def make_field(active_ire=30.0, porch_noise_ire=2.0, sync_noise_ire=0.0, burst_pp_ire=40.0):
    """A synthetic luma field and its chroma field, flattened like the TBC."""
    luma = np.full((HEIGHT, WIDTH), BLANKING, dtype=np.int64)
    sign = _row_pair_sign()
    # Sync tip at -40 IRE below blanking, optionally +/- sync_noise_ire.
    luma[:, :BURST_START] = BLANKING - 40 * SCALE + sign * sync_noise_ire * SCALE
    # Burst interval flat on the luma stream (S-Video luma carries no burst).
    # Back porch alternating +/- porch_noise_ire around blanking.
    luma[:, BURST_END:ACTIVE_START] = BLANKING + sign * porch_noise_ire * SCALE
    # Flat active picture.
    luma[:, ACTIVE_START:ACTIVE_END] = BLACK + active_ire * SCALE

    chroma = np.full((HEIGHT, WIDTH), BLANKING, dtype=np.int64)
    cols = np.arange(BURST_START, BURST_END)
    alternate = np.where(cols % 2 == 0, 1, -1)
    chroma[:, BURST_START:BURST_END] = BLANKING + alternate * (burst_pp_ire / 2) * SCALE

    assert luma.min() >= 0 and luma.max() <= 65535
    return luma.reshape(-1).astype(np.uint16), chroma.reshape(-1).astype(np.uint16)


@pytest.fixture
def geometry():
    return geometry_from_video_parameters(NTSC_VP, "NTSC")


class TestGeometry:
    def test_ntsc_defaults_and_levels(self, geometry):
        assert geometry.valid()
        assert (geometry.first_active_field_line, geometry.last_active_field_line) == ACTIVE_FIELD_LINES["NTSC"]
        assert geometry.sync_tip_ire == -40.0
        assert geometry.ire_scale == SCALE
        assert geometry.samples == WIDTH * HEIGHT

    def test_pal_family_lines_and_sync_tip(self):
        vp = dict(NTSC_VP, system="PAL", fieldHeight=313)
        for system in ("PAL", "SECAM", "MESECAM"):
            g = geometry_from_video_parameters(vp, system)
            assert (g.first_active_field_line, g.last_active_field_line) == ACTIVE_FIELD_LINES["PAL"]
            assert g.sync_tip_ire == pytest.approx(-300.0 / 7.0)

    def test_pal_m_is_a_525_line_system(self):
        for system in ("PAL-M", "PAL_M", "NLINHA"):
            g = geometry_from_video_parameters(dict(NTSC_VP, system="PAL-M"), system)
            assert (g.first_active_field_line, g.last_active_field_line) == ACTIVE_FIELD_LINES["NTSC"]
            assert g.sync_tip_ire == -40.0

    def test_system_defaults_to_the_parameters(self):
        g = geometry_from_video_parameters(NTSC_VP)
        assert (g.first_active_field_line, g.last_active_field_line) == ACTIVE_FIELD_LINES["NTSC"]

    def test_405_and_819_line_systems_are_disabled(self):
        vp = dict(NTSC_VP, system="PAL", fieldHeight=203)
        assert geometry_from_video_parameters(vp, "405") is None
        assert geometry_from_video_parameters(vp, "819") is None
        assert measure_field(make_field()[0], None, None, None) == {}

    def test_explicit_active_lines_are_honoured_and_clamped(self):
        vp = dict(NTSC_VP, firstActiveFieldLine=0, lastActiveFieldLine=400)
        g = geometry_from_video_parameters(vp, "NTSC")
        assert g.first_active_field_line == 1
        assert g.last_active_field_line == HEIGHT + 1

    def test_levels_round_like_the_library_reads_them(self):
        vp = dict(NTSC_VP, black16bIre=17818.4, white16bIre=51199.6)
        del vp["blanking16bIre"]
        g = geometry_from_video_parameters(vp, "NTSC")
        assert (g.black16, g.white16) == (17818.0, 51200.0)
        # No blanking level: the library falls back to black.
        assert g.blanking16 == 17818.0

    def test_degenerate_parameters_are_invalid(self):
        assert not geometry_from_video_parameters(
            dict(NTSC_VP, activeVideoEnd=ACTIVE_START), "NTSC"
        ).valid()
        assert not geometry_from_video_parameters(
            dict(NTSC_VP, white16bIre=float(BLACK)), "NTSC"
        ).valid()
        assert not geometry_from_video_parameters({}, "NTSC").valid()


class TestMeasureField:
    def test_synthetic_field_gives_the_pinned_numbers(self, geometry):
        luma, chroma = make_field()

        m = measure_field(luma, chroma, None, geometry)

        assert set(m) == {"blankingDevIre", "burstAmpIre", "lumaMeanIre", "noiseIre", "syncTipDevIre"}
        assert m["lumaMeanIre"] == pytest.approx(30.0, abs=0.01)
        assert m["blankingDevIre"] == pytest.approx(0.0, abs=0.01)
        assert m["noiseIre"] == pytest.approx(2.0, abs=0.01)
        assert m["syncTipDevIre"] == pytest.approx(0.0, abs=0.01)
        assert m["burstAmpIre"] == pytest.approx(40.0, abs=0.01)
        # No previous same-parity field -> no difference.
        assert "fieldDiffIre" not in m

    def test_same_parity_difference(self, geometry):
        luma, chroma = make_field(active_ire=30.0)
        brighter, _ = make_field(active_ire=40.0)

        m = measure_field(brighter, chroma, luma, geometry)

        assert m["fieldDiffIre"] == pytest.approx(10.0, abs=0.01)
        assert m["lumaMeanIre"] == pytest.approx(40.0, abs=0.01)
        # Symmetric: the difference is absolute.
        assert measure_field(luma, chroma, brighter, geometry)["fieldDiffIre"] == pytest.approx(10.0, abs=0.01)
        assert measure_field(luma, chroma, luma, geometry)["fieldDiffIre"] == 0.0

    def test_burst_comes_from_the_luma_field_without_chroma(self, geometry):
        luma, chroma = make_field()

        without = measure_field(luma, None, None, geometry)
        with_chroma = measure_field(luma, chroma, None, geometry)

        # The luma stream carries no burst: measured flat.
        assert without["burstAmpIre"] == 0.0
        assert with_chroma["burstAmpIre"] == pytest.approx(40.0, abs=0.01)
        # Everything else is unaffected by the chroma field.
        for key in ("lumaMeanIre", "blankingDevIre", "noiseIre", "syncTipDevIre"):
            assert without[key] == with_chroma[key]

    def test_narrow_porch_falls_back_to_the_sync_tip_for_noise(self):
        # activeVideoStart right after the burst leaves no measurable back
        # porch ([112, 113) is under the 4-sample minimum), so the blanking
        # deviation is unmeasurable and the noise comes from the sync tip.
        vp = dict(NTSC_VP, activeVideoStart=BURST_END + 5)
        g = geometry_from_video_parameters(vp, "NTSC")
        luma, chroma = make_field(sync_noise_ire=1.0)

        m = measure_field(luma, chroma, None, g)

        assert "blankingDevIre" not in m
        assert m["noiseIre"] == pytest.approx(1.0, abs=0.01)
        assert m["syncTipDevIre"] == pytest.approx(0.0, abs=0.01)
        assert m["burstAmpIre"] == pytest.approx(40.0, abs=0.01)

    def test_degenerate_geometry_returns_empty(self):
        luma, chroma = make_field()
        invalid = geometry_from_video_parameters(dict(NTSC_VP, activeVideoEnd=ACTIVE_START), "NTSC")
        assert measure_field(luma, chroma, None, invalid) == {}
        assert measure_field(luma, chroma, None, None) == {}
        # A field shorter than the geometry is unmeasurable, never an error.
        g = geometry_from_video_parameters(NTSC_VP, "NTSC")
        assert measure_field(luma[:-1], chroma, None, g) == {}
        # A short chroma field just drops the chroma burst source.
        assert measure_field(luma, chroma[:-1], None, g)["burstAmpIre"] == 0.0

    def test_result_is_plain_json_floats(self, geometry):
        luma, chroma = make_field()
        prev, _ = make_field(active_ire=25.0)

        m = measure_field(luma, chroma, prev, geometry)

        assert list(m) == sorted(m)
        assert set(m) <= set(METRIC_KEYS)
        for value in m.values():
            assert type(value) is float  # no numpy scalars
            assert math.isfinite(value)
            assert round(value, 2) == value
        text = json.dumps(m, allow_nan=False, separators=(",", ":"))
        assert json.loads(text) == m

    def test_accepts_the_raw_bytes_the_writer_has(self, geometry):
        luma, chroma = make_field()
        assert measure_field(luma.tobytes(), chroma.tobytes(), None, geometry) == measure_field(
            luma, chroma, None, geometry
        )

    def test_geometry_dataclass_is_frozen(self, geometry):
        assert isinstance(geometry, Geometry)
        with pytest.raises(Exception):
            geometry.field_width = 1
