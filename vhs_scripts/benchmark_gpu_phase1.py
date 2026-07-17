#!/usr/bin/env python3
import argparse
import inspect
import os
import statistics
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import vhsdecode.process as process
import vhsdecode.utils as utils


def _make_decoder(system: str, use_gpu: bool, force_cpu: bool):
    return process.VHSRFDecode(
        inputfreq=40,
        system=system,
        rf_options={"use_gpu": use_gpu, "force_cpu": force_cpu},
    )


def _build_input_wave(decoder, system: str, input_dtype: str):
    if system == "PAL":
        max_hz, min_hz = 4_800_000, 3_800_000
    else:
        max_hz, min_hz = 4_400_000, 3_400_000
    half = decoder.blocklen // 2
    wavemax = utils.gen_wave_at_frequency(max_hz / 1_000_000, 40, half)
    wavemin = utils.gen_wave_at_frequency(min_hz / 1_000_000, 40, half)
    wave = np.concatenate((wavemax, wavemin))
    if input_dtype == "int-like":
        # Mirror integer captures (u8/u16/s16 loaders): both backends promote
        # to float64/complex128, so this is the exact-equivalence mode.
        return (wave * 16000.0).astype(np.int16)
    if input_dtype == "float32":
        return wave.astype(np.float32)
    return wave


def _supports_profile(decoder) -> bool:
    try:
        return "profile" in inspect.signature(decoder.demodblock).parameters
    except (TypeError, ValueError):
        return False


def _run_benchmark(decoder, wave, warmup: int, repeats: int, profile=None):
    kwargs = {}
    if profile is not None:
        kwargs["profile"] = profile
    for _ in range(warmup):
        decoder.demodblock(data=wave, **kwargs)
    if profile is not None:
        profile.clear()
    times = []
    output = None
    for _ in range(repeats):
        start = time.perf_counter()
        output = decoder.demodblock(data=wave, **kwargs)
        times.append(time.perf_counter() - start)
    return times, output["video"]


def _run_threaded(decoder, wave, threads: int, repeats: int) -> float:
    """Aggregate blocks/sec with N threads sharing one decoder, mirroring how
    DemodCache workers share a single RF decoder in real decode runs."""
    barrier = threading.Barrier(threads + 1)
    done = []

    def worker():
        barrier.wait()
        for _ in range(repeats):
            decoder.demodblock(data=wave)
        done.append(True)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    barrier.wait()
    start = time.perf_counter()
    for t in pool:
        t.join()
    elapsed = time.perf_counter() - start
    return (threads * repeats) / elapsed


def _compare_video_outputs(cpu_video, gpu_video):
    fields = ["demod", "demod_05", "demod_burst", "envelope"]
    stats = {}
    for field in fields:
        cpu = np.asarray(cpu_video[field], dtype=np.float64)
        gpu = np.asarray(gpu_video[field], dtype=np.float64)
        diff = np.abs(cpu - gpu)
        stats[field] = {
            "max_abs_diff": float(np.max(diff)),
            "mean_abs_diff": float(np.mean(diff)),
        }
    return stats


def _equivalence_thresholds(env_mean: float, input_dtype: str):
    """Per-array bounds. Outputs are Hz-scale (~3.4-4.8e6); the measured
    cuFFT-vs-pocketfft rounding floor is ~0.3 Hz, so 5 Hz (~1e-6 relative,
    ~0.0007 IRE) is a tight-but-honest float64 bound. Bit equality across FFT
    implementations is impossible and not the contract."""
    if input_dtype == "float32":
        # CPU reference runs complex64 here while the GPU runs float64; the
        # CPU rounding dominates the diff.
        demod = (200.0, 20.0)
    else:
        demod = (5.0, 0.5)
    return {
        "demod": demod,
        "demod_05": demod,
        "demod_burst": (1.0, 0.1),
        "envelope": (max(1e-4 * env_mean, 1e-9), max(1e-5 * env_mean, 1e-10)),
    }


def _check_equivalence(diff_stats, thresholds):
    overall_pass = True
    for field_name, field_stats in diff_stats.items():
        max_bound, mean_bound = thresholds[field_name]
        max_abs = field_stats["max_abs_diff"]
        mean_abs = field_stats["mean_abs_diff"]
        ok = max_abs <= max_bound and mean_abs <= mean_bound
        overall_pass &= ok
        print(
            f"{field_name}: max_abs_diff={max_abs:.6f} (<={max_bound:g}) "
            f"mean_abs_diff={mean_abs:.6f} (<={mean_bound:g}) "
            f"{'ok' if ok else 'FAIL'}"
        )
    return overall_pass


def _print_backend_info(decoder, label: str):
    backend = decoder.gpu_backend
    print(f"{label} backend: {backend.name}")
    if backend.gpu_name:
        print(f"{label} GPU: {backend.gpu_name}")
    if backend.reason:
        print(f"{label} reason: {backend.reason}")
    if backend.active:
        try:
            import cupy as cp

            print(f"{label} CuPy version: {cp.__version__}")
            print(
                f"{label} CUDA runtime version: {cp.cuda.runtime.runtimeGetVersion()}"
            )
        except Exception as exc:
            print(f"{label} backend metadata unavailable: {exc}")


def _print_profile(profile, label: str, repeats: int):
    if not profile:
        print(f"{label} stage profile: no data")
        return
    total = sum(profile.values())
    print(f"{label} stage profile (ms/block, mean over {repeats} runs):")
    for stage, seconds in sorted(profile.items(), key=lambda kv: -kv[1]):
        print(
            f"  {stage}: {1000 * seconds / repeats:.3f} "
            f"({100 * seconds / total:.1f}%)"
        )


def _numba_reference_video(system: str, wave):
    """Extra untimed CPU pass with the exact-f64 numba demodulator forced,
    for equivalence when vhsd_rust (approximate f32 atan2) is installed."""
    import vhsdecode.demod as demod_mod

    had_rust = demod_mod._HAS_VHSD_RUST
    demod_mod._HAS_VHSD_RUST = False
    try:
        decoder = _make_decoder(system, use_gpu=False, force_cpu=True)
        return decoder.demodblock(data=wave)["video"]
    finally:
        demod_mod._HAS_VHSD_RUST = had_rust


def main():
    parser = argparse.ArgumentParser(
        description="CPU vs GPU benchmark + equivalence check for VHS demodblock."
    )
    parser.add_argument("--system", choices=["PAL", "NTSC"], default="PAL")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument(
        "--input-dtype",
        choices=["float64", "float32", "int-like"],
        default="int-like",
        help="Synthetic input sample type; int-like mirrors u8/u16/s16 captures.",
    )
    parser.add_argument(
        "--reference",
        choices=["auto", "numba"],
        default="numba",
        help="Equivalence reference: numba forces the exact-f64 demodulator for "
        "an extra untimed CPU pass; auto compares against the production CPU "
        "dispatch (vhsd_rust when installed).",
    )
    parser.add_argument(
        "--profile-stages",
        action="store_true",
        help="Report per-stage timings when demodblock supports profiling.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Also measure aggregate blocks/sec with N threads sharing a decoder.",
    )
    args = parser.parse_args()

    cpu_decoder = _make_decoder(args.system, use_gpu=False, force_cpu=True)
    gpu_decoder = _make_decoder(args.system, use_gpu=True, force_cpu=False)

    _print_backend_info(cpu_decoder, "CPU")
    _print_backend_info(gpu_decoder, "GPU")
    print(f"Input dtype mode: {args.input_dtype}")

    wave = _build_input_wave(cpu_decoder, args.system, args.input_dtype)

    cpu_profile = gpu_profile = None
    if args.profile_stages:
        if _supports_profile(cpu_decoder):
            cpu_profile, gpu_profile = {}, {}
        else:
            print("Stage profiling not supported by this demodblock build.")

    cpu_times, cpu_video = _run_benchmark(
        cpu_decoder, wave, args.warmup, args.repeats, cpu_profile
    )
    gpu_times, gpu_video = _run_benchmark(
        gpu_decoder, wave, args.warmup, args.repeats, gpu_profile
    )

    cpu_median = statistics.median(cpu_times)
    gpu_median = statistics.median(gpu_times)
    speedup = cpu_median / gpu_median if gpu_median > 0 else float("inf")

    print(f"CPU median seconds/block: {cpu_median:.6f}")
    print(f"GPU median seconds/block: {gpu_median:.6f}")
    print(f"CPU blocks/sec: {1.0 / cpu_median:.2f}")
    print(f"GPU blocks/sec: {1.0 / gpu_median if gpu_median > 0 else float('inf'):.2f}")
    print(f"Speedup (CPU/GPU): {speedup:.2f}x")

    if cpu_profile is not None:
        _print_profile(cpu_profile, "CPU", args.repeats)
        _print_profile(gpu_profile, "GPU", args.repeats)

    if args.threads > 0:
        cpu_tps = _run_threaded(cpu_decoder, wave, args.threads, args.repeats)
        gpu_tps = _run_threaded(gpu_decoder, wave, args.threads, args.repeats)
        print(f"CPU blocks/sec ({args.threads} threads): {cpu_tps:.2f}")
        print(f"GPU blocks/sec ({args.threads} threads): {gpu_tps:.2f}")

    reference_video = cpu_video
    if args.reference == "numba":
        import vhsdecode.demod as demod_mod

        if demod_mod._HAS_VHSD_RUST:
            print("Equivalence reference: numba (vhsd_rust bypassed)")
            reference_video = _numba_reference_video(args.system, wave)

    diff_stats = _compare_video_outputs(reference_video, gpu_video)
    env_mean = float(np.mean(np.asarray(reference_video["envelope"], dtype=np.float64)))
    thresholds = _equivalence_thresholds(env_mean, args.input_dtype)
    overall_pass = _check_equivalence(diff_stats, thresholds)

    print(f"Equivalence pass: {overall_pass}")
    return 0 if overall_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
