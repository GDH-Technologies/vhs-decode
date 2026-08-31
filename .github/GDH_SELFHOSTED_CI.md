# Self-hosted CI on the GDH fork

Fork-local notes for `GDH-Technologies/vhs-decode`. None of this exists upstream.

## What runs, and where

All CI for this fork runs on the org's own capture fleet, via
`.github/workflows/deploy-self-hosted.yml`:

Four stages, one pair per platform, plus a small availability gate:

| job | what it does | where | depends on |
|---|---|---|---|
| `preflight` | asks GitHub whether `air0` is online | `wm` | — |
| `verify-fedora` | build + unit tests | `wm` | — |
| `verify-macos` | build + unit tests | `air0` | `preflight` (skipped if offline) |
| `deploy-fedora` | pipx install | `wm`, `wf1`, `lws` | `verify-fedora` |
| `deploy-macos` | pipx install | `air0` | `verify-macos` |

**The two platforms are independent.** Each deploys on the strength of its own build and
tests; neither can block, gate or stall the other. A red `wm` does not stop a macOS deploy,
and an absent `air0` does not delay the Fedora fleet by so much as a second.

| Event | `verify-fedora` | `verify-macos` | `deploy-fedora` | `deploy-macos` |
|---|---|---|---|---|
| Pull request (same-repo) | yes | yes, if `air0` is online | no | no |
| Pull request (from a fork) | **skipped — see Security** | **skipped — see Security** | no | no |
| Push to `main` | yes | yes, if `air0` is online | **yes**, if Fedora tests passed | **yes**, if macOS tests passed |
| `workflow_dispatch` | yes | yes, if `air0` is online | no | no |

On the Fedora nodes the runner service runs as `rdodge`, the same user as the desktop
session, so the install step reaches that user's pipx venvs without sudo. On `air0` the
runner is a **launchd agent** (`~/Library/LaunchAgents/actions.runner.GDH-Technologies.air-0.plist`)
running as **`dodge`** — a different username, same principle.

There is deliberately no `push` trigger on `'**'`. A feature branch with an open PR is
already covered by the `pull_request` event; triggering on both doubles every run, which is
the noise this workflow was created to stop. A branch with no PR gets no CI — open a draft
PR if you want it checked.

## Every inherited workflow is DISABLED — as a repo setting, not a file edit

All 14 workflow files inherited from `oyvindln/vhs-decode` are byte-identical to upstream
and must stay that way. They are turned off on this fork with `gh workflow disable`:

```bash
for w in "Build AppImage" "Build and Test" "Build Linux decode" "Build macOS decode" \
         "Build Windows decode" "Deploy Docs to GitHub Pages" "Functional Tests" \
         "Prepare Release Draft" "Push to PyPI" "Push to TestPyPI" "Release" \
         "Build Windows Package"; do
  gh workflow disable "$w" --repo GDH-Technologies/vhs-decode
done
# The two workflows both named "Tests" collide by name; disable them by ID:
gh workflow disable 308473144 --repo GDH-Technologies/vhs-decode  # vhs-decode-integration-tests.yml
gh workflow disable 308473147 --repo GDH-Technologies/vhs-decode  # vhs-decode-unit-tests.yml
```

Check the current state with `gh workflow list --repo GDH-Technologies/vhs-decode --all`,
and re-enable any one with `gh workflow enable`.

**This is invisible in the working tree** — hence this file. If you ever wonder why pushing
a `v*` tag produces no release build, or why a PR shows no ubuntu/windows/macOS jobs,
that's why.

It is a setting rather than an `if:` guard or a deleted file because upstream churns
`.github/workflows` constantly and we merge from `oyvindln/vhs_decode` regularly. Leaving
those files untouched keeps every upstream merge conflict-free.

### What was actually running before

Worth recording, because the original motivation was a cost that turned out not to exist.
The fork is **public**, so GitHub-hosted minutes are free and unmetered. Across 46 runs,
33 were 1-second `startup_failure`s and every run that reached a conclusion reported
`total_ms: 0` billable. Nothing was being spent.

What *was* real: `build-and-test.yml` triggers on `push` to `'**'` and `pull_request` to
`'**'`, so every commit on every branch produced a red X. It has been failing at startup
since it gained `uses: ./.github/workflows/...` reusable-workflow calls.

### Consequence: reduced cross-platform coverage here

Windows and the AppImage are no longer built on this fork at all, and neither PyPI workflow
can fire. A fork-local change can break them without CI noticing. Before sending work
upstream, re-enable the relevant workflow and run it once via `workflow_dispatch`.

macOS is the exception: the `verify-macos` job builds and unit-tests on `air0` (Apple Silicon) for
every same-repo PR, so a Cython or `setuptools-rust` break that only shows on macOS is
caught here. It is **non-gating** and it is not a substitute for upstream's `Build macOS
decode` workflow — it proves the source builds and the unit tests pass on macOS, not that
the `.dmg` packaging still works.

### Two workflows are not files

`gh workflow list` also shows **Copilot code review** and **Dependency Graph**. Those are
GitHub-managed, not workflow files, so `gh workflow disable` does not apply — they are
repo/org settings. They were left on deliberately.

## Security: this fork is PUBLIC

This is the one place where the MISRC-GUI setup must **not** be copied verbatim. That fork
is private, and its `selfhosted-deploy.yml` accepts `pull_request` unguarded. This fork is
public, and the org's `Default` runner group sets `allows_public_repositories: true`, so an
unguarded `pull_request` trigger would let anyone's PR execute arbitrary code as `rdodge`
on `wm`, `wf1` and `lws` — machines that hold client capture data.

Two mitigations, both in place:

1. The `preflight` and `verify-fedora` jobs each carry a same-repo guard (every other job
   descends from one of them via `needs`, and `verify-macos` repeats it explicitly):

   ```yaml
   if: >-
     github.event_name != 'pull_request' ||
     github.event.pull_request.head.repo.full_name == github.repository
   ```

   A fork PR shows as skipped and never reaches a runner. Do not remove this.

2. The repo's fork-PR approval policy was tightened from `first_time_contributors` (the
   default, which lets a *returning* contributor's PR run without approval) to
   `all_external_contributors`:

   ```bash
   gh api -X PUT repos/GDH-Technologies/vhs-decode/actions/permissions/fork-pr-contributor-approval \
     -f approval_policy=all_external_contributors
   ```

Revisit both before accepting outside contributions.

## The unit-test fixtures are a submodule

`tests/unit` reads `tests/data` through the `data_dir` fixture in `tests/conftest.py`, not
via a literal path — so grepping the tests for `tests/data` finds nothing and the
dependency looks absent. It is not. Without the submodule, six tests in `test_sync.py` and
`test_rust_math.py` fail with `FileNotFoundError` on `PAL_GOOD.txt.gz` /
`hilbert_data.npz`.

This is the "pre-existing pytest baseline of 4 failures + 3 errors" that `CLAUDE.md`
records. It is not a baseline to be compared against — it is an uninitialized submodule.
With `tests/data` checked out, `tests/unit` is green. Counts observed in CI on 2026-08-31:
**84 passed, 12 skipped, 0 failed** — identical on `wm` (Fedora/x86-64) and `air0`
(macOS/arm64). The count grows as tests are added, so treat it as a snapshot and gate on
green rather than on the number.

The workflow initialises it explicitly rather than using `submodules: recursive`, which
would also clone the `testdata` laserdisc submodule that nothing here reads:

```bash
git submodule update --init --depth 1 tests/data   # ~300 MB
```

Integration tests are deliberately not run: they decode real RF samples and take up to an
hour.

## The install step

Each fleet node runs `pipx install --force "$GITHUB_WORKSPACE[<extras>]"` (bare
`"$GITHUB_WORKSPACE"`, with no bracket suffix, if the node resolves to no extras at all —
`pipx install "/path[]"` is not a valid requirement).

- **From the runner workspace, not `~/Repos/vhs-decode`.** A regular (non-editable) install
  copies the source into the venv, so nothing depends on that path surviving, and CI must
  never hard-reset a repo directory that gets edited by hand. Note the side effect: after
  the first CI deploy, a node's `pipx list` `package_or_url` points at the runner workspace
  rather than `~/Repos/vhs-decode`.
- **Regular, never editable.** `pipx install -e` does not build `vhsd_rust` at all, which
  silently drops every decode on that node to the scipy `sosfiltfilt` fallback for both
  video and hifi. See `CLAUDE.md`.
- **Extras are the union of the node's own and a per-node baseline.** The step parses the
  existing `package_or_url` (e.g. `…/vhs-decode[intel,hifi_gui_qt6,cuda13]`) and unions
  those extras with the `baseline_extras` recorded for that host in the deploy matrix.

### Why union, and not either side alone

This changed on 2026-08-31; it used to be "the node's recorded extras win, seed with
`DEFAULT_EXTRAS` only if there are none". Both one-sided rules are wrong:

- **Not "baseline wins".** `CLAUDE.md` records that extras drift between hosts and that its
  own notes should not be trusted over live metadata. Imposing one spec would silently strip
  an extra somebody added by hand on a node.
- **Not "recorded wins"** (the old behaviour). A node that already has *any* extras recorded
  could then never be handed a new one by CI. That is exactly what blocked adding
  `gnuradio`/`hifi_gnuradio`/`test` to `wm`, which already had `intel,hifi_gui_qt6,cuda13`
  recorded — changing the seed did nothing, because the seed only applied to nodes with no
  extras at all.

So the rule is: **CI can add an extra to the fleet and can never remove one.** Dropping an
extra is a deliberate manual `pipx install` on that node; CI will not do it for you, and
will re-add it on the next merge if it is still in that host's `baseline_extras`.

The current baselines:

| host | `baseline_extras` | declared in |
|---|---|---|
| `wm` | `intel,hifi_gui_qt6,cuda13,gnuradio,hifi_gnuradio,test` | `deploy-fedora` matrix |
| `wf1`, `lws` | `intel,hifi_gui_qt6,cuda13` | `deploy-fedora` matrix |
| `air0` | `hifi_gui_qt6,hifi_gnuradio` | `deploy-macos` matrix |

The union is computed with a plain `tr`/`sed`/`awk` pipeline that dedupes while preserving
order. The rejoin is `tr '\n' ','` + `sed 's/,$//'` rather than `paste -s`, deliberately, so
nothing depends on how a given `paste(1)` treats the `-` stdin operand across BSD and GNU
userlands.

Post-install it verifies, from `/tmp` (inside the workspace, cwd is on `sys.path` and the
source tree masks whether the build produced anything), that the Cython extensions import
and that `vhsdecode.rust_utils._HAS_VHSD_RUST` is true.

`~/.cargo/bin` and `~/.local/bin` are prepended to `PATH`: the runner is a systemd service
with a minimal environment, and the `vhsd_rust` ext-module in `pyproject.toml` is not marked
`optional`, so a missing `cargo` hard-fails the wheel build.

### `UV_VENV_CLEAR=1` is required — do not remove it

The first fleet deploy succeeded on `wm` and failed on `wf1` and `lws` with:

```
error: Failed to create virtual environment
  Caused by: A virtual environment already exists at: .
⚠️  Not removing existing venv …/pipx/venvs/vhs-decode because it was not created
    in this session
```

**`pipx install --force` does not delete an existing venv.** On an existing install it
prints `Installing to existing venv`, prepends `--force-reinstall` to the pip args
(`pipx/commands/install.py:100`), and then calls `create_venv()` on the directory that is
already there:

- **pip backend** — `python -m venv <dir>` reuses a populated directory. Harmless.
- **uv backend** — `UvBackend.create_venv` runs `uv venv <root>` with **no `--clear`**, and
  uv refuses to write into a directory that already holds a venv. The install raises, and
  the `except` branch's `venv.remove_venv()` declines to clean up because `safe_to_remove()`
  is false for a pre-existing venv — hence that misleading second line. (uv reports the path
  as `.` because pipx runs it with `run_dir=root`.)

Which backend runs is **not** a per-run choice. `resolve_backend_name()`'s precedence is
`cli > metadata > env > auto`, and `metadata` is the backend recorded in the venv already on
disk — deliberately, so `PIPX_DEFAULT_BACKEND` cannot silently retarget existing venvs. All
three nodes have identical pipx 1.15.0 / uv 0.12.2 / Python 3.14.7; they differed only in
how their venv had originally been created:

| node | `pyvenv.cfg` says | backend | result |
|---|---|---|---|
| `wm` | `command = /usr/bin/python3.14 -m venv …` | pip | worked |
| `wf1`, `lws` | `uv = 0.12.2` | uv | failed |
| `air0` | `uv = 0.12.2` | uv | would have failed the same way |

`air0` joined the fleet deploy later (2026-08-31) and is **uv-backed too**, so `deploy-macos`
sets `UV_VENV_CLEAR=1` for exactly the same reason — verified by running the real
`UV_VENV_CLEAR=1 pipx install --force "$HOME/Repos/vhs-decode[hifi_gui_qt6,hifi_gnuradio]"`
on it by hand first, which succeeded (`vhs-decode 0.4.1.dev88`). Its pipx is Homebrew's
1.16.6 on Python 3.14.6, not the Fedora nodes' 1.15.0/3.14.7, so the versions in the table
above are not fleet-wide.

Measured against every venv state (scratch `PIPX_HOME`, `pycowsay`):

| candidate | none | pip-backed | uv-backed | keeps backend |
|---|---|---|---|---|
| `--force` (original) | ok | ok | **fail** | — |
| `--force --backend pip` | ok | ok | **fail** | no |
| `pipx uninstall` then install | ok | ok | ok | **no** — flips pip→uv |
| `--force` + `UV_VENV_CLEAR=1` | ok | ok | ok | **yes** |

`--backend pip` does not help: pipx will not retarget an existing venv's backend.
`UV_VENV_CLEAR` is uv's own documented remedy — it is the hint uv prints on this exact
error — and it passes through because pipx only overrides `VIRTUAL_ENV` and
`UV_NO_PROGRESS`. It is inert on pip-backed nodes and leaves each node's recorded backend
alone, which uninstall-then-install would not.

Note the failure was safe: it happened at venv creation, before anything was written, so
`wf1` and `lws` kept their previous working installs.

## The fork needs tags, or every CI build is misversioned

`fetch-depth: 0` fetches tags **from the fork**, and until 2026-08-31 the GDH fork had
**zero** — every fleet host had them only because they had been fetched by hand locally.
So the first CI deploy stamped `wm` with `0.1.dev5678` (setuptools_scm's no-tag fallback)
instead of `0.4.1.dev88`.

Fixed by pushing upstream's 53 tags to the fork:

```bash
git fetch <oyvindln-url> --tags
git push <gdh-fork-url> --tags
```

If a fresh clone of the fork ever stamps `0.1.devN` again, the tags are gone — re-push
them. Identify both remotes by URL, never by name: remote names differ per machine.

## `air0` — the macOS node

Added 2026-08-31. `air0` has its own verify→deploy pair (`verify-macos`, `deploy-macos`),
independent of the Fedora pair, plus a `preflight` job that skips both when the Air is
offline.

Runner labels are `self-hosted, macOS, ARM64, air0` (runner name `air-0`, in the `Default`
pool). What differs from a Fedora node:

- **Different user.** The runner is a launchd agent running as `dodge`, not `rdodge`.
- **Homebrew, not rustup.** `cargo`, `pipx` and `python3` are all `/opt/homebrew/bin/…`;
  there is no `~/.cargo/bin` on that host. The launchd agent's captured
  `~/actions-runner/.path` **already contains** `/opt/homebrew/bin`, so this works today —
  the PATH step adds `/opt/homebrew/{bin,sbin}` behind a `-d` guard only as insurance
  against a runner re-registration picking up a leaner environment. (The `CLAUDE.md` note
  that Homebrew pipx "is not on the non-interactive PATH" is about plain `ssh air0 <cmd>`,
  which is a non-login shell — it does not describe the runner.)
- **`PIPX_LOCAL_VENVS` contains a space**: `~/Library/Application Support/pipx/venvs`. Every
  expansion of it in the workflow has to stay quoted.
- **`vhsd_rust` builds fine** on Apple Silicon —
  `vhsd_rust.cpython-314-darwin.so` lands in the venv and `_HAS_VHSD_RUST` is true, so the
  existing post-install check needed no macOS special-casing.

### Half the Linux extras cannot exist on Apple Silicon

This is why a per-node baseline was needed rather than one fleet-wide `DEFAULT_EXTRAS`: the
old global default would have handed `air0` the Linux spec and hard-failed the deploy.

| extra | packages | on arm64 macOS? |
|---|---|---|
| `intel` | `intel-cmplr-lib-rt`, `icc_rt` | **no** — no macOS wheels at all, and no sdist to fall back on |
| `cuda13`/`cuda12`/`cuda11` | `cupy-cuda1Nx` + nvidia rt libs | **no** — NVIDIA-only |
| `hifi_gui_qt6` | `PyQt6` | yes — `macosx_10_14_universal2` |
| `hifi_gui_qt5` | `PyQt5` | yes — `macosx_11_0_arm64` |
| `gnuradio`/`hifi_gnuradio` | `pyzmq` | yes — `macosx_11_0_arm64` |
| `test` | `pytest` | yes (pure Python) |

### `/Applications/decode.app` is not managed by CI

`air0` also has the prebuilt `.dmg` from an upstream release installed (installed 2026-07-09;
its `CFBundleShortVersionString` is `0.0.0`, so it does not identify its own build). It is a
single self-contained `decode` binary and it exports **nothing** onto `PATH` — no symlink in
`/usr/local/bin` or `~/.local/bin` points at it, and `vhs-decode` resolves to the pipx shim
in `~/.local/bin`. So it does not shadow a CI deploy.

The corollary: **CI cannot update it either.** After a deploy, `air0`'s CLIs are current with
`main` while `decode.app` stays frozen at whatever upstream release was last installed by
hand. If someone reports air0 behaving like an old build, check which of the two they ran.
Should a future `.dmg` start installing CLI symlinks, it could begin shadowing the pipx
shims — that would be silent, so re-check `command -v vhs-decode` after any `.dmg` install.

## Offline nodes: `lws` and `wf1` queue, `air0` is skipped

A job targeting an offline self-hosted runner does **not** fail — it queues, for up to 24
hours, until that runner appears. The fleet uses both behaviours, on purpose:

- **`wf1` and `lws` queue.** They are legs of the `deploy-fedora` matrix, and a queued leg
  means "this node self-updates the next time it is online" — which for a laptop is exactly
  what we want. The cost is the run showing in-progress until then. Both are
  `continue-on-error: true`; only `wm` can turn a merge red.
- **`air0` is skipped instead**, by the `preflight` job. It is on its own stage, so it can
  be skipped cleanly without leaving a run half-open for a day.

### The `preflight` job, and why it needs a GitHub App

There is no native way to skip a job whose runner is offline — the scheduler queues it. So
`preflight` asks GitHub directly, and `verify-macos` carries
`if: needs.preflight.outputs.air0_online == 'true'`.

`GITHUB_TOKEN` **cannot** answer that question. These runners are registered at the **org**
level (the `Default` group); the repo-level runners endpoint lists only runners registered to
the repository and returns an empty list here. Reading org runners needs
`organization_self_hosted_runners: read`.

The **`gdh-ci-cd`** GitHub App holds exactly that permission. Both halves are org-level:
`vars.GDH_APP_ID` (= 4105774) and `secrets.GDH_APP_PRIVATE_KEY`.

> **Gotcha that cost a debugging round:** `GDH_APP_PRIVATE_KEY`'s org visibility was
> `private`, meaning *private repos only* — and **this fork is public**, so
> `secrets.GDH_APP_PRIVATE_KEY` silently resolved to an empty string here. It was changed to
> `selected` with `vhs-decode` included on 2026-08-31. If the token step ever fails on an
> empty private key, check that visibility before anything else.

The probe matches on the **`air0` label**, not the runner's name (`air-0`), because the label
is what `runs-on` selects on — renaming the runner would otherwise break scheduling while
leaving this check green. Any failure to get an answer (bad token, API outage) is treated as
*offline* and emits a `::warning::`: skipping macOS is the safe failure mode, and the warning
is there because a silently-expired credential otherwise looks identical to a sleeping Mac.

### Why `deploy-macos` gates on an output, not on `needs.verify-macos.result`

`verify-macos` is `continue-on-error: true` (a macOS failure must not turn a PR red). How a
dependent job sees a *forgiven* failure through `needs.<job>.result` is subtle enough that
betting a deploy on it is a bad trade. So `verify-macos` publishes an explicit output:

```yaml
outputs:
  passed: ${{ steps.done.outputs.passed }}
```

set by a `Record success` step placed after the tests, which is only reachable when every
prior step succeeded. `deploy-macos` requires `needs.verify-macos.outputs.passed == 'true'`.
That is `'true'` or it is absent — there is no third reading, and it behaves correctly
whichever way `result` reports. **Do not give that step an `if:`**, and do not "simplify" the
gate to `result == 'success'`.

### History

This layout replaced two earlier shapes, both wrong: `air0` as a leg of the `verify` matrix,
and a single `macos` job that tested and deployed in one. Both were contortions to avoid a
sleeping Air stalling the Fedora deploy — a problem that simply does not exist once each
platform has its own verify→deploy pair, since `deploy-fedora` then depends on nothing
macOS-related. The git history has them.

## Reproducing a CI run by hand

```bash
git submodule update --init --depth 1 tests/data
python3 -m venv /tmp/vhsd-venv
/tmp/vhsd-venv/bin/python -m pip install ".[test]"
cd /tmp && /tmp/vhsd-venv/bin/python -c "import vhsdecode.rust_utils as r; print(r._HAS_VHSD_RUST)"
cd /tmp && GITHUB_WORKSPACE=~/Repos/vhs-decode ~/Repos/vhs-decode/tests/run_unit.sh
```

Run the import checks from `/tmp`, never from inside the repo — cwd is on `sys.path` there
and a stale `vhsd_rust*.so` at the repo root makes the check always pass.
