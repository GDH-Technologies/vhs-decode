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

- Capture-fleet hosts install this repo via pipx. `pipx install --force` **without `-e`
  silently reverts to a regular install.** Check each host's pipx metadata before assuming
  its layout:
  `pipx list --json | jq '.venvs["vhs-decode"].metadata.main_package | {package_or_url, pip_args}'`
- Fleet roster (verified 2026-08-18): **workflow-master, lws, wf1** — Fedora, regular
  installs of `~/Repos/vhs-decode[intel,hifi_gui_qt6,cuda13]`; **air0** — macOS, Homebrew
  pipx at `/opt/homebrew/bin/pipx` (not on non-interactive PATH), regular install of
  `~/Repos/vhs-decode` with no extras, venv under `~/Library/Application Support/pipx`.
  cs0/cs1 have no vhs-decode (cs0 has the repo + pipx but nothing installed; cs1 nothing).
  win-node-0 was unreachable on 2026-08-18 and did not get that day's update.
  lws and wf1 were **editable until 2026-08-18**, then converted to regular installs for
  the same vhsd_rust reason as workflow-master below.
- Extras drift happens: workflow-master's spec had silently drifted to `[hifi_gui_qt6]`
  alone (no intel/cuda13, so no cupy and no `--use-gpu`) before being restored on
  2026-08-18. Verify the metadata; don't trust these notes for the current spec.
- workflow-master was editable until 2026-08-07 and is now a **regular** install
  (`.[intel,hifi_gui_qt6,cuda13]`, no `--editable`), chosen so `vhsd_rust` gets built —
  see the editable/`vhsd_rust` bullet below. Consequence: pure-Python edits in the repo no
  longer reach the installed CLIs, so **rebuild after every change**, not just Rust/Cython
  ones.
- GPU support is durable now: `cuda13` (also `cuda12`/`cuda11`) is a real extra in
  `pyproject.toml`, and workflow-master's pipx spec includes it. cupy no longer depends on
  hand-injection surviving, so `pipx reinstall` is safe.
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
  Any editable host therefore silently takes the scipy fallback in `sosfiltfilt_rust` for
  **both video and hifi** decodes; regular installs get the rust path. This is why
  workflow-master was moved to a regular install on 2026-08-07.
  Check from a neutral cwd:
  `cd /tmp && python -c "import vhsdecode.rust_utils as r; print(r._HAS_VHSD_RUST)"`
  — inside the repo it always prints True because cwd is on sys.path and a `.so` sits at
  the repo root. Remedy: build a wheel/regular install and copy the resulting
  `vhsd_rust*.so` into the pipx venv's site-packages.
- **A shadowing toolchain on PATH breaks `-flto` builds** (hit on workflow-master via
  swiftly, verified 2026-08-07). The Swift toolchain ships `clang`, `clang++`, `lld` and
  `clangd`, but no `LLVMgold.so` — the LTO plugin `ld.bfd` needs. Its clang *compiles*
  fine and only dies at link with `LLVMgold.so: cannot open shared object file`, so the
  failure looks like a compiler bug rather than a PATH problem. conda and nix ship
  similarly incomplete toolchains; expect this class again.
  `setup.py` now **probes** instead of trusting PATH: it tries a real `-flto` link with
  each `clang` on PATH and takes the first that works (absolute path), falling back to the
  compiler that built Python and dropping `-flto` — with a warning — if nothing can LTO.
  An explicitly set `CC` is always respected; only the `-flto` decision is probed.
  So no `CC=` incantation is needed any more. Note the earlier `os.environ.setdefault`
  fix was **insufficient**: it only helped when the caller set `CC`, and the normal
  `pipx install` path does not.
- workflow-master's `~/.bash_profile` additionally moves swiftly's bin to the **end** of
  PATH (it sources swiftly's `env.sh` for the `SWIFTLY_*` vars, then relocates
  `$SWIFTLY_BIN_DIR`). That un-shadows `/usr/bin/clang` for every other project on the
  box; `swift`/`swiftly` still resolve. Backups: `~/.bash_profile.bak.*`.

## CI (fork-local, self-hosted)

Full detail in **`.github/GDH_SELFHOSTED_CI.md`** — read it before touching CI. Summary:

- All 14 workflows inherited from upstream are **disabled as a repo setting**
  (`gh workflow disable`), not by editing files, so upstream merges stay conflict-free.
  That state is invisible in the tree; the doc above is the only record of it.
- `.github/workflows/deploy-self-hosted.yml` is the only CI that runs. Build + unit tests
  on `wm` for same-repo PRs and dispatches; a merge to `main` also pipx-reinstalls
  vhs-decode on `wm`, `wf1` and `lws`.
- **This fork is public** and the org runner group allows public repos, so the `verify` job
  carries a same-repo guard (`head.repo.full_name == github.repository`) — without it any
  fork PR would run arbitrary code as `rdodge` on the fleet. MISRC-GUI's equivalent
  workflow has no such guard because that fork is private; do not copy between them
  without re-checking visibility. Fork-PR approval is set to `all_external_contributors`.
- The deploy step **reads each node's existing pipx extras** and reuses them rather than
  imposing one spec, per the extras-drift warning above. It installs from the runner
  workspace, so after the first CI deploy a node's `package_or_url` points there rather
  than at `~/Repos/vhs-decode`.
- Offline nodes (usually `lws`) **queue rather than fail** — up to 24h. `wf1` and `lws` are
  `continue-on-error`, so only `wm` can turn a merge red.

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
- ~~Pre-existing pytest baseline: 4 failures + 3 errors, all from test-data files
  (PAL_GOOD.txt.gz etc.) removed by upstream's pytest migration.~~ **Wrong — corrected
  2026-08-31.** Those files were never removed; `tests/data` is a *submodule*
  (eshaz/vhs-decode-testdata-ci, ~300 MB) and the failures are simply what an
  uninitialized submodule looks like. `tests/unit` reaches it through the `data_dir`
  fixture in `tests/conftest.py`, not a literal path, so grepping the tests for
  `tests/data` finds nothing and the dependency looks absent. Run
  `git submodule update --init --depth 1 tests/data` and the suite is **66 passed,
  12 skipped, 0 failed**. There is no baseline to compare failure sets against — gate on
  green.
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

## MISRC-GUI (capture tool, GDH-Technologies/MISRC-GUI fork at ~/Repos/MISRC-GUI)

- RF FLAC convention: STREAMINFO `sample_rate` is stored in **kHz** (FLAC's 20-bit rate
  field caps at 655,350 Hz; 40 MSps → 40000). True values live in the vorbis tags
  (`RF_SAMPLE_RATE`, `RF_TOTAL_SAMPLES`, `DURATION_SECONDS`). Header-derived durations
  therefore read 1000× long in naive players — expected, not a bug.
- Builds ≤ v1.0.7-25 wrote STREAMINFO `total_samples` scaled ÷1000, so libsndfile-trusting
  readers silently decoded 1/1000th of the capture with exit 0 (hifi-decode: 61 ms of a
  60.8 s tape). Fixed writer-side in MISRC-GUI PR #1 (v1.0.7-27). Reader-side, hifi-decode
  falls back to ffmpeg on implausible header frame counts since vhs-decode fork PR #7, so
  captures from the buggy builds still decode fully (with a WARN). Captures > 2^36 samples
  (~28.6 min at 40 MSps) now get `total_samples = 0` ("unknown") instead of a wrapped count.
- workflow-master install: `meson install -C build-fedora` with prefix `~/.local` (set
  2026-08-18) → `misrc_gui`/`misrc_capture`/`misrc_extract` in `~/.local/bin`. The binaries
  rpath-link vendored hsdaoh from `~/Repos/MISRC-GUI/.deps/install` — **the repo dir must
  stay put** or the installed binaries break. Upgrade = `git pull` + `meson compile` +
  `meson install`; do not rerun `scripts/build-fedora.sh` on an existing build dir (it
  `--wipe`s the configuration, including the `~/.local` prefix).
- GNOME launcher: `~/.local/share/applications/misrc-gui.desktop`, icon copied to
  `~/.local/share/icons/misrc-gui.png`. `StartupWMClass` embeds the version string
  (`MISRC Capture v...`, matching the CI AppImage convention) — refresh it after upgrades
  or dock/window matching silently breaks.

## Output conventions

- NTSC decodes follow the TFF field-order convention (verify per tape with
  `ffmpeg -filter:v idet`). Downstream QTGMC deinterlace tooling lives with the capture
  archives, not in this repo.
