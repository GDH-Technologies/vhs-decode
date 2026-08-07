# CLAUDE.md

Project context for Claude Code sessions in the GDH-Technologies fork of vhs-decode.
This file carries org-specific operational knowledge; see README.md / BUILD.md / INSTALL.md
for general project documentation.

## Repo identity & git workflow

- This is **GDH-Technologies/vhs-decode**, a fork of oyvindln/vhs-decode (itself derived
  from happycube/ld-decode). Upstream development happens on oyvindln's `vhs_decode`
  branch; our integration branch is `main`, periodically merged from upstream.
- **Never push to oyvindln/vhs-decode or happycube/ld-decode. Push only to the
  GDH-Technologies fork.** Remote *names* differ per machine (workflow-master:
  `fork` = GDH, `origin` = oyvindln, `upstream` = happycube; lws: `origin` = GDH,
  `upstream` = oyvindln) — always identify the push target by URL via `git remote -v`,
  never by remote name.
- Versioning is setuptools_scm from upstream tags. If builds stamp `0.3.8.x.devNNN`
  instead of `0.4.x.devNN`, the clone is missing upstream tags — fetch `--tags` from the
  oyvindln remote (cosmetic otherwise).

## Fleet deployment (pipx)

- Capture-fleet hosts install this repo via pipx with extras `[intel,hifi_gui_qt6]`.
  On workflow-master the install is **editable** (`--editable` in pip_args, verified
  2026-07-21): pure-Python edits are live in the installed CLIs immediately; rebuild only
  for Rust/Cython changes. `pipx install --force` **without `-e` silently reverts to a
  regular install.** Check each host's pipx metadata before assuming its layout.
- cupy-cuda13x + nvidia libs were added to the venv manually and are **not in the pipx
  spec** (injected_packages empty): they survive `pipx install --force` but
  `pipx reinstall` drops GPU support. Adding a `cuda13` extra to the spec would make them
  durable.
- **Regular→editable switch gotcha** (hit on two hosts, expect it on any): the old
  install's numba `.nbc`/`.nbi` and `.pyc` caches leave `__init__.py`-less husk dirs in
  site-packages (`vhsdecode/`, `vhsdecode/addons/`, `vhsdecode/hifi/`; also check
  `lddecode`, `cvbsdecode`, `filter_tune`). PathFinder resolves them as namespace
  packages *before* setuptools' editable finder, so plain submodules load from the repo
  but subpackages raise ModuleNotFoundError. Fix: `rm -rf` the cache-only dirs.
  Diagnose from a neutral cwd — importing from inside the repo masks the problem because
  cwd is on sys.path.
- **Editable installs never build `vhsd_rust`** (verified 2026-08-07 by A/B-ing throwaway
  venvs). A regular `pip install .` compiles it and drops
  `vhsd_rust.cpython-3xx-*.so` into site-packages; `pip install -e .` does not build it at
  all, in either lenient or strict (`--config-settings editable_mode=strict`) mode —
  setuptools documents editable support as "primarily restricted to Python modules".
  So workflow-master (editable) silently takes the scipy fallback in `sosfiltfilt_rust`
  for **both video and hifi** decodes; fleet hosts on regular installs get the rust path.
  Check from a neutral cwd:
  `cd /tmp && python -c "import vhsdecode.rust_utils as r; print(r._HAS_VHSD_RUST)"`
  — inside the repo it always prints True because cwd is on sys.path and a `.so` sits at
  the repo root. Remedy: build a wheel/regular install and copy the resulting
  `vhsd_rust*.so` into the pipx venv's site-packages.
- **swiftly's clang shadows `/usr/bin/clang` and breaks every rebuild** (workflow-master,
  verified 2026-08-07). `/home/rdodge/.local/share/swiftly/bin` precedes `/usr/bin` on
  PATH, and `setup.py` compiles with `-flto`; the Swift toolchain ships no `LLVMgold.so`,
  so linking dies with `LLVMgold.so: cannot open shared object file`. Until the fixed
  `setup.py` (which uses `os.environ.setdefault`) is deployed, exporting `CC` does **not**
  help — the old code overwrote it with the bare name `clang`, re-resolving through PATH.
  Workaround on the old code: drop swiftly from PATH for the build. With the fix:
  `CC=/usr/bin/clang CXX=/usr/bin/clang++ pipx install --force ...`.

## GPU-resident demodblock (`feat/gpu-resident-demodblock`, fork PR #4)

- Makes demodblock GPU-resident; CPU path byte-identical, 100-frame Puppy Test
  end-to-end bit-identical.
- Measured verdicts (2026-07): cupyx `sosfiltfilt` is 4–9× *slower* than CPU for single
  32k blocks, so IIR stays on CPU via the `_gpu_iir_steps` table (re-A/B with
  `--gpu-iir`). Multi-threaded GPU is GIL/launch-bound at ~300 blocks/s flat regardless
  of `-t` — GPU wins on weak-CPU hosts, strong CPUs win multi-threaded. Benchmarks on
  shared boxes are noisy; trust within-run A/Bs only.
- `CUDA_PATH` must be resolved before CuPy's first touch — `create_backend` auto-probes
  `/usr/local/cuda`.
- A stale-ish `vhsd_rust.cpython-314*.so` sits at the repo **root** — importable only
  when the repo root is on sys.path (tests/benchmarks yes, deployed CLI no), and its
  `unwrap_hilbert` is an approximate f32-atan2, so equivalence tests must pin the numba
  reference.
- `tau = np.pi*2` in `lddecode/utils.py` (commit 9b5f071e) is required for the numba
  unwrap to compile — keep it through upstream merges.
- Pre-existing pytest baseline: 4 failures + 3 errors, all from test-data files
  (PAL_GOOD.txt.gz etc.) removed by upstream's pytest migration. Compare failure *sets*
  against baseline, not counts, when gating changes.
- Parked follow-ups: launch batching / `cp.fuse` for thread scaling, cupyx `filtfilt`
  gate for the Betamax fsc notch, HiFi pipeline unported.

## Interface contracts with digitization-toolkit

The org's digitization-toolkit drives these CLIs on the capture fleet. Breaking any of
these silently breaks fleet decode jobs:

- `--recheck_phase` was deleted upstream 2026-03-12 (its behavior is now default);
  passing it makes vhs-decode **exit 2**. The toolkit tombstone-pops it from legacy
  presets — do not reintroduce the flag.
- vhs-decode emits no total-frame estimate for VHS-family decodes (`est_frames` only
  threads through on the ld-decode laserdisc path); downstream progress bars ffprobe the
  parallel reference capture for a denominator.
- The toolkit parses timestamped `File Frame` lines for rolling fps/ETA and the final
  `Took ... FPS post-setup` line for completion-rate comparison. Changing these log
  lines breaks fleet progress reporting.
- GPU decode (`--use-gpu`) additionally requires cupy present in that host's vhs-decode
  pipx venv (see Fleet deployment above).

## Output conventions

- NTSC decodes follow the TFF field-order convention (verify per tape with
  `ffmpeg -filter:v idet`). Downstream QTGMC deinterlace tooling lives with the capture
  archives, not in this repo.
