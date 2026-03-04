from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class BackendContext:
    active: bool
    name: str
    xp: Any
    reason: str = ""
    gpu_name: Optional[str] = None


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

        return BackendContext(
            active=True,
            name="cupy",
            xp=cp,
            reason="CUDA backend active",
            gpu_name=gpu_name,
        )
    except Exception as exc:
        return BackendContext(
            active=False,
            name="numpy",
            xp=np,
            reason=f"CUDA init failed: {exc}",
        )


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
