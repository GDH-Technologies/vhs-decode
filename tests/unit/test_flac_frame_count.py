"""Guard tests for ``flac_frame_count_mismatch``.

STREAMINFO's total_samples is a 36-bit field. At 40 MSps it overflows after
~28.6 minutes, so every full-length RF capture is written with the "unknown
length" value of 0 -- and libsndfile reports that back as SF_COUNT_MAX, not 0.
Missing that case keeps the libsndfile reader, which dies near EOF with
"Internal psf_fseek() failed"; the block reader swallows that as a short read
and truncates the tail while still exiting 0.
"""
from __future__ import annotations

import types

import pytest

from vhsdecode.hifi.main import _FLAC_MAX_TOTAL_SAMPLES, flac_frame_count_mismatch


SF_COUNT_MAX = (1 << 63) - 1


def _reader(frames, *, subtype="PCM_S8", channels=1):
    return types.SimpleNamespace(frames=frames, subtype=subtype, channels=channels)


@pytest.fixture
def flac_file(tmp_path):
    """A stand-in whose size implies ~1000 decoded 8-bit mono samples."""
    path = tmp_path / "capture.flac"
    path.write_bytes(b"\0" * 1000)
    return str(path)


@pytest.mark.parametrize(
    "frames",
    [
        0,
        -1,
        SF_COUNT_MAX,
        _FLAC_MAX_TOTAL_SAMPLES,
        _FLAC_MAX_TOTAL_SAMPLES + 1,
    ],
    ids=["zero", "negative", "sf_count_max", "at_36_bit_cap", "past_36_bit_cap"],
)
def test_unknown_length_is_rejected(flac_file, frames):
    reason = flac_frame_count_mismatch(_reader(frames), flac_file)
    assert reason is not None
    assert "unknown length" in reason


def test_sf_count_max_is_not_mistaken_for_a_plausible_count(flac_file):
    """The regression: SF_COUNT_MAX is huge, so the "far below file size" arm
    never fires on it. Only the explicit cap check catches it."""
    reason = flac_frame_count_mismatch(_reader(SF_COUNT_MAX), flac_file)
    assert reason is not None


def test_plausible_count_is_accepted(flac_file):
    # 2000 8-bit mono samples decode to 2000 bytes from a 1000-byte file --
    # a normal ~2:1 FLAC ratio.
    assert flac_frame_count_mismatch(_reader(2000), flac_file) is None


def test_count_far_below_file_size_is_rejected(flac_file):
    # misrc_tools' /1000-scaled header: claims far less audio than the file holds.
    reason = flac_frame_count_mismatch(_reader(2), flac_file)
    assert reason is not None
    assert "corrupt" in reason


def test_largest_representable_count_is_still_accepted(tmp_path):
    """One below the cap is a legal header value and must not be rejected."""
    path = tmp_path / "big.flac"
    path.write_bytes(b"\0" * 1024)
    frames = _FLAC_MAX_TOTAL_SAMPLES - 1
    assert flac_frame_count_mismatch(_reader(frames), str(path)) is None


def test_missing_file_degrades_to_no_opinion(tmp_path):
    assert flac_frame_count_mismatch(_reader(0), str(tmp_path / "gone.flac")) is None
