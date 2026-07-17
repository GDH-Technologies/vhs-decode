import os

import numpy as np
import pytest

import vhsdecode.process as process
import vhsdecode.utils as utils
from vhsdecode.gpu_backend import create_backend


def _make_decoder(use_gpu):
    return process.VHSRFDecode(
        inputfreq=40,
        system="PAL",
        rf_options={"use_gpu": use_gpu, "force_cpu": not use_gpu},
    )


def _make_wave(decoder):
    wavemax = utils.gen_wave_at_frequency(4.8, 40, decoder.blocklen // 2)
    wavemin = utils.gen_wave_at_frequency(3.8, 40, decoder.blocklen // 2)
    return np.concatenate((wavemax, wavemin))


def _cuda_device_available():
    try:
        # Ensure CUDA_PATH is resolved before cupy's first touch caches it,
        # matching how create_backend is always the first cupy user in decode.
        from vhsdecode.gpu_backend import _ensure_cuda_path

        _ensure_cuda_path()
        import cupy as cp

        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:
        return False


class TestBackendCapabilities:
    def test_cpu_backend_reports_no_cupyx_signal(self):
        ctx = create_backend(use_gpu=False)
        assert ctx.active is False
        assert ctx.has_cupyx_signal is False

    def test_force_cpu_reports_no_cupyx_signal(self):
        ctx = create_backend(use_gpu=True, force_cpu=True)
        assert ctx.active is False
        assert ctx.has_cupyx_signal is False

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_gpu_backend_probes_cupyx_signal(self):
        ctx = create_backend(use_gpu=True)
        if not ctx.active:
            pytest.skip(f"GPU backend inactive: {ctx.reason}")
        # On a working CUDA setup the sosfiltfilt probe should succeed; if it
        # failed, the reason must say why so decode can degrade gracefully.
        assert isinstance(ctx.has_cupyx_signal, bool)
        if not ctx.has_cupyx_signal:
            assert "cupyx" in ctx.reason

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    @pytest.mark.skipif(
        not os.path.isdir("/usr/local/cuda"), reason="no system CUDA toolkit"
    )
    def test_cuda_path_autoprobe_sets_env(self, monkeypatch):
        monkeypatch.delenv("CUDA_PATH", raising=False)
        monkeypatch.delenv("CUDA_HOME", raising=False)
        ctx = create_backend(use_gpu=True)
        if not ctx.active:
            pytest.skip(f"GPU backend inactive: {ctx.reason}")
        assert os.environ.get("CUDA_PATH") == "/usr/local/cuda"
        # With a system toolkit present the probe must actually succeed;
        # a False here means the header lookup regressed.
        assert ctx.has_cupyx_signal is True, ctx.reason


def _analytic_test_signal(n=32768, seed=1234):
    rng = np.random.default_rng(seed)
    phase = np.cumsum(rng.uniform(0.3, 1.2, n))
    amp = 1.0 + 0.1 * np.sin(np.linspace(0.0, 50.0, n))
    return amp * np.exp(1j * phase)


class TestUnwrapHilbertXp:
    def test_matches_numba_reference_numpy(self):
        import lddecode.utils as lddu

        from vhsdecode.gpu_backend import unwrap_hilbert_xp

        h = _analytic_test_signal()
        ref = lddu.unwrap_hilbert(h, 40e6)
        out = unwrap_hilbert_xp(h, 40e6, np)
        assert out.dtype == np.float64
        assert float(np.max(np.abs(out - ref))) <= 1e-6

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_matches_numba_reference_cupy(self):
        import cupy as cp
        import lddecode.utils as lddu

        from vhsdecode.gpu_backend import unwrap_hilbert_xp

        h = _analytic_test_signal()
        ref = lddu.unwrap_hilbert(h, 40e6)
        out = cp.asnumpy(unwrap_hilbert_xp(cp.asarray(h), 40e6, cp))
        assert float(np.max(np.abs(out - ref))) <= 1e-6


class TestComplexEdiff1dXp:
    def test_matches_ediff1d_numpy(self):
        from vhsdecode.gpu_backend import complex_ediff1d_xp

        h = _analytic_test_signal(n=4096)
        ref = np.ediff1d(h, to_begin=0)
        out = complex_ediff1d_xp(h, np)
        assert np.array_equal(out, ref)

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_matches_ediff1d_cupy(self):
        import cupy as cp

        from vhsdecode.gpu_backend import complex_ediff1d_xp

        h = _analytic_test_signal(n=4096)
        ref = np.ediff1d(h, to_begin=0)
        out = cp.asnumpy(complex_ediff1d_xp(cp.asarray(h), cp))
        assert np.array_equal(out, ref)


class TestGatedIIR:
    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_cupyx_sosfiltfilt_matches_scipy_on_real_filters(self):
        import cupy as cp
        import cupyx.scipy.signal as cpx_signal
        import scipy.signal as sps

        decoder = _make_decoder(use_gpu=True)
        if not decoder.gpu_backend.has_cupyx_signal:
            pytest.skip(decoder.gpu_backend.reason)
        rng = np.random.default_rng(7)
        x = rng.normal(0.0, 1.0, decoder.blocklen)
        for name in ["FEnvPost", "RFTop"]:
            sos = np.atleast_2d(decoder.Filters[name])
            ref = sps.sosfiltfilt(sos, x)
            out = cp.asnumpy(cpx_signal.sosfiltfilt(cp.asarray(sos), cp.asarray(x)))
            assert float(np.max(np.abs(out - ref))) <= 1e-9, name

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_gpu_iir_table_flip_keeps_equivalence(self):
        gpu = _make_decoder(use_gpu=True)
        if not gpu.gpu_backend.has_cupyx_signal:
            pytest.skip(gpu.gpu_backend.reason)
        cpu = _make_decoder(use_gpu=False)
        wave = _make_wave(cpu)

        assert gpu._gpu_iir_steps == {}
        gpu._gpu_iir_steps = {"FEnvPost": True, "RFTop": True}

        cpu_video = cpu.demodblock(data=wave)["video"]
        gpu_video = gpu.demodblock(data=wave)["video"]
        env_scale = float(np.mean(np.asarray(cpu_video["envelope"], dtype=np.float64)))
        env_diff = float(
            np.max(
                np.abs(
                    np.asarray(cpu_video["envelope"], dtype=np.float64)
                    - np.asarray(gpu_video["envelope"], dtype=np.float64)
                )
            )
        )
        assert env_diff <= 1e-4 * env_scale
        demod_diff = float(
            np.max(np.abs(cpu_video["demod"] - np.asarray(gpu_video["demod"])))
        )
        assert demod_diff <= 5.0


class TestDeemphasisXp:
    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_gpu_deemphasis_stays_on_device_and_matches_cpu(self):
        import cupy as cp

        opts = {"nldeemp": True, "subdeemp": True}
        cpu = process.VHSRFDecode(
            inputfreq=40,
            system="PAL",
            tape_format="VHS",
            rf_options={"force_cpu": True, **opts},
        )
        gpu = process.VHSRFDecode(
            inputfreq=40,
            system="PAL",
            tape_format="VHS",
            rf_options={"use_gpu": True, **opts},
        )
        if not gpu.gpu_backend.active:
            pytest.skip(f"GPU backend inactive: {gpu.gpu_backend.reason}")

        rng = np.random.default_rng(11)
        # Demod-scale signal: a few MHz baseline with video-ish structure.
        demod = 4.0e6 + 2.0e5 * np.cumsum(rng.normal(0.0, 0.02, cpu.blocklen))

        cpu_out, _ = cpu._db_deemphasis(demod.copy(), cpu.gpu_backend)
        gpu_out, _ = gpu._db_deemphasis(demod.copy(), gpu.gpu_backend)

        # The whole deemphasis chain must stay device-resident on GPU...
        assert hasattr(gpu_out, "__cuda_array_interface__")
        # ...and match the CPU chain to float64 rounding on Hz-scale data.
        diff = float(np.max(np.abs(cp.asnumpy(gpu_out) - cpu_out)))
        assert diff <= 1.0


class TestSpikeRepairEquivalence:
    """The GPU decode path must repair diff-demod spikes index-for-index
    like the numba replace_spikes it round-trips through."""

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_gpu_decoder_repairs_spikes_like_cpu(self, monkeypatch):
        import vhsdecode.demod as demod_mod

        # Compare against the exact-f64 numba demodulator, not the
        # approximate-f32-atan2 vhsd_rust one.
        monkeypatch.setattr(demod_mod, "_HAS_VHSD_RUST", False)

        gpu = _make_decoder(use_gpu=True)
        if not gpu.gpu_backend.active:
            pytest.skip(f"GPU backend inactive: {gpu.gpu_backend.reason}")
        cpu = _make_decoder(use_gpu=False)
        # A wave with a burst of out-of-band garbage triggers the
        # diff-demod spike branch in both decoders.
        wave = _make_wave(cpu)
        rng = np.random.default_rng(42)
        wave[5000:5100] += rng.uniform(-1.5, 1.5, 100)

        cpu_demod = cpu.demodblock(data=wave)["video"]["demod"]
        gpu_demod = gpu.demodblock(data=wave)["video"]["demod"]
        assert float(np.max(np.abs(cpu_demod - gpu_demod))) <= 5.0
    def test_filter_cache_persists_across_blocks(self):
        decoder = _make_decoder(use_gpu=False)
        wave = _make_wave(decoder)
        decoder.demodblock(data=wave)
        assert decoder._backend_filters
        ids_first = {k: id(v) for k, v in decoder._backend_filters.items()}
        decoder.demodblock(data=wave)
        ids_second = {k: id(v) for k, v in decoder._backend_filters.items()}
        assert ids_first == ids_second

    def test_filter_cache_invalidated_on_recompute(self):
        decoder = _make_decoder(use_gpu=False)
        wave = _make_wave(decoder)
        decoder.demodblock(data=wave)
        assert decoder._backend_filters
        decoder.computevideofilters()
        assert decoder._backend_filters == {}

    @pytest.mark.skipif(not _cuda_device_available(), reason="no CUDA device")
    def test_filter_cache_holds_device_arrays_on_gpu(self):
        decoder = _make_decoder(use_gpu=True)
        if not decoder.gpu_backend.active:
            pytest.skip(f"GPU backend inactive: {decoder.gpu_backend.reason}")
        wave = _make_wave(decoder)
        decoder.demodblock(data=wave)
        assert decoder._backend_filters
        for name, arr in decoder._backend_filters.items():
            assert hasattr(arr, "__cuda_array_interface__"), name
