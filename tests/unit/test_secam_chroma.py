"""Tests for the SECAM/MESECAM chroma up-conversion.

ME-SECAM (IEC 60774-1 Annex E) is recorded like PAL colour-under with an
inverting mix against the PAL converter LO of 5060571.875 Hz, so up-converting
with that same LO must put the rest carriers back on the studio frequencies
foR = 4406250 Hz and foB = 4250000 Hz (ITU-R BT.470/BT.1700).
"""

import logging

import numpy as np
import scipy.signal as sps

import vhsdecode.process as process
import vhsdecode.formats as vhs_formats
from vhsdecode.addons.chromaAFC import ChromaAFC
from vhsdecode.chroma import measure_secam_under_carrier_offset

FOR = 4406250.0
FOB = 4250000.0
CONVERSION_LO = 5060571.875
DR_UNDER = CONVERSION_LO - FOR  # 654321.875 Hz
DB_UNDER = CONVERSION_LO - FOB  # 810571.875 Hz

LINES = 313


def _get_params():
    return vhs_formats.get_format_params(
        "MESECAM", "VHS", 0, logging.getLogger("test")
    )


def _make_afc(sys_params, rf_params):
    return ChromaAFC(
        40e6,
        rf_params["chroma_bpf_upper"] / rf_params["color_under_carrier"],
        sys_params,
        rf_params["color_under_carrier"],
        tape_format="VHS",
        do_cafc=False,
        conversion_lo_freq=rf_params["chroma_conversion_lo"],
    )


def _make_under_signal(outlinelen, true_rate, crystal_error_hz):
    """Line-alternating colour-under rest carriers as they come off the TBC."""
    num_samples = LINES * outlinelen
    t = np.arange(num_samples) / true_rate
    sig = np.zeros(num_samples)
    for line in range(LINES):
        start, end = line * outlinelen, (line + 1) * outlinelen
        freq = (DR_UNDER if line % 2 else DB_UNDER) + crystal_error_hz
        sig[start:end] = np.sin(2 * np.pi * freq * t[start:end])
    return sig


def _measure_restored_carriers(afc, uphet, outlinelen, true_rate):
    """Median instantaneous frequency of the mixed+filtered signal per line parity."""
    filtered = sps.sosfiltfilt(afc.get_chroma_bandpass_final(True), uphet)
    analytic = sps.hilbert(filtered)
    f_inst = np.diff(np.unwrap(np.angle(analytic))) * true_rate / (2 * np.pi)
    odd, even = [], []
    for line in range(20, LINES - 20):
        start = line * outlinelen + 200
        end = (line + 1) * outlinelen - 200
        (odd if line % 2 else even).append(np.median(f_inst[start:end]))
    return np.median(odd), np.median(even)


class TestMESECAMUpconversion:
    def test_format_params(self):
        sys_params, rf_params = _get_params()

        assert rf_params["chroma_conversion_lo"] == CONVERSION_LO
        # fsc must stay at the PAL value so it is consistent with outlinelen
        # and the true TBC output rate.
        assert sys_params["fsc_mhz"] == 4.43361875
        assert rf_params["color_under_carrier"] == (DR_UNDER + DB_UNDER) / 2

    def test_carriers_restored_to_studio_frequencies(self):
        sys_params, rf_params = _get_params()
        afc = _make_afc(sys_params, rf_params)
        outlinelen = sys_params["outlinelen"]
        true_rate = outlinelen * sys_params["FPS"] * sys_params["frame_lines"]
        num_samples = LINES * outlinelen

        sig = _make_under_signal(outlinelen, true_rate, 0.0)
        het = afc.getChromaHet()[0][:num_samples]
        restored_dr, restored_db = _measure_restored_carriers(
            afc, sig * het, outlinelen, true_rate
        )

        # The old code restored the carriers about 110 kHz high.
        np.testing.assert_allclose(restored_dr, FOR, atol=5)
        np.testing.assert_allclose(restored_db, FOB, atol=5)

    def test_servo_measures_crystal_error(self):
        sys_params, rf_params = _get_params()
        afc = _make_afc(sys_params, rf_params)
        outlinelen = sys_params["outlinelen"]
        true_rate = outlinelen * sys_params["FPS"] * sys_params["frame_lines"]
        num_samples = LINES * outlinelen

        error = 1500.0
        sig = _make_under_signal(outlinelen, true_rate, error)

        active_start_px = int(10.5e-6 * true_rate)
        window = (active_start_px - 65, active_start_px - 5)
        measured = measure_secam_under_carrier_offset(
            sig, LINES, outlinelen, window, true_rate, afc.color_under
        )

        assert measured is not None
        # Single-field accuracy is on the order of +-100 Hz.
        np.testing.assert_allclose(measured, error, atol=150)

        # Applying the measurement as LO trim must bring the restored
        # carriers back near nominal.
        afc.updateConversion(measured, 0)
        het = afc.getChromaHet()[0][:num_samples]
        restored_dr, restored_db = _measure_restored_carriers(
            afc, sig * het, outlinelen, true_rate
        )
        np.testing.assert_allclose(restored_dr, FOR, atol=200)
        np.testing.assert_allclose(restored_db, FOB, atol=200)

    def test_servo_rejects_signal_without_carrier_pair(self):
        sys_params, rf_params = _get_params()
        afc = _make_afc(sys_params, rf_params)
        outlinelen = sys_params["outlinelen"]
        true_rate = outlinelen * sys_params["FPS"] * sys_params["frame_lines"]

        rng = np.random.default_rng(1234)
        noise = rng.normal(0, 1, LINES * outlinelen)
        active_start_px = int(10.5e-6 * true_rate)
        window = (active_start_px - 65, active_start_px - 5)
        measured = measure_secam_under_carrier_offset(
            noise, LINES, outlinelen, window, true_rate, afc.color_under
        )

        assert measured is None

    def test_heterodyne_phase_continuous_across_fields(self):
        sys_params, rf_params = _get_params()
        afc = _make_afc(sys_params, rf_params)
        outlinelen = sys_params["outlinelen"]
        num_samples = LINES * outlinelen

        afc.updateConversion(0.0, 0)
        field0 = afc.getChromaHet()[0][:num_samples].copy()
        afc.updateConversion(0.0, num_samples)
        field1 = afc.getChromaHet()[0][:outlinelen].copy()

        joined = np.concatenate([field0, field1])
        # A phase step shows up as a spike in the second difference.
        second_diff = np.abs(np.diff(joined, 2))
        boundary = second_diff[num_samples - 10 : num_samples + 10].max()
        assert boundary <= second_diff[: num_samples // 2].max() * 1.01


class TestMESECAMDecoderConstruction:
    def test_construct(self):
        decoder = process.VHSRFDecode(inputfreq=40, system="MESECAM")

        # SECAM has no phase-locked burst, so burst-locked hsync must be off.
        assert decoder.options.disable_burst_hsync
        # cafc peak measurement is meaningless on the two-carrier SECAM signal.
        assert not decoder.do_cafc
        assert decoder.chroma_afc.conversion_lo == CONVERSION_LO
        # No trim seed given, so the servo average starts empty and no trim
        # is applied until enough fields have been measured.
        assert not decoder.secam_servo_avg.has_values()

    def test_lo_trim_seed(self):
        decoder = process.VHSRFDecode(
            inputfreq=40,
            system="MESECAM",
            rf_options={"secam_lo_trim": 2000.0},
        )

        # Seeded (e.g. by the two-pass calibration) so the trim applies from
        # the first field.
        assert decoder.secam_servo_avg.has_values()
        np.testing.assert_allclose(decoder.secam_servo_avg.pull(), 2000.0)
