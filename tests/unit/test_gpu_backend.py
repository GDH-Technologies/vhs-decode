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


class TestBackendFilterCache:
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
