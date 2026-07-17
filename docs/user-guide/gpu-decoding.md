# GPU-accelerated decoding (CUDA)

vhs-decode can run the video demodulation hot path on an NVIDIA GPU via
[CuPy](https://cupy.dev). This accelerates the per-block FM demodulation
pipeline (RF filtering, envelope, FM demod, de-emphasis, demod_05) while
keeping the CPU path bit-for-bit unchanged when not enabled.

## Usage

```bash
vhs-decode --tf VHS --use-gpu <infile> <outfile>
```

- `--use-gpu` — enable the CUDA backend. If CuPy or a CUDA device is
  unavailable, decoding transparently falls back to the CPU and logs the
  reason at startup (`Using CPU backend: numpy (...)`).
- `--force-cpu` — force the CPU path even if a GPU is available.

## Installation

Install the CuPy extra matching your CUDA toolkit major version:

```bash
pip install "vhs-decode[cuda13]"   # or cuda12 / cuda11
```

The runtime compiles a few kernels via NVRTC and needs the CUDA headers. If
`CUDA_PATH`/`CUDA_HOME` are unset, `/usr/local/cuda` is probed automatically;
set `CUDA_PATH` explicitly for toolkits installed elsewhere. When the header
lookup fails, decode still works — the affected filter steps stay on the CPU
and the startup log says so.

## Fidelity

The GPU path runs the same algorithms in float64 end to end. Differences
versus the CPU path are bounded at floating-point rounding level (sub-Hz on
MHz-scale demodulated output, i.e. well below one 16-bit TBC quantization
step); an end-to-end 100-frame VHS comparison produced bit-identical `.tbc`
files. Exact bit equality of intermediate math is not promised — cuFFT and
NumPy's FFT round differently.

`python tests.py --benchmark-gpu` runs a CPU-vs-GPU benchmark and
equivalence check (`--profile-stages`, `--threads N`, and `--gpu-iir` give
per-stage timings, thread-contention numbers, and IIR placement A/Bs).

## Performance notes

- Single-thread demodulation is roughly 2-2.7x faster than one CPU thread
  (measured on an RTX 3080 with 32k blocks). Zero-phase IIR filtering
  (`sosfiltfilt`) measured 4-9x *slower* on the GPU for single blocks, so
  those steps intentionally stay on the CPU with transfers overlapped.
- Worker threads each get their own CUDA stream, but multi-threaded GPU
  throughput is currently bound by Python kernel-launch overhead, so it does
  not scale with `-t` the way the CPU path does. On machines with many fast
  cores the CPU path at `-t 8+` can still win; the GPU path shines where CPU
  cores are the bottleneck.
- Expect on the order of ~0.8 GB of GPU memory per worker thread (CuPy
  memory pools plus FFT plans).
