import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

_DEFAULT_CUDA_PATH = "/usr/local/cuda"


@dataclass(frozen=True)
class BackendContext:
    active: bool
    name: str
    xp: Any
    reason: str = ""
    gpu_name: Optional[str] = None
    has_cupyx_signal: bool = False


def _ensure_cuda_path():
    """cupyx kernels are NVRTC-compiled at runtime and need the CUDA headers,
    located via CUDA_PATH/CUDA_HOME. Point at the system toolkit if unset."""
    if "CUDA_PATH" in os.environ or "CUDA_HOME" in os.environ:
        return
    if os.path.isdir(_DEFAULT_CUDA_PATH):
        os.environ.setdefault("CUDA_PATH", _DEFAULT_CUDA_PATH)


def _probe_cupyx_signal(cp):
    try:
        import cupyx.scipy.signal as cpx_signal

        sos = np.array([[0.5, 0.5, 0.0, 1.0, 0.0, 0.0]])
        cpx_signal.sosfiltfilt(cp.asarray(sos), cp.arange(16, dtype=cp.float64))
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def create_backend(use_gpu: bool = False, force_cpu: bool = False) -> BackendContext:
    if force_cpu:
        return BackendContext(
            active=False,
            name="numpy",
            xp=np,
            reason="GPU disabled by --force-cpu",
        )

    if not use_gpu:
        return BackendContext(active=False, name="numpy", xp=np, reason="GPU not requested")

    # Must happen before the first cupy import/use: cupy resolves and caches
    # its CUDA toolkit path (used for NVRTC header lookup) on first touch.
    _ensure_cuda_path()

    try:
        import cupy as cp
    except Exception as exc:
        return BackendContext(
            active=False,
            name="numpy",
            xp=np,
            reason=f"CuPy import failed: {exc}",
        )

    try:
        device_count = cp.cuda.runtime.getDeviceCount()
        if device_count < 1:
            return BackendContext(
                active=False,
                name="numpy",
                xp=np,
                reason="No CUDA devices detected",
            )

        device = cp.cuda.Device()
        props = cp.cuda.runtime.getDeviceProperties(device.id)
        gpu_name = props.get("name", None)
        if isinstance(gpu_name, bytes):
            gpu_name = gpu_name.decode("utf-8", errors="replace")

        try:
            cp.fft.fft(cp.asarray([0.0], dtype=cp.float32))
        except Exception as fft_exc:
            return BackendContext(
                active=False,
                name="numpy",
                xp=np,
                reason=f"CuPy FFT unavailable: {fft_exc}",
            )

        has_cupyx_signal, probe_error = _probe_cupyx_signal(cp)
        reason = "CUDA backend active"
        if not has_cupyx_signal:
            reason += (
                " (cupyx.scipy.signal unavailable, IIR filters stay on CPU: "
                f"{probe_error})"
            )

        return BackendContext(
            active=True,
            name="cupy",
            xp=cp,
            reason=reason,
            gpu_name=gpu_name,
            has_cupyx_signal=has_cupyx_signal,
        )
    except Exception as exc:
        return BackendContext(
            active=False,
            name="numpy",
            xp=np,
            reason=f"CUDA init failed: {exc}",
        )


class StageTimer:
    """Accumulates per-stage wall time into a dict when profiling is
    requested; free when it is not. Synchronizes the device at each mark so
    GPU stage times are honest."""

    def __init__(self, profile, ctx: BackendContext):
        self._profile = profile
        self._ctx = ctx
        self._last = time.perf_counter() if profile is not None else 0.0

    def mark(self, name: str):
        if self._profile is None:
            return
        if self._ctx.active:
            self._ctx.xp.cuda.get_current_stream().synchronize()
        now = time.perf_counter()
        self._profile[name] = self._profile.get(name, 0.0) + (now - self._last)
        self._last = now


_TAU = np.pi * 2


def unwrap_hilbert_xp(hilbert, freq_hz, xp):
    """Conjugate-product FM discriminator, the same algorithm as the numba
    lddecode.utils.unwrap_hilbert, expressed in xp (numpy or cupy) ops so it
    can run on whichever backend holds the analytic signal."""
    out = xp.empty(len(hilbert), dtype=xp.float64)
    out[0] = 0.0

    # phase increment between consecutive samples = arg(z[n] * conj(z[n-1]))
    prod = hilbert[1:] * xp.conj(hilbert[:-1])
    d = xp.arctan2(prod.imag, prod.real)

    # preserve the historical [0, tau) convention (positive frequencies only)
    out[1:] = xp.where(d < 0.0, d + _TAU, d)

    return out * (freq_hz / _TAU)


def complex_ediff1d_xp(arr, xp):
    """np.ediff1d(arr, to_begin=0) for either backend."""
    out = xp.empty_like(arr)
    out[0] = 0
    out[1:] = arr[1:] - arr[:-1]
    return out


def using_gpu(ctx: BackendContext) -> bool:
    return ctx.active


def to_backend_array(array: Any, ctx: BackendContext, dtype: Any = None) -> Any:
    if ctx.active:
        return ctx.xp.asarray(array, dtype=dtype)
    return np.asarray(array, dtype=dtype) if dtype is not None else array


def to_numpy_if_needed(array: Any, ctx: BackendContext) -> Any:
    if ctx.active and hasattr(array, "__cuda_array_interface__"):
        return ctx.xp.asnumpy(array)
    return array


def fft(array: Any, ctx: BackendContext) -> Any:
    return ctx.xp.fft.fft(array)


def ifft(array: Any, ctx: BackendContext) -> Any:
    return ctx.xp.fft.ifft(array)


def rfft(array: Any, ctx: BackendContext) -> Any:
    return ctx.xp.fft.rfft(array)


def irfft(array: Any, ctx: BackendContext) -> Any:
    return ctx.xp.fft.irfft(array)
