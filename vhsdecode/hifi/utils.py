from multiprocessing.shared_memory import SharedMemory
from numba import njit
import numba
import numpy as np
from dataclasses import dataclass

import io
import string
from random import SystemRandom

from cProfile import Profile
import ctypes
from pstats import SortKey, Stats

BLOCK_DTYPE = np.int16
REAL_DTYPE = np.float32
ALIGNMENT = 64

NumbaAudioArray = numba.types.Array(numba.types.float32, 1, "C")

# The STREAMINFO total_samples field is 36 bits wide, so captures longer than
# 2^36 samples (~28.6 minutes at 40 MSps) overflow it and the stored count
# wraps around modulo 2^36. libsndfile trusts this count and stops reading
# there, truncating the decode. See parse_flac_streaminfo() below.
FLAC_TOTAL_SAMPLES_FIELD_MOD = 2**36


def parse_flac_streaminfo(file_path):
    """Parse the FLAC STREAMINFO metadata block directly from the file.

    Returns a dict with the STREAMINFO fields and the offset where the
    audio frames start, or None if the file could not be parsed as FLAC.
    """
    try:
        with open(file_path, "rb") as f:
            if f.read(4) != b"fLaC":
                return None

            streaminfo = None
            audio_offset = None
            while True:
                header = f.read(4)
                if len(header) != 4:
                    return None
                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                length = int.from_bytes(header[1:4], "big")

                if block_type == 0 and streaminfo is None:
                    data = f.read(length)
                    if len(data) < 34:
                        return None
                    streaminfo = data
                else:
                    f.seek(length, io.SEEK_CUR)

                if is_last:
                    audio_offset = f.tell()
                    break

            if streaminfo is None or audio_offset is None:
                return None

            d = streaminfo
            return {
                "min_blocksize": int.from_bytes(d[0:2], "big"),
                "max_blocksize": int.from_bytes(d[2:4], "big"),
                "min_framesize": int.from_bytes(d[4:7], "big"),
                "max_framesize": int.from_bytes(d[7:10], "big"),
                "sample_rate": (d[10] << 12) | (d[11] << 4) | (d[12] >> 4),
                "channels": ((d[12] >> 1) & 0x7) + 1,
                "bits_per_sample": (((d[12] & 1) << 4) | (d[13] >> 4)) + 1,
                "total_samples": ((d[13] & 0xF) << 32)
                | int.from_bytes(d[14:18], "big"),
                "audio_offset": audio_offset,
            }
    except OSError:
        return None


def check_flac_header_total_samples(streaminfo, file_size):
    """Check whether the STREAMINFO total_samples count can be trusted.

    A FLAC frame stores at most max_framesize bytes for at least
    min_blocksize samples, so `total_samples` samples can never occupy more
    than (total_samples / min_blocksize + 1) * max_framesize bytes of audio
    payload. If the file holds substantially more audio data than that, the
    36 bit total_samples field overflowed and wrapped (or was never
    finalized), and the header length must not be trusted.

    Returns (header_is_trustworthy, corrected_total_samples or None).
    """
    declared = streaminfo["total_samples"]
    audio_bytes = file_size - streaminfo["audio_offset"]

    if audio_bytes <= 0:
        return True, None

    if declared == 0:
        # length marked as unknown (e.g. the encoder could not seek back to
        # finalize the header)
        return False, None

    min_blocksize = streaminfo["min_blocksize"]
    max_blocksize = streaminfo["max_blocksize"]
    min_framesize = streaminfo["min_framesize"]
    max_framesize = streaminfo["max_framesize"]

    if min_blocksize > 0 and max_framesize > 0:
        # largest possible payload for the declared sample count
        declared_max_bytes = (declared // min_blocksize + 1) * max_framesize
    else:
        # min/max frame sizes may legally be 0 (unknown), fall back to the
        # verbatim worst case (uncompressed samples) plus generous margin
        declared_max_bytes = (
            int(
                declared
                * streaminfo["channels"]
                * (streaminfo["bits_per_sample"] / 8)
                * 1.05
            )
            + 65536
        )

    if audio_bytes <= declared_max_bytes:
        return True, None

    # the header count is impossibly small for the amount of audio data in
    # the file: it wrapped modulo 2^36. try to recover the true count using
    # the frame size statistics, accepting it only if exactly one candidate
    # fits between the possible payload bounds
    corrected = None
    if (
        min_blocksize > 0
        and max_blocksize > 0
        and min_framesize > 0
        and max_framesize > 0
    ):
        lower = audio_bytes / max_framesize * min_blocksize
        upper = audio_bytes / min_framesize * max_blocksize
        candidates = []
        k = 1
        while declared + k * FLAC_TOTAL_SAMPLES_FIELD_MOD <= upper:
            candidate = declared + k * FLAC_TOTAL_SAMPLES_FIELD_MOD
            if candidate >= lower:
                candidates.append(candidate)
            k += 1
        if len(candidates) == 1:
            corrected = candidates[0]

    return False, corrected

@dataclass
class DecoderState:
    def __init__(self, decoder, buffer_name, block_frames_read, block_size, block_num, is_last_block):
        block_sizes = decoder.set_block_sizes(block_size)
        block_overlap = decoder.get_block_overlap()

        self.name = buffer_name
        self.block_num = block_num
        self.is_last_block = is_last_block

        # block data for input rf
        self.block_frames_read = block_frames_read
        self.block_dtype = BLOCK_DTYPE
        self.block_size = block_sizes["block_size"]
        self.block_overlap = block_overlap["block_overlap"]
        self.block_read_overlap = block_overlap["block_read_overlap"]

        # block data for demodulated audio @ 192000Hz
        self.audio_dtype = REAL_DTYPE
        self.block_audio_size = block_sizes["block_audio_size"]

        # block data for resampled audio @ user set audio rate
        self.block_audio_final_size = block_sizes["block_audio_final_size"]
        self.block_audio_final_overlap = block_overlap["block_audio_final_overlap"]

    name: str
    block_num: int
    is_last_block: bool

    block_frames_read: int
    block_dtype: np.dtype
    block_size: int
    block_overlap: int
    block_read_overlap: int

    audio_dtype: np.dtype
    block_audio_size: int

    block_audio_final_size: int
    block_audio_final_overlap: int
        
    @property
    def block_audio_final_len(self):
        if self.is_last_block:
            # shrink the final stereo output to only include the actual frames read, and the overlap that would have been used for the next block
            rf_rate_to_final_rate_ratio = self.block_size / self.block_audio_final_size
            audio_size = round(self.block_frames_read / rf_rate_to_final_rate_ratio)
            return max(50, audio_size + self.block_audio_final_overlap)
        else:
            # don't allow 0 or negative length audio, even if there's more overlap than actual audio
            return max(50, self.block_audio_final_size - self.block_audio_final_overlap * 2)


def to_aligned_offset(size):
    alignment = ALIGNMENT
    offset = size % alignment
    aligned_size = 0 if offset == 0 else alignment - offset
    return size + aligned_size


class PostProcessorSharedMemory:
    def __init__(self, decoder_state: DecoderState):
        self.shared_memory = SharedMemory(name=decoder_state.name)

        self.size = self.shared_memory.size
        self.buf = self.shared_memory.buf
        self.name = self.shared_memory.name
        self.close = self.shared_memory.close
        self.unlink = self.shared_memory.unlink

        self.audio_dtype = decoder_state.audio_dtype
        self.channel_len = decoder_state.block_audio_final_len
        self.audio_dtype_item_size = np.dtype(self.audio_dtype).itemsize

        ### Post Processing Memory
        # |--pre_left--|--pre_right--|--post_left--|--post_right--|
        # |-----------stereo---------|

        # pre left
        self.l_pre_offset = 0
        self.l_pre_len = self.channel_len
        self.l_pre_bytes = self.l_pre_len * self.audio_dtype_item_size
        # pre right
        self.r_pre_offset = to_aligned_offset(self.l_pre_offset + self.l_pre_bytes)
        self.r_pre_len = self.channel_len
        self.r_pre_bytes = self.r_pre_len * self.audio_dtype_item_size

        # overlaps with pre
        ## stereo out
        self.stereo_offset = 0
        self.stereo_len = self.channel_len * 2
        self.stereo_bytes = self.stereo_len * self.audio_dtype_item_size

        ## noise reduction out
        # left
        self.l_post_offset = to_aligned_offset(
            max(
                self.stereo_offset + self.stereo_bytes,
                self.r_pre_offset + self.r_pre_bytes,
            )
        )
        self.l_post_len = self.channel_len
        self.l_post_bytes = self.l_post_len * self.audio_dtype_item_size
        # right
        self.r_post_offset = to_aligned_offset(self.l_post_offset + self.l_post_bytes)
        self.r_post_len = self.channel_len
        self.r_post_bytes = self.r_post_len * self.audio_dtype_item_size

    @staticmethod
    def get_shared_memory(channel_size, name, audio_dtype=REAL_DTYPE):
        byte_size = (
            to_aligned_offset(channel_size * np.dtype(audio_dtype).itemsize * 4)
            + ALIGNMENT * 16
        )

        # allow more than one instance to run at a time
        system_random = SystemRandom()
        name += "_" + "".join(
            system_random.choice(string.ascii_lowercase + string.digits)
            for _ in range(8)
        )

        # this instance must be saved in a variable that persists on both processes
        # Windows will remove the shared memory if it garbage collects the handle in any of the processes it is open in
        # https://stackoverflow.com/a/63717188
        return SharedMemory(size=byte_size, name=name, create=True)

    def get_pre_left(self) -> np.array:
        return np.ndarray(
            self.l_pre_len,
            dtype=self.audio_dtype,
            offset=self.l_pre_offset,
            buffer=self.buf,
            order="C"
        )

    def get_pre_right(self) -> np.array:
        return np.ndarray(
            self.r_pre_len,
            dtype=self.audio_dtype,
            offset=self.r_pre_offset,
            buffer=self.buf,
            order="C"
        )

    # overlaps with the pre audio
    def get_stereo(self) -> np.array:
        return np.ndarray(
            self.stereo_len,
            dtype=self.audio_dtype,
            offset=self.stereo_offset,
            buffer=self.buf,
            order="C"
        )

    def get_post_left(self) -> np.array:
        return np.ndarray(
            self.l_post_len,
            dtype=self.audio_dtype,
            offset=self.l_post_offset,
            buffer=self.buf,
            order="C"
        )

    def get_post_right(self) -> np.array:
        return np.ndarray(
            self.r_post_len,
            dtype=self.audio_dtype,
            offset=self.r_post_offset,
            buffer=self.buf,
            order="C"
        )


class DecoderSharedMemory:
    def __init__(self, decoder_state: DecoderState):
        self.shared_memory = SharedMemory(name=decoder_state.name)

        self.size = self.shared_memory.size
        self.buf = self.shared_memory.buf
        self.name = self.shared_memory.name
        self.close = self.shared_memory.close
        self.unlink = self.shared_memory.unlink

        self.block_dtype = decoder_state.block_dtype
        self.block_dtype_item_size = np.dtype(self.block_dtype).itemsize

        self.audio_dtype = decoder_state.audio_dtype
        self.audio_dtype_item_size = np.dtype(self.audio_dtype).itemsize

        self.block_audio_final_len = decoder_state.block_audio_final_len

        ### Decoder Memory
        # -------------------------------raw_data-------------------------------|
        # RF data is demodulated and raw data can be discarded
        # Output audio data overwrites where the raw data was
        # |--pre_left--|--pre_right--|-------------------empty------------------|
        # |--pre_left--|--pre_right--|-------------------empty------------------|

        ## raw data in
        # first overlap
        self.block_start_overlap_offset = 0
        self.block_start_overlap_len = decoder_state.block_read_overlap
        self.block_start_overlap_bytes = (
            self.block_start_overlap_len * self.block_dtype_item_size
        )
        # block data
        self.block_frames_read = decoder_state.block_frames_read
        self.block_offset = (
            self.block_start_overlap_offset + self.block_start_overlap_bytes
        )
        self.block_len = decoder_state.block_size - (
            decoder_state.block_read_overlap * 2
        )
        self.block_bytes = self.block_len * self.block_dtype_item_size
        # second overlap
        self.block_end_overlap_offset = self.block_offset + self.block_bytes
        self.block_end_overlap_len = decoder_state.block_read_overlap
        self.block_end_overlap_bytes = (
            self.block_end_overlap_len * self.block_dtype_item_size
        )

        # pre left
        self.l_pre_offset = 0
        self.l_pre_len = self.block_audio_final_len
        self.l_pre_bytes = self.l_pre_len * self.audio_dtype_item_size
        # pre right
        self.r_pre_offset = to_aligned_offset(self.l_pre_offset + self.l_pre_bytes)
        self.r_pre_len = self.block_audio_final_len
        self.r_pre_bytes = self.r_pre_len * self.audio_dtype_item_size

    @staticmethod
    def get_shared_memory(
        block_size,
        block_overlap,
        block_audio_final_size,
        name,
        block_dtype=BLOCK_DTYPE,
        audio_dtype=REAL_DTYPE,
    ):
        max_audio_size = (
            block_audio_final_size + to_aligned_offset(block_audio_final_size)
        ) * np.dtype(audio_dtype).itemsize
        block_size = (block_size + block_overlap * 2) * np.dtype(block_dtype).itemsize

        byte_size = max(max_audio_size, block_size)

        # allow more than one instance to run at a time
        system_random = SystemRandom()
        name += "_" + "".join(
            system_random.choice(string.ascii_lowercase + string.digits)
            for _ in range(8)
        )

        # this instance must be saved in a variable that persists on both processes
        # Windows will remove the shared memory if it garbage collects the handle in any of the processes it is open in
        # https://stackoverflow.com/a/63717188
        return SharedMemory(size=byte_size, name=name, create=True)

    ## Decoder methods

    # block data with start and end overlap included
    def get_block(self) -> np.array:
        return np.ndarray(
            self.block_start_overlap_len + self.block_len + self.block_end_overlap_len,
            dtype=self.block_dtype,
            offset=self.block_start_overlap_offset,
            buffer=self.buf,
            order="C"
        )

    # block starts after first overlap, goes until the end of the last overlap
    # first part of the block is copied from the previous read
    def get_block_in(self) -> np.array:
        return np.ndarray(
            self.block_len + self.block_end_overlap_len,
            dtype=self.block_dtype,
            offset=self.block_offset,
            buffer=self.buf,
            order="C"
        )

    # end overlap is copied into the start overlap
    def get_block_in_start_overlap(self) -> np.array:
        return np.ndarray(
            self.block_start_overlap_len,
            dtype=self.block_dtype,
            offset=self.block_start_overlap_offset,
            buffer=self.buf,
            order="C"
        )

    # end overlap is copied and appended to the beginning of the next block
    def get_block_in_end_overlap(self) -> np.array:
        return np.ndarray(
            self.block_end_overlap_len,
            dtype=self.block_dtype,
            offset=self.block_end_overlap_offset,
            buffer=self.buf,
            order="C"
        )

    def get_pre_left(self) -> np.array:
        return np.ndarray(
            self.block_audio_final_len,
            dtype=self.audio_dtype,
            offset=self.l_pre_offset,
            buffer=self.buf,
            order="C"
        )

    def get_pre_right(self) -> np.array:
        return np.ndarray(
            self.block_audio_final_len,
            dtype=self.audio_dtype,
            offset=self.r_pre_offset,
            buffer=self.buf,
            order="C"
        )

    @staticmethod
    @njit(
        numba.types.void(NumbaAudioArray, NumbaAudioArray, numba.types.int64),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_float32(src: np.array, dst: np.array, length: int):
        # ctypes.memmove(dst.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), src.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), length)
        for i in range(length):
            dst[i] = src[i]

    @staticmethod
    @njit(
        numba.types.void(
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.int64,
        ),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_int16(src: np.array, dst: np.array, length: int):
        for i in range(length):
            dst[i] = src[i]

    @staticmethod
    @njit(
        numba.types.void(
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.int64,
            numba.types.int64,
        ),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_dst_offset_int16(
        src: np.array, dst: np.array, dst_offset: int, length: int
    ):
        for i in range(length):
            dst[i + dst_offset] = src[i]

    @staticmethod
    @njit(
        numba.types.void(
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.Array(numba.int16, 1, "C"),
            numba.types.int64,
            numba.types.int64,
        ),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_src_offset_int16(
        src: np.array, dst: np.array, src_offset: int, length: int
    ):
        for i in range(length):
            dst[i] = src[i + src_offset]

    @staticmethod
    @njit(
        numba.types.void(
            NumbaAudioArray,
            NumbaAudioArray,
            numba.types.int64,
            numba.types.int64,
        ),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_dst_offset_float32(
        src: np.array, dst: np.array, dst_offset: int, length: int
    ):
        for i in range(length):
            dst[i + dst_offset] = src[i]

    @staticmethod
    @njit(
        numba.types.void(
            NumbaAudioArray, NumbaAudioArray, numba.types.int64, numba.types.int64
        ),
        cache=True,
        fastmath=True,
        nogil=True,
    )
    def copy_data_src_offset_float32(
        src: np.array, dst: np.array, src_offset: int, length: int
    ):
        for i in range(length):
            dst[i] = src[i + src_offset]

class PeakGain(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_float),
        ("right", ctypes.c_float),
    ]

def profile(function) -> int:
    def run_profiler(*args, **kwarg):
        with Profile() as profiler:
            return_code = function(*args, **kwarg)
            (Stats(profiler).strip_dirs().sort_stats(SortKey.CUMULATIVE).print_stats())
        return return_code

    return run_profiler
