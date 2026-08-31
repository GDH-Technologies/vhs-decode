# Self-hosted CI on the GDH fork

Fork-local notes for `GDH-Technologies/vhs-decode`. None of this exists upstream.

## What runs, and where

All CI for this fork runs on the org's own capture fleet, via
`.github/workflows/deploy-self-hosted.yml`:

| Event | Build + unit tests (`wm`) | pipx install (`wm`, `wf1`, `lws`) |
|---|---|---|
| Pull request (same-repo) | yes | no |
| Pull request (from a fork) | **skipped — see Security** | no |
| Push to `main` | yes | **yes** |
| `workflow_dispatch` | yes | no |

The runner service runs as `rdodge`, the same user as the desktop session, so the install
step reaches that user's pipx venvs without sudo.

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

### Consequence: no cross-platform coverage here

Windows, macOS and the AppImage are no longer built on this fork at all, and neither PyPI
workflow can fire. A fork-local change can break them without CI noticing. Before sending
work upstream, re-enable the relevant workflow and run it once via `workflow_dispatch`.

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

1. The `verify` job carries a same-repo guard:

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
With `tests/data` checked out, `tests/unit` is **66 passed, 12 skipped, 0 failed**.

The workflow initialises it explicitly rather than using `submodules: recursive`, which
would also clone the `testdata` laserdisc submodule that nothing here reads:

```bash
git submodule update --init --depth 1 tests/data   # ~300 MB
```

Integration tests are deliberately not run: they decode real RF samples and take up to an
hour.

## The install step

Each Fedora node runs `pipx install --force "$GITHUB_WORKSPACE[<extras>]"`.

- **From the runner workspace, not `~/Repos/vhs-decode`.** A regular (non-editable) install
  copies the source into the venv, so nothing depends on that path surviving, and CI must
  never hard-reset a repo directory that gets edited by hand. Note the side effect: after
  the first CI deploy, a node's `pipx list` `package_or_url` points at the runner workspace
  rather than `~/Repos/vhs-decode`.
- **Regular, never editable.** `pipx install -e` does not build `vhsd_rust` at all, which
  silently drops every decode on that node to the scipy `sosfiltfilt` fallback for both
  video and hifi. See `CLAUDE.md`.
- **Extras are read from the node, not imposed.** The step parses the existing
  `package_or_url` (e.g. `…/vhs-decode[intel,hifi_gui_qt6,cuda13]`) and reuses those
  extras, falling back to `DEFAULT_EXTRAS` only on a node with no vhs-decode yet. `CLAUDE.md`
  records that extras drift between hosts and that its own notes should not be trusted over
  live metadata — so the workflow reads the live metadata.

Post-install it verifies, from `/tmp` (inside the workspace, cwd is on `sys.path` and the
source tree masks whether the build produced anything), that the Cython extensions import
and that `vhsdecode.rust_utils._HAS_VHSD_RUST` is true.

`~/.cargo/bin` and `~/.local/bin` are prepended to `PATH`: the runner is a systemd service
with a minimal environment, and the `vhsd_rust` ext-module in `pyproject.toml` is not marked
`optional`, so a missing `cargo` hard-fails the wheel build.

## Offline nodes queue, they do not fail

`lws` is a laptop and is frequently offline; `wf1` is usually up. A job targeting an offline
self-hosted runner does **not** fail — it queues, for up to 24 hours, until that runner
appears.

That is deliberate: a queued leg means "this node self-updates the next time it is online".
The cost is the run showing in-progress until then. `wm` gates the run; `wf1` and `lws` are
`continue-on-error: true`, so neither can turn a merge red.

If you want the matrix to skip offline hosts instead, it needs a preflight job that queries
`orgs/GDH-Technologies/actions/runners` — which requires a PAT with `admin:org` read, since
`GITHUB_TOKEN` cannot read org-level runners.

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
