"""Regression tests for the pure-scipy fallback in sosfiltfilt_rust.

When the optional ``vhsd_rust`` extension is unavailable, ``sosfiltfilt_rust``
falls back to ``scipy.signal.sosfiltfilt``. scipy always promotes to float64 and
returns a reverse-strided view, while the rust path returns a C-contiguous array
that keeps float32 input as float32.

The hifi decoder feeds this output straight into numba kernels compiled with
explicit signatures (``FMDiscriminator.demod_quadrature`` only accepts float32),
so a fallback that changes dtype or layout makes every decoder worker die with
"No matching definition for argument type(s) array(float64, 1d, A), ...".
"""

import numpy as np
import pytest
from scipy.signal import cheby2

import vhsdecode.rust_utils as rust_utils
from vhsdecode.hifi.constants import (
    DEMOD_HILBERT,
    DEMOD_HILBERT_IF_RATE,
    DEMOD_QUADRATURE,
)


@pytest.fixture
def sos():
    return cheby2(N=4, rs=60, Wn=[0.1, 0.3], btype="bandpass", output="sos")


@pytest.fixture
def force_fallback(monkeypatch):
    """Exercise the scipy path even on machines where vhsd_rust is built."""
    monkeypatch.setattr(rust_utils, "_HAS_VHSD_RUST", False)


@pytest.mark.usefixtures("force_fallback")
class TestFallbackMatchesRustContract:
    @pytest.mark.parametrize(
        "in_dtype, expected_dtype",
        [
            (np.float32, np.float32),
            (np.float64, np.float64),
            (np.int16, np.float32),
        ],
    )
    def test_output_dtype(self, sos, in_dtype, expected_dtype):
        data = np.zeros(4096, dtype=in_dtype)
        result = rust_utils.sosfiltfilt_rust(sos, data)
        assert result.dtype == expected_dtype

    def test_output_is_c_contiguous(self, sos):
        data = np.zeros(4096, dtype=np.float32)
        result = rust_utils.sosfiltfilt_rust(sos, data)
        assert result.flags["C_CONTIGUOUS"]

    def test_non_contiguous_input_is_accepted(self, sos):
        data = np.zeros(8192, dtype=np.float32)[::2]
        result = rust_utils.sosfiltfilt_rust(sos, data)
        assert result.dtype == np.float32
        assert result.flags["C_CONTIGUOUS"]

    def test_filtering_is_still_correct(self, sos):
        rng = np.random.default_rng(0)
        data = rng.standard_normal(4096).astype(np.float32)
        result = rust_utils.sosfiltfilt_rust(sos, data)
        from scipy.signal import sosfiltfilt

        assert np.allclose(result, sosfiltfilt(sos, data), atol=1e-5)


class TestHiFiDemodAcceptsFallbackOutput:
    """The user-visible failure: AFE output must be usable by the numba demod."""

    @pytest.mark.parametrize(
        "demod_type, if_rate",
        [
            (DEMOD_QUADRATURE, 40_000_000),
            (DEMOD_HILBERT, DEMOD_HILBERT_IF_RATE),
        ],
    )
    def test_demod_accepts_afe_output(self, monkeypatch, demod_type, if_rate):
        monkeypatch.setattr(rust_utils, "_HAS_VHSD_RUST", False)

        from vhsdecode.hifi.HiFiDecode import (
            AFEFilterable,
            AFEParamsNTSCVHS,
            FMDiscriminator,
            REAL_DTYPE,
        )

        params = AFEParamsNTSCVHS()
        afe = AFEFilterable(params, if_rate, 0)

        rf = np.zeros(16384, dtype=REAL_DTYPE)
        filtered = afe.work(rf)

        fm = FMDiscriminator(
            if_rate,
            params.LCarrierRef,
            params.LCarrierDeviation,
            len(rf),
            demod_type,
        )
        out = np.empty(len(filtered), dtype=REAL_DTYPE, order="C")

        fm.work(filtered, out)
