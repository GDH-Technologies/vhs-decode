import numpy as np
import scipy.signal as sps

try:
    from vhsd_rust import sosfiltfilt, sosfiltfilt_f32

    _HAS_VHSD_RUST = True
except ModuleNotFoundError:
    sosfiltfilt = None
    sosfiltfilt_f32 = None
    _HAS_VHSD_RUST = False


def sos_filter_as_array_and_order(filter):
    """Convert the sos filter to a array derive the filter order for use inside
    rust code with sci_rs
    We do this here rather than in rust for now for easier interop."""
    filter_view = filter.ravel()
    assert (
        len(filter_view) % 6 == 0
    ), "filter length is not divideable by 6, there is a bug somewhere!"
    return int(len(filter_view) / 6), filter_view


def sosfiltfilt_rust(sos, input):
    assert input.dtype != np.complex128
    if input.dtype == np.complex128:
        input = abs(input)

    if not _HAS_VHSD_RUST:
        # scipy promotes everything to float64 and hands back a reverse-strided
        # view. Match the rust path's contract instead (float64 in -> float64 out,
        # anything else -> float32, always C-contiguous), otherwise numba kernels
        # compiled with explicit signatures reject the result. hifi-decode's
        # FMDiscriminator.demod_quadrature only accepts float32, so the raw scipy
        # output kills every decoder worker with "No matching definition for
        # argument type(s) array(float64, 1d, A), ...".
        out_dtype = np.float64 if input.dtype == np.float64 else np.float32
        return np.ascontiguousarray(sps.sosfiltfilt(sos, input), dtype=out_dtype)

    order, filter = sos_filter_as_array_and_order(sos)

    if input.dtype == np.float64:
        return sosfiltfilt(order, filter, input)
    # if input.dtype == np.float32:
    #    return sosfiltfilt_f32(order, filter, input)
    return sosfiltfilt_f32(order, filter, input.astype(np.float32))
