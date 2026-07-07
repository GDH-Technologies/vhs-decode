#!/usr/bin/env python3
import argparse
import os
import statistics
import sys
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


def _build_input_wave(decoder, system: str):
    if system == "PAL":
        max_hz, min_hz = 4_800_000, 3_800_000
    else:
        max_hz, min_hz = 4_400_000, 3_400_000
    half = decoder.blocklen // 2
    wavemax = utils.gen_wave_at_frequency(max_hz / 1_000_000, 40, half)
    wavemin = utils.gen_wave_at_frequency(min_hz / 1_000_000, 40, half)
    return np.concatenate((wavemax, wavemin))


def _run_benchmark(decoder, wave, warmup: int, repeats: int):
    for _ in range(warmup):
        decoder.demodblock(data=wave)
    times = []
    outputs = []
    for _ in range(repeats):
        start = time.perf_counter()
        out = decoder.demodblock(data=wave)
        times.append(time.perf_counter() - start)
        outputs.append(out["video"])
    return times, outputs[-1]


def _compare_video_outputs(cpu_video, gpu_video):
    fields = ["demod", "demod_05", "demod_burst", "envelope"]
    stats = {}
    for field in fields:
        cpu = np.asarray(cpu_video[field])
        gpu = np.asarray(gpu_video[field])
        diff = np.abs(cpu - gpu)
        stats[field] = {
            "max_abs_diff": float(np.max(diff)),
            "mean_abs_diff": float(np.mean(diff)),
        }
    return stats


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


def main():
    parser = argparse.ArgumentParser(
        description="Phase 1 GPU benchmark for VHS demodblock FFT acceleration."
    )
    parser.add_argument("--system", choices=["PAL", "NTSC"], default="PAL")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--rtol", type=float, default=1e-4)
    parser.add_argument("--atol", type=float, default=5e-2)
    args = parser.parse_args()

    cpu_decoder = _make_decoder(args.system, use_gpu=False, force_cpu=True)
    gpu_decoder = _make_decoder(args.system, use_gpu=True, force_cpu=False)

    _print_backend_info(cpu_decoder, "CPU")
    _print_backend_info(gpu_decoder, "GPU")

    wave = _build_input_wave(cpu_decoder, args.system)

    cpu_times, cpu_video = _run_benchmark(cpu_decoder, wave, args.warmup, args.repeats)
    gpu_times, gpu_video = _run_benchmark(gpu_decoder, wave, args.warmup, args.repeats)

    cpu_median = statistics.median(cpu_times)
    gpu_median = statistics.median(gpu_times)
    speedup = cpu_median / gpu_median if gpu_median > 0 else float("inf")
    blocks_per_sec_cpu = 1.0 / cpu_median
    blocks_per_sec_gpu = 1.0 / gpu_median if gpu_median > 0 else float("inf")

    print(f"CPU median seconds/block: {cpu_median:.6f}")
    print(f"GPU median seconds/block: {gpu_median:.6f}")
    print(f"CPU blocks/sec: {blocks_per_sec_cpu:.2f}")
    print(f"GPU blocks/sec: {blocks_per_sec_gpu:.2f}")
    print(f"Speedup (CPU/GPU): {speedup:.2f}x")

    diff_stats = _compare_video_outputs(cpu_video, gpu_video)
    overall_pass = True
    for field_name, field_stats in diff_stats.items():
        max_abs_diff = field_stats["max_abs_diff"]
        mean_abs_diff = field_stats["mean_abs_diff"]
        print(
            f"{field_name}: max_abs_diff={max_abs_diff:.6f} mean_abs_diff={mean_abs_diff:.6f}"
        )
        if not np.isclose(max_abs_diff, 0.0, rtol=args.rtol, atol=args.atol):
            overall_pass = False

    print(f"Equivalence pass: {overall_pass}")
    return 0 if overall_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
