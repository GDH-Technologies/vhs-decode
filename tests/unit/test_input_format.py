"""The sample format of a headerless capture.

A MISRC RAW capture carries no header, and the extension it is written with
(``.raw``) says nothing about the width or signedness of the samples inside:
the tool writes signed int16 in its 16-bit mode and signed int8 in its 8-bit
one. Reading one as the other does not fail -- it silently yields a scrambled
waveform that no sync can be found in -- so the format has to be selectable,
and the selection has to win over the extension table.
"""

from __future__ import annotations

import numpy as np

import lddecode.utils as utils


def _write(tmp_path, name, values, dtype):
    path = tmp_path / name
    np.asarray(values, dtype=dtype).tofile(path)
    return path


class TestReadersNumpyRemoved:
    """np.fromstring's binary mode was removed in numpy 1.22.

    Both readers that still called it raised ``ValueError`` on every call, so
    a .s16 or .rf capture could not be read at all on any current build.
    """

    def test_a_signed_sixteen_bit_capture_reads_back(self, tmp_path):
        samples = [-32768, -1, 0, 1, 32767]
        path = _write(tmp_path, "capture.s16", samples, np.int16)

        with open(path, "rb") as infile:
            data = utils.load_unpacked_data_s16(infile, 0, len(samples))

        assert data.tolist() == samples

    def test_a_float_capture_reads_back_scaled_to_the_sample_domain(self, tmp_path):
        samples = [-1.0, -0.5, 0.0, 0.5]
        path = _write(tmp_path, "capture.rf", samples, np.float32)

        with open(path, "rb") as infile:
            data = utils.load_unpacked_data_float32(infile, 0, len(samples))

        assert data.tolist() == [v * 32768 for v in samples]
