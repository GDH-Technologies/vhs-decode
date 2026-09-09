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
import pytest

import lddecode.utils as utils


def _write(tmp_path, name, values, dtype):
    path = tmp_path / name
    np.asarray(values, dtype=dtype).tofile(path)
    return path


class TestSampleFormats:
    """The names --input_format accepts, and the dtype each one reads."""

    def test_every_name_maps_to_a_numpy_dtype_and_an_ffmpeg_format(self):
        assert set(utils.SAMPLE_FORMATS) == {"s8", "u8", "s16", "u16", "f32"}
        for name, spec in utils.SAMPLE_FORMATS.items():
            assert np.dtype(spec.dtype).itemsize in (1, 2, 4), name
            assert spec.ffmpeg_format, name

    def test_an_unknown_name_is_refused_by_name(self):
        with pytest.raises(ValueError, match="s24"):
            utils.make_loader("capture.raw", None, sample_format="s24")


class TestSignedEightBitLoader:
    """The reader MISRC's 8-bit RAW mode needs."""

    def test_it_reads_the_samples_as_written(self, tmp_path):
        # The values that exposed this: read as uint8, -9 comes back as 247.
        samples = [24, 25, 17, 5, -9, -17, -13, -6, 5, 17]
        path = _write(tmp_path, "capture.raw", samples, np.int8)

        with open(path, "rb") as infile:
            data = utils.load_unpacked_data_s8(infile, 0, len(samples))

        assert data.dtype == np.int8
        assert data.tolist() == samples

    def test_it_seeks_by_sample_not_by_byte_pair(self, tmp_path):
        samples = list(range(-8, 8))
        path = _write(tmp_path, "capture.raw", samples, np.int8)

        with open(path, "rb") as infile:
            data = utils.load_unpacked_data_s8(infile, 5, 4)

        assert data.tolist() == samples[5:9]

    def test_a_short_read_reports_nothing_rather_than_a_short_block(self, tmp_path):
        path = _write(tmp_path, "capture.raw", [1, 2, 3], np.int8)

        with open(path, "rb") as infile:
            assert utils.load_unpacked_data_s8(infile, 0, 10) is None


class TestMakeLoaderSampleFormat:
    """An explicit format wins over what the extension would have implied."""

    def test_it_overrides_the_extension_when_reading_directly(self, tmp_path):
        samples = [24, 25, 17, 5, -9, -17]
        path = _write(tmp_path, "capture.raw", samples, np.int8)

        loader = utils.make_loader(str(path), None, sample_format="s8")

        with open(path, "rb") as infile:
            assert loader(infile, 0, len(samples)).tolist() == samples

    def test_it_overrides_the_extension_when_resampling(self):
        loader = utils.make_loader("capture.raw", 40, sample_format="s8")

        assert isinstance(loader, utils.LoadFFmpeg)
        assert loader.input_args == ["-f", "s8"]

    def test_a_resampling_rate_still_reaches_ffmpeg(self):
        loader = utils.make_loader("capture.raw", 20, sample_format="s8")

        assert loader.input_args == ["-f", "s8"]
        assert "asetrate=20000000.0" in "".join(loader.output_args)

    def test_it_overrides_a_format_that_cannot_be_resampled(self):
        # .lds refuses outright without a format; naming one makes it readable.
        loader = utils.make_loader("capture.lds", 40, sample_format="u8")

        assert loader.input_args == ["-f", "u8"]

    @pytest.mark.parametrize(
        "name, dtype, samples",
        [
            ("u8", np.uint8, [0, 128, 255]),
            ("s16", np.int16, [-32768, 0, 32767]),
            ("u16", np.uint16, [0, 32768, 65535]),
        ],
    )
    def test_the_other_widths_read_their_own_samples_back(
        self, tmp_path, name, dtype, samples
    ):
        path = _write(tmp_path, f"capture_{name}.raw", samples, dtype)

        loader = utils.make_loader(str(path), None, sample_format=name)

        with open(path, "rb") as infile:
            assert loader(infile, 0, len(samples)).tolist() == samples


class TestExtensionBehaviourIsUnchanged:
    """Every capture already on the fleet must keep decoding as it did."""

    def test_raw_without_a_format_is_still_signed_sixteen_bit(self):
        loader = utils.make_loader("capture.raw", 40)

        assert loader.input_args == ["-f", "s16le"]

    def test_the_direct_loaders_still_dispatch_on_the_extension(self):
        assert utils.make_loader("c.u8", None) is utils.load_unpacked_data_u8
        assert utils.make_loader("c.s16", None) is utils.load_unpacked_data_s16
        assert utils.make_loader("c.u16", None) is utils.load_unpacked_data_u16
        assert utils.make_loader("c.rf", None) is utils.load_unpacked_data_float32

    def test_an_s8_capture_no_longer_falls_through_to_ffmpeg(self):
        # .s8 named an ffmpeg format when resampling but had no direct reader,
        # so --no_resample sent a headerless file to ffmpeg with no -f at all.
        assert utils.make_loader("c.s8", None) is utils.load_unpacked_data_s8
        assert utils.make_loader("c.s8", 40).input_args == ["-f", "s8"]


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


class TestDumperWithoutMetadata:
    """A decode that produced no usable field still has to shut down cleanly."""

    def _drain(self, tmp_path, item):
        import queue as _queue
        import threading

        q = _queue.Queue()
        ready = threading.Event()
        q.put(item)
        q.put(None)
        utils.JSONDumper._consume(q, ready, str(tmp_path / "out"), False)
        return ready

    def test_no_metadata_writes_nothing_and_leaves_no_temp_file(self, tmp_path):
        self._drain(tmp_path, (None, [{"seqNo": 1}]))

        assert not (tmp_path / "out.tbc.json.tmp").exists()
        assert not (tmp_path / "out.tbc.json").exists()

    def test_it_releases_the_writing_flag_so_a_later_write_can_queue(self, tmp_path):
        ready = self._drain(tmp_path, (None, [{"seqNo": 1}]))

        assert not ready.is_set()

    def test_metadata_that_is_present_is_still_written(self, tmp_path):
        import json

        self._drain(
            tmp_path, ({"videoParameters": {"system": "NTSC"}}, [{"seqNo": 1}])
        )

        out = json.loads((tmp_path / "out.tbc.json").read_text())
        assert out["videoParameters"]["system"] == "NTSC"
        assert out["fields"] == [{"seqNo": 1}]
