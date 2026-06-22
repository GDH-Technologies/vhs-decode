import numba as nb
import numpy as np
from vhsdecode.hifi.constants import (
    FORMAT_U8,
    FORMAT_U10,
    FORMAT_U12,
    FORMAT_U16,
    FORMAT_S8,
    FORMAT_S10,
    FORMAT_S12,
    FORMAT_S16,
    FORMAT_F32,
    FORMAT_TO_DTYPE,
    DTYPE_TO_FORMAT
)

@nb.njit("void(uint8[:], float32[:], int32)", fastmath=True)
def _u8_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (2.0 / 255.0) - 1.0


@nb.njit("void(int8[:], float32[:], int32)", fastmath=True)
def _s8_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (1.0 / 128.0)


@nb.njit("void(uint16[:], float32[:], int32)", fastmath=True)
def _u10_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (2.0 / 1023.0) - 1.0


@nb.njit("void(int16[:], float32[:], int32)", fastmath=True)
def _s10_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (1.0 / 512.0)


@nb.njit("void(uint16[:], float32[:], int32)", fastmath=True)
def _u12_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (2.0 / 4095.0) - 1.0


@nb.njit("void(int16[:], float32[:], int32)", fastmath=True)
def _s12_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (1.0 / 2048.0)


@nb.njit("void(uint16[:], float32[:], int32)", fastmath=True)
def _u16_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (2.0 / 65535.0) - 1.0


@nb.njit("void(int16[:], float32[:], int32)", fastmath=True)
def _s16_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i] * (1.0 / 32768.0)


@nb.njit("void(float32[:], float32[:], int32)", fastmath=True)
def _f32_to_f32(x, out, n):
    for i in range(n - 1, -1, -1):
        out[i] = x[i]

_FORMAT_TO_NORMALIZER = {
    FORMAT_U8: _u8_to_f32,
    FORMAT_U10: _u10_to_f32,
    FORMAT_U12: _u12_to_f32,
    FORMAT_U16: _u16_to_f32,
    FORMAT_S8: _s8_to_f32,
    FORMAT_S10: _s10_to_f32,
    FORMAT_S12: _s12_to_f32,
    FORMAT_S16: _s16_to_f32,
    FORMAT_F32: _f32_to_f32
}

def get_normalizer(fmt_or_dtype):
    if isinstance(fmt_or_dtype, str):
        string_dtype = fmt_or_dtype.lower()
    else:
        string_dtype = DTYPE_TO_FORMAT[fmt_or_dtype]
        
    numpy_dtype = FORMAT_TO_DTYPE[string_dtype]

    try:
        return _FORMAT_TO_NORMALIZER[string_dtype], numpy_dtype
    except KeyError:
        raise ValueError(f"Unsupported format: {fmt_or_dtype}")