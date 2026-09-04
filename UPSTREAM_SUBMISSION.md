# Upstream submission package (C5) — prepared 2026-08-31

Two branches on the GDH fork, cherry-picked onto oyvindln/vhs-decode head
`b6cafaea` (both conflicts resolved: upstream's `init_logging` has no `debug`
param, so the resume commit adds only `append=`). Full `tests/unit` suite on
the upstream base: **57 passed, 0 failed** (with tests/data initialized).

- `upstream-tbc-db-correctness` -> commit `041f7535` (one commit)
- `upstream-decode-resume`      -> commit `4f3f4fb0` (stacked on the above)

## Submit (you, not Claude — never push to oyvindln remotes)

1. PR 1: https://github.com/oyvindln/vhs-decode/compare/vhs_decode...GDH-Technologies:vhs-decode:upstream-tbc-db-correctness
2. PR 2: https://github.com/oyvindln/vhs-decode/compare/vhs_decode...GDH-Technologies:vhs-decode:upstream-decode-resume
   (shows 2 commits until PR 1 merges; say so in the body — text below does)

Draft bodies below. The scratch worktree (`.claude/worktrees/upstream-prep`,
with copied .so files + tests/data initialized) is kept for review-feedback
iteration; remove it with `git worktree remove --force` when both PRs land.

---

## PR 1 body: fix(metadata): correct .tbc.db capture-row identity and overwrite preflight

Three small correctness fixes for the SQLite (`--write_db`) metadata path,
found while building tooling that reads the db downstream (decode-orc treats
`capture.decoder` as a pipeline selector):

- **`capture.decoder` was always `'ld-decode'`**: nothing ever set the
  `decoder` key the db writer reads, so vhs-decode and cvbs-decode runs were
  recorded as ld-decode. Each main now declares its decoder name
  (`vhs-decode` for the VHS/CVBS paths), factored into a small
  `lddecode/tbc_db.py` helper.
- **PAL-M wrote `"PAL-M"` into a column whose CHECK allows `'PAL_M'`**, so
  `--write_db` PAL-M decodes died on the constraint. The system value is now
  mapped at insert time (JSON output unchanged).
- **The `--overwrite` preflight didn't know the db/orc outputs**: it checked
  only `.tbc/_chroma.tbc/.log/.tbc.json`, so stale `.tbc.db`, `.tbcy`,
  `.tbcc` files from a previous run survived into a "fresh" decode.

Unit tests included (`tests/unit/test_tbc_db_metadata.py`); the full
`tests/unit` suite passes on this branch.

---

## PR 2 body: feat(decode): --resume continues an interrupted decode bit-perfectly

Adds `--resume` to vhs-decode: re-running an interrupted `--write_db` decode
with `--resume` continues it in place — same `.tbc`/chroma outputs, same db —
instead of starting over. Motivation: long decodes (multi-hour SP tapes) that
must survive a host reboot or an operator interruption without losing hours
of work.

**How it works** (design notes, all in the diff):

- Preflight requires the `.tbc.db` and the video output(s), and validates the
  `capture` row (system, field dims, sample rate) against the current
  invocation, refusing on mismatch with remedy text. A db mid-write by
  another process is refused ("database is locked") rather than corrupted.
- Reconcile on start: `N = min(db field rows, video bytes // field size)`;
  a trailing first field is dropped so the resume point lands on a complete
  frame; `field_record` rows `>= N` are deleted (cascades take the child
  tables), outputs are truncated to `N x field_bytes` and reopened for
  append. This also repairs a hard-kill boundary, where the db can be one
  field ahead of the picture data (commits land before the picture write).
- Warm-up: the loader seeks to the `file_loc` of field `N-10` and decodes
  forward, suppressing writes until the output reaches field `N` — sync/AGC
  re-lock happens on the warm-up fields, so there is no seam.
- The chroma phase rotation is re-anchored by reconstructing the decoder's
  internal `field_number` (mod 4) from the stored `fieldPhaseID`s of the last
  two kept fields — the counter can drift from the field index when a
  "readloc didn't advance" event occurred, so the index alone is not enough.
- SIGTERM is handled like SIGINT in all three mains (flush and exit
  cleanly), so a service manager's stop produces a resumable state.
  The logfile is opened in append mode on resume.
- `--resume` is mutually exclusive with `--overwrite`/`--start_fileloc`/`-s`.

**Verification**: interrupted decodes of a real NTSC VHS RF capture with
SIGTERM (flushed) and SIGKILL (no flush, boundary repaired by the reconcile)
both resume to outputs **byte-identical** to an uninterrupted control run —
luma, chroma, and metadata — verified with cmp and db dumps. Unit tests for
the reconcile/seed logic in `tests/unit/test_tbc_db_resume.py`; the full
`tests/unit` suite passes.

Stacked on the metadata-correctness PR (its commit appears here until that
merges); hifi-decode is unchanged.
