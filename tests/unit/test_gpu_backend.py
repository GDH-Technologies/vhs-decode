import os

import numpy as np
import pytest

from vhsdecode.gpu_backend import create_backend


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
