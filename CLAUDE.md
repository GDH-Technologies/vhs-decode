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
- Versioning is setuptools_scm from upstream tags. If builds stamp `0.3.8.x.devNNN` or
  `0.1.devNNNN` instead of `0.4.x.devNN`, the clone is missing upstream tags — fetch
  `--tags` from the oyvindln remote (cosmetic otherwise).
- **The GDH fork itself carried no tags until 2026-08-31.** Every fleet host had them
  locally (fetched by hand), so this stayed invisible until CI started cloning from the
  fork and stamped `0.1.dev5678`. The 53 upstream tags were pushed to the fork
  (`git push fork --tags`), so `fetch-depth: 0` now resolves a real version. If a fresh
  clone of the fork ever stamps `0.1.devN` again, the tags were lost — re-push them.

## Upstream syncs

- Merges from oyvindln's `vhs_decode` follow the generic sync-fork procedure with this
  repo's profile at `.claude/skills/sync-upstream/SKILL.md`. That file is local-only
  (`.claude/` is untracked here), so it exists on wm only.
- **The `.tbc.db` DDL lives in `lddecode/tbc_db.py` (`SCHEMA_SQL`)**, not in
  `LDdecode.create_db_schema`. Upstream still edits its inline copy there, so each such
  edit conflicts with the fork's deletion. Port it into `SCHEMA_SQL` *and*
  `migrate_schema`. Upstream's INSERTs name the new columns, so a missed port breaks
  every ld-decode run at its first field (2026-09-15: `field_record.ac3_symbols`, added
  nullable with no `user_version` bump, as upstream did).
- **Keep `static_ffmpeg.add_paths(weak=True)`** in `lddecode/utils.py`. Non-weak mode
  downloads static-ffmpeg and puts it ahead of the host's ffmpeg (wm:
  `/usr/local/bin/ffmpeg`) for every decode, FLAC RF reads included. Upstream lost `weak`
  in its 2026-09-13 happycube merge and is restoring it on its `ffmpeg_static_weak`
  branch. Drop the fork's one-line fix once that lands.

## Field numbering in `.tbc.json` / `.tbc.db`

Every consumer indexes a field by its number — tbc-tools returns `fields[n - 1]`, and the
SQLite path keys on `field_id = seqNo - 1` — so the metadata is only readable while
`fields[i]["seqNo"] == i + 1` **and** there is one record per field image on disk.

- **`seqNo` is stamped in `writeout()` on a per-write copy**, in all three decoders (fork
  since `6b804ee2`, 2026-09-06; pinned by `tests/unit/test_field_numbering.py`). Upstream
  still stamps it in `buildmetadata()` and appends the same dict, so its duplicate-field
  compensation (`readfield` writes the previous field a second time) emits
  `…59, 60, 59, 61, 63…` — VHS-C_03_Recital's shape, and the `ld-analyse` core dump once
  that JSON was converted to a `.tbc.db`. Keep the writeout stamp through every upstream
  merge; upstream has not fixed it (checked 2026-09-15). The defect was the filler path,
  not `--resume` — toolkit #419 and tbc-tools #22 attribute it to resume, wrongly.
- **Shape A** (a repeated number and a skipped one, array length still equal to the
  payload) loses nothing: the repeat is a real duplicated field image. **Renumber in place;
  never drop the repeat.** tbc-tools' `--repair` drops it and then names the wrong image
  for every field after the break. **Shape B** (array short of the payload; JJ_Promo,
  81641 records for 81653 images, older builds) needs placeholders instead — renumbering
  is wrong there too.
- **`isDuplicateField` keeps upstream's placement.** It sits on the field that *tripped*
  the compensation, together with `decodeFaults` bit 4 and `syncConf = 0`, and means "the
  action taken at this skipped field was duplicate-previous" (the other `field_order_action`
  outcomes, drop and flip, leave no flag). The inserted copy is the record *before* the
  flagged one. Do not move the flag: tbc-tools' `tbc-segments` reads it on the record that
  carries the fault bit, and upstream's JSON is a de-facto contract for third-party readers.
  The fork's own `duplicate_field` decoder event names the copy's index exactly.
- **`--resume` guards** (`lddecode/tbc_db.py`): a holed `field_record` (`MAX(field_id)+1 !=
  COUNT(*)`) is refused — the seam is placed by count but rows are read by id — and a
  misnumbered `.tbc.json` prefix is renumbered from position before seeding, with a
  warning. The fork's DB-writing decodes always stamped at writeout, so only a hand-made
  database reaches either guard.
- **Post-run audit** (`tbc_db.audit_outputs`, called from `cleanup()` in
  `vhsdecode/main.py`): reconciles writeouts, records, the `.tbc`/chroma sizes, the
  `field_record` rows and span, and the finished `.tbc.json`'s count and numbering. It
  **warns and never fails**. `ld-decode`/`cvbs-decode` have no audit.

## Fleet deployment (pipx)

- Capture-fleet hosts install this repo via pipx. `pipx install --force` **without `-e`
  silently reverts to a regular install.** Check each host's pipx metadata before assuming
  its layout:
  `pipx list --json | jq '.venvs["vhs-decode"].metadata.main_package | {package_or_url, pip_args}'`
- Fleet roster (verified 2026-08-18): **workflow-master, lws, wf1** — Fedora, regular
  installs of `~/Repos/vhs-decode[intel,hifi_gui_qt6,cuda13]`; **air0** — macOS/Apple
  Silicon, user `dodge` (not `rdodge`), Homebrew pipx at `/opt/homebrew/bin/pipx` (not on a
  non-interactive ssh PATH, but the runner's launchd `.path` does carry `/opt/homebrew/bin`),
  regular install of `~/Repos/vhs-decode`, venv under `~/Library/Application Support/pipx`
  (note the space). Its extras were **none** until 2026-08-31, now
  `[hifi_gui_qt6,hifi_gnuradio]`. `cargo` there is Homebrew's, not rustup's — there is no
  `~/.cargo/bin`. air0 also has upstream's prebuilt `.dmg` at `/Applications/decode.app`; it
  exports nothing onto PATH and CI does not manage it, so it stays frozen at whatever
  release was last installed by hand.
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
- **macOS 27's dyld rejects a stripped `vhsd_rust`** (hit on air0, fixed 2026-09-17).
  `Cargo.toml`'s release profile sets `strip = "symbols"`; on macOS rustc runs Apple's
  `strip` over the linked dylib, which leaves the string table wherever the symbol tables
  end. When that offset is not a multiple of 8 the loader refuses the extension with
  `mis-aligned LINKEDIT string pool`. It depends on the build's symbol counts, so it comes
  and goes between Python versions rather than failing everywhere (air0's cp314 build lands
  on 4 mod 8). The 2026-09-15 CI deploy left `vhs-decode`, `hifi-decode` and `cvbs-decode`
  dead at import on air0; `ld-decode` kept working because it never imports
  `vhsdecode.rust_utils`. `setup.py` (`_apply_cargo_defaults`) now sets
  `CARGO_PROFILE_RELEASE_STRIP=none` on darwin only. An explicit value wins, so re-test a
  future Xcode with `CARGO_PROFILE_RELEASE_STRIP=symbols`. Check any build with
  `otool -l <so> | grep stroff` — the offset must divide by 8. Cost: ~145 KB, no speed.
  `rust_utils` also survives an unloadable extension now: it warns and takes the scipy
  path, where it used to catch only `ModuleNotFoundError` and let the `ImportError` escape.
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
- `.github/workflows/deploy-self-hosted.yml` is the only CI that runs. Five jobs:
  `preflight` (is air0 online?), then an independent verify→deploy pair per platform —
  `verify-fedora`/`deploy-fedora` (`wm`, `wf1`, `lws`) and `verify-macos`/`deploy-macos`
  (`air0`). Verify runs on same-repo PRs and dispatches; deploy only on a merge to `main`.
- **The two platforms are independent.** Each deploys on its own build + tests; neither can
  block or stall the other. A red `wm` does not stop a macOS deploy and vice versa. This is
  the point of the layout — earlier shapes (air0 as a `verify` matrix leg; one `macos` job
  that tested and deployed together) were contortions to stop a sleeping Air stalling the
  Fedora deploy, a problem that vanishes once `deploy-fedora` depends on nothing macOS.
- **`preflight` exists because an offline runner queues rather than failing** (up to 24h),
  and there is no native "skip if the runner is offline". It asks GitHub whether a runner
  labelled `air0` is online; `verify-macos` is `if:`-gated on that output. `GITHUB_TOKEN`
  cannot answer — the runners are org-level, so it needs
  `organization_self_hosted_runners: read`, held by the **gdh-ci-cd** App
  (`vars.GDH_APP_CLIENT_ID` + `secrets.GDH_APP_PRIVATE_KEY` — the action's `client-id` input
  needs the `Iv23li…` Client ID, **not** the numeric `vars.GDH_APP_ID`; they are different
  values). That org secret was `private`
  visibility (private repos only) and this fork is **public**, so it resolved to an empty
  string until it was changed to `selected` on 2026-08-31 — check that first if the token
  step ever fails. `wf1`/`lws` still queue rather than skip; that is deliberate for laptops.
- **`deploy-macos` gates on `needs.verify-macos.outputs.passed == 'true'`, not on
  `.result`** — `verify-macos` is `continue-on-error`, and how a dependent job sees a
  forgiven failure is too subtle to bet a deploy on. The output is set by a `Record success`
  step reachable only when everything before it passed. Do not give that step an `if:`, and
  do not "simplify" the gate to `result == 'success'`.
- **This fork is public** and the org runner group allows public repos, so `preflight` and
  `verify-fedora` each carry a same-repo guard (every other job descends from one of them,
  and `verify-macos` repeats it explicitly) (`head.repo.full_name == github.repository`) — without it any
  fork PR would run arbitrary code as `rdodge` on the fleet. MISRC-GUI's equivalent
  workflow has no such guard because that fork is private; do not copy between them
  without re-checking visibility. Fork-PR approval is set to `all_external_contributors`.
- The deploy step installs the **union** of each node's existing pipx extras and a per-node
  `baseline_extras` in the deploy matrices (changed 2026-08-31; it used to be "recorded extras
  win, seed only if none"). So **CI can add an extra fleet-wide and can never remove one** —
  which respects the extras-drift warning above while still letting CI push a new extra to a
  node that already has some. Dropping an extra is a manual `pipx install` on that node, and
  CI will re-add it next merge if it is still in that host's baseline. It installs from the
  runner workspace, so after the first CI deploy a node's `package_or_url` points there
  rather than at `~/Repos/vhs-decode`.
- Current baselines: `wm` = `intel,hifi_gui_qt6,cuda13,gnuradio,hifi_gnuradio,test`;
  `wf1`/`lws` = `intel,hifi_gui_qt6,cuda13`; `air0` = `hifi_gui_qt6,hifi_gnuradio`.
  **`intel` and `cuda*` cannot be installed on Apple Silicon at all** — `intel-cmplr-lib-rt`
  and `icc_rt` publish no macOS wheels and no sdist, and cupy is NVIDIA-only — which is why
  the baseline is per-node rather than one fleet-wide default.
- Offline nodes (usually `lws` and `air0`) **queue rather than fail** — up to 24h. `wf1`,
  `lws` and `air0` are `continue-on-error`, so only `wm` can turn a merge red.

- **A skipped test file cannot turn a run red.** Both `tests/unit/test_setup_*.py` files
  `importorskip("setuptools")`. venvs stopped bundling setuptools in Python 3.12, and CI's
  throwaway venv does not see a system-wide install (`brew install python-setuptools` on air0
  changed nothing), so both files skipped on every host until `setuptools` joined the `test`
  extra on 2026-09-17: CI's 210 passed / 13 skipped became 223 / 11. The only visible symptom
  was the skip count, so when it moves, find out why.

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
  `git submodule update --init --depth 1 tests/data` and the suite is green — **84 passed,
  12 skipped, 0 failed** as measured in CI on 2026-08-31, identical on `wm` (Fedora/x86-64)
  and `air0` (macOS/arm64). That count rises as tests are added, so it is a snapshot, not a
  contract. There is no baseline to compare failure sets against — gate on green.
- Parked follow-ups: launch batching / `cp.fuse` for thread scaling, cupyx `filtfilt`
  gate for the Betamax fsc notch, HiFi pipeline unported.

## Headerless capture input formats

A `.raw` capture carries no header, and the extension does not say what is inside
it. **MISRC writes `.raw` for both of its RAW modes** — signed int16 in 16-bit mode
and **signed int8** in 8-bit mode (`convert_16to8_sse(int16_t *in, int8_t *out, …)`,
`misrc_tools/common/extract.h`). Reading one as the other does not raise; it yields a
scrambled waveform and a decode that drops every field with *"Unable to determine
start of field"*, an empty `.tbc`, and **exit status 0**.

- Pass **`--input_format s8`** for an 8-bit MISRC RAW capture (`s8`/`u8`/`s16`/`u16`/`f32`).
  It overrides the extension table on both the direct and the ffmpeg-resampling path.
  Without it, `make_loader` maps `.raw` to `s16le` — correct only for the 16-bit mode.
  With `--no_resample` and no flag it is worse: the bare `LoadFFmpeg()` probes a
  headerless file as **rawvideo** and dies (`Invalid pixel format`), still exiting 0.
- Signed vs offset-unsigned does not change the decode: the RF bandpass removes DC.
  Measured on `VHS_02_1997_Wedding` — reading the same window as `int8` and as
  `uint8 + 128` differed in 0.067 % of `.tbc` samples, mean |diff| 0.0025 of 65535.
- **`np.fromstring`'s binary mode was removed in numpy 1.22**, and the `.s16` and
  `.rf` readers still called it — those formats could not be read at all on any
  current build. Fixed 2026-09-09; pinned by `tests/unit/test_input_format.py`.
- A decode that handles no fields **still exits 0**. Deliberate, but it means the
  only reliable success signal for fleet tooling is a non-empty `.tbc` whose field
  count matches the metadata — not the exit status.

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
