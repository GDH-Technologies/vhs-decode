#!/usr/bin/env python3
# gdh_version.py
#
# Resolve, propose and cut the GDH fork version for vhs-decode.
#
# This fork carries GDH-only work on top of oyvindln/vhs-decode and needs to say
# so without colonising upstream's version namespace. The scheme is:
#
#     tag        v<UPSTREAM>-gdh-<MAJOR>.<MINOR>        e.g. v0.4.0-gdh-1.0
#     canonical  <UPSTREAM>-gdh-<MAJOR>.<MINOR>[+<N>.g<sha>][.dirty]
#     PEP 440    <UPSTREAM>+gdh.<MAJOR>.<MINOR>[.<N>.g<sha>][.dirty]
#
# <UPSTREAM> is upstream's own current release -- the nearest upstream release
# tag, found with `git describe`.
#
#   *** Never version-sort the tag list to find it. ***
#
# v0.5, v0.6 and v0.7 are ancient ld-decode tags (2014-2017) that are reachable
# from HEAD and sort ABOVE v0.4.0, so `git tag --sort=-v:refname --merged` yields
# 0.7. `git describe` picks the nearest by distance and yields 0.4.0, which is
# the right answer.
#
# The package version is this version: setuptools_scm's public part is the
# upstream tag (version_scheme = "only-version") and gdh_local_scheme() below
# supplies the local segment. `-gdh-` becomes `+gdh.` because PEP 440 has no
# room for the former; the ordering is identical either way.
#
# Usage:
#   scripts/gdh_version.py show [--json|--pep440]
#   scripts/gdh_version.py propose [--level auto|major|minor]
#   scripts/gdh_version.py bump --level auto --push
#
# Exit codes: 0 on success; 2 when `propose --level auto` finds nothing that
# warrants a release; other non-zero on error.

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `git describe --long` output: <tag>-<distance>-g<node>
DESCRIBE_RE = re.compile(r"^(?P<tag>.+)-(?P<distance>\d+)-g(?P<node>[0-9a-f]+)$")
GDH_TAG_RE = re.compile(r"^v(?P<upstream>\d+(?:\.\d+)*)-gdh-(?P<major>\d+)\.(?P<minor>\d+)$")

BREAKING_SUBJECT_RE = re.compile(r"^[a-zA-Z]+(?:\([^)]*\))?!:")
FEAT_SUBJECT_RE = re.compile(r"^feat(?:\([^)]*\))?:")
BREAKING_BODY_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)

# git grows its default abbreviation with repo size; pin it so the node length
# does not drift between machines.
ABBREV = "8"

# Upstream release tags only: gdh tags and the dated nightlies (nightly.yml is
# live on this fork and has already cut several) must never become the base.
UPSTREAM_MATCH = ["--match", "v[0-9]*", "--match", "[0-9]*"]
UPSTREAM_EXCLUDE = ["--exclude", "nightly-*", "--exclude", "*-gdh-*"]


class VersionError(RuntimeError):
    """A resolution or bump failure with a message fit for stderr."""


def git(*args: str, root: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root or REPO_ROOT), *args],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise VersionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip() if result.returncode == 0 else ""


def describe_upstream(root: Path) -> tuple[str, int, str] | None:
    """(upstream version, distance, node) from the nearest upstream release tag."""
    # No commit-ish and no --dirty here: git refuses to combine the two, and
    # dirtiness is read separately.
    out = git(
        "describe", "--tags", "--long", "--abbrev=" + ABBREV,
        *UPSTREAM_MATCH, *UPSTREAM_EXCLUDE,
        root=root, check=False,
    )
    match = DESCRIBE_RE.match(out) if out else None
    if not match:
        return None
    return (
        match.group("tag").lstrip("v"),
        int(match.group("distance")),
        "g" + match.group("node"),
    )


def describe_gdh(root: Path) -> tuple[str, int, int, int, str] | None:
    """(upstream, major, minor, distance, node) from the nearest gdh tag."""
    out = git(
        "describe", "--tags", "--long", "--abbrev=" + ABBREV, "--match", "v*-gdh-*",
        root=root, check=False,
    )
    match = DESCRIBE_RE.match(out) if out else None
    if not match:
        return None
    tag = GDH_TAG_RE.match(match.group("tag"))
    if not tag:
        return None
    return (
        tag.group("upstream"),
        int(tag.group("major")),
        int(tag.group("minor")),
        int(match.group("distance")),
        "g" + match.group("node"),
    )


def is_dirty(root: Path) -> bool:
    return bool(git("status", "--porcelain", root=root, check=False))


def resolve(root: Path | None = None, dirty: bool | None = None) -> dict:
    """Resolve the current version. See the module docstring for the scheme."""
    root = root or REPO_ROOT
    upstream_info = describe_upstream(root)
    if upstream_info is None:
        raise VersionError(
            "cannot find an upstream release tag; is this a shallow clone? "
            "fetch tags with `git fetch --tags`"
        )
    upstream, upstream_distance, upstream_node = upstream_info
    if dirty is None:
        dirty = is_dirty(root)

    gdh = describe_gdh(root)
    upstream_moved = False
    if gdh is None:
        major, minor = 0, 0
        distance, node = upstream_distance, upstream_node
    elif gdh[0] != upstream:
        # Upstream moved since the last gdh tag: the counters reset, so no gdh
        # release exists on this base yet.
        upstream_moved = True
        major, minor = 0, 0
        distance, node = upstream_distance, upstream_node
    else:
        _, major, minor, distance, node = gdh

    # gdh-0.0 is a sentinel, never a release, so it always carries its distance.
    exact = distance == 0 and not dirty and (major, minor) != (0, 0)

    return {
        "upstream": upstream,
        "major": major,
        "minor": minor,
        "distance": distance,
        "node": node,
        "dirty": dirty,
        "exact": exact,
        "upstream_moved": upstream_moved,
        "has_gdh_tag": gdh is not None,
    }


def _local_parts(state: dict) -> list[str]:
    """The local segment's parts: gdh, <major>.<minor>, [<n>, g<sha>, [dirty]]"""
    parts = ["gdh", f"{state['major']}.{state['minor']}"]
    if not state["exact"]:
        parts.extend([str(state["distance"]), state["node"]])
        if state["dirty"]:
            parts.append("dirty")
    return parts


def local_segment(state: dict) -> str:
    """The PEP 440 local segment: +gdh.<major>.<minor>[.<n>.g<sha>][.dirty]"""
    return "+" + ".".join(_local_parts(state))


def scm_local_segment(state: dict) -> str:
    """The same segment, with the counters joined by `-` instead of `.`.

    setuptools_scm assembles the final version through
    vcs_versioning._version_schemes._common.combine_version_with_local_parts,
    which splits the local part on "." and drops any segment that already
    appeared ANYWHERE in the list. Dot-separated counters get eaten by that:

        gdh.1.1.5.g143a89f8   ->  gdh.1.5.g143a89f8    (minor lost)
        gdh.1.0.1.g143a89f8   ->  gdh.1.0.g143a89f8    (distance lost)
        gdh.0.0.129.g143a89f8 ->  gdh.0.129.g143a89f8  (minor lost)

    Joining major and minor with "-" keeps them a single segment through that
    dedup, and PEP 440 normalisation then rewrites "-" back to "." -- so the
    published version reads exactly as local_segment() describes it.
    """
    parts = _local_parts(state)
    parts[1] = parts[1].replace(".", "-")
    return "+" + ".".join(parts)


def pep440(state: dict) -> str:
    return f"{state['upstream']}{local_segment(state)}"


def canonical(state: dict) -> str:
    """The form the tag carries: <upstream>-gdh-<major>.<minor>[+<n>.g<sha>][.dirty]"""
    version = f"{state['upstream']}-gdh-{state['major']}.{state['minor']}"
    if not state["exact"]:
        version += f"+{state['distance']}.{state['node']}"
        if state["dirty"]:
            version += ".dirty"
    return version


def to_canonical(version: str) -> str:
    """Rewrite a PEP 440 gdh version into the canonical tag form.

    `0.4.0+gdh.1.0.13.g143a89f8` -> `0.4.0-gdh-1.0+13.g143a89f8`. Anything that
    is not a gdh version is returned unchanged, so this is safe on any input.

    Accepts `-` as well as `.` between the counters, so it also handles the
    pre-normalisation form scm_local_segment() emits.
    """
    match = re.match(
        r"^(?P<upstream>[^+]+)\+gdh[.-](?P<major>\d+)[.-](?P<minor>\d+)(?P<rest>.*)$",
        version,
    )
    if not match:
        return version
    out = f"{match.group('upstream')}-gdh-{match.group('major')}.{match.group('minor')}"
    rest = match.group("rest").lstrip(".")
    return f"{out}+{rest}" if rest else out


# --- setuptools_scm integration -------------------------------------------------
#
# setup.py loads this module by path and passes gdh_local_scheme as the
# local_scheme callable. The public part comes from version_scheme
# "only-version" in pyproject.toml, i.e. the upstream release tag verbatim.


def _local_from_pkg_info(root: Path) -> str | None:
    """Recover the local segment from PKG-INFO when there is no git.

    Building a wheel from an sdist has no .git, but setuptools_scm still calls
    local_scheme (the metadata workdir is not "preformatted"). Without this, the
    gdh identity would silently degrade to gdh.0.0 on that path.
    """
    for candidate in [root / "PKG-INFO", *sorted(root.glob("*.egg-info/PKG-INFO"))]:
        try:
            for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    break
                if line.startswith("Version:"):
                    _, _, local = line.partition(":")[2].strip().partition("+")
                    return "+" + local if local.startswith("gdh.") else None
        except OSError:
            continue
    return None


def gdh_local_scheme(version) -> str:
    """setuptools_scm local_scheme: build the +gdh.<major>.<minor>... segment.

    `version.tag` has already been reduced to the upstream release and the raw
    tag string is not retained on ScmVersion, so the gdh coordinates have to be
    re-derived from git.
    """
    root = Path(getattr(version.config, "absolute_root", REPO_ROOT))
    try:
        state = resolve(root=root, dirty=bool(version.dirty))
    except VersionError:
        recovered = _local_from_pkg_info(root)
        if recovered:
            return recovered
        # No git and no PKG-INFO: fall back to the upstream coordinates
        # setuptools_scm already worked out, flagged as a non-release.
        node = version.node or "gunknown"
        parts = ["gdh", "0-0", str(version.distance or 0), node]
        if version.dirty:
            parts.append("dirty")
        return "+" + ".".join(parts)
    return scm_local_segment(state)


# --- bump proposal --------------------------------------------------------------


def scan_range(state: dict) -> str:
    if state["has_gdh_tag"] and not state["upstream_moved"]:
        return f"v{state['upstream']}-gdh-{state['major']}.{state['minor']}..HEAD"
    return f"v{state['upstream']}..HEAD"


def classify(commit_range: str, root: Path | None = None) -> tuple[str, list[str]]:
    """Scan Conventional Commits in the range; return (level, reasons).

    Merge commits are skipped: their subjects carry no type, while the commits
    they bring in do.
    """
    raw = git("log", "--no-merges", "--format=%H%x00%s%x00%b%x1e", commit_range, root=root)
    level, reasons = "none", []

    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split("\x00", 2) + ["", ""])[:3]
        short = sha[:8]

        if BREAKING_SUBJECT_RE.match(subject):
            level = "major"
            reasons.append(f"{short} MAJOR (breaking `!:`)  {subject}")
        elif BREAKING_BODY_RE.search(body):
            level = "major"
            reasons.append(f"{short} MAJOR (BREAKING CHANGE trailer)  {subject}")
        elif FEAT_SUBJECT_RE.match(subject):
            if level != "major":
                level = "minor"
            reasons.append(f"{short} minor (feat)  {subject}")

    return level, reasons


def next_tag(state: dict, level: str) -> str:
    if state["upstream_moved"] or not state["has_gdh_tag"]:
        return f"v{state['upstream']}-gdh-1.0"
    if level == "major":
        return f"v{state['upstream']}-gdh-{state['major'] + 1}.0"
    return f"v{state['upstream']}-gdh-{state['major']}.{state['minor'] + 1}"


def do_show(args: argparse.Namespace) -> int:
    state = resolve()
    if args.json:
        import json

        print(json.dumps({**state, "canonical": canonical(state), "pep440": pep440(state)}, indent=2))
    elif args.pep440:
        print(pep440(state))
    else:
        print(canonical(state))
    return 0


def do_propose(args: argparse.Namespace) -> int:
    state = resolve()
    commit_range = scan_range(state)
    scanned, reasons = classify(commit_range)
    level = scanned if args.level == "auto" else args.level

    print(f"upstream base    {state['upstream']}  (nearest upstream release tag)")
    print(f"current version  {canonical(state)}")
    print(f"           PEP 440  {pep440(state)}")
    print(f"scanned range    {commit_range}")
    if state["upstream_moved"]:
        print(
            f"note             upstream moved to {state['upstream']} since the last gdh "
            "tag; the GDH counters reset to 1.0"
        )
    print()

    if reasons:
        print("commits driving the level:")
        for reason in reasons:
            print(f"  {reason}")
    else:
        print("no feat/breaking commits in range")
    print()

    if level == "none":
        print("proposed         no bump -- nothing in range warrants a release")
        print("                 (override with --level minor or --level major)")
        return 2

    proposed = next_tag(state, level)
    source = "scanned" if args.level == "auto" else "forced"
    print(f"proposed level   {level}  ({source})")
    print(f"proposed tag     {proposed}")
    return 0


def do_bump(args: argparse.Namespace) -> int:
    state = resolve()
    if state["dirty"]:
        raise VersionError("refusing to tag a dirty working tree; commit or clean it first")

    commit_range = scan_range(state)
    scanned, _ = classify(commit_range)
    level = scanned if args.level == "auto" else args.level
    if level == "none":
        raise VersionError(
            f"nothing in {commit_range} warrants a release; "
            "pass --level minor or --level major to force one"
        )

    tag = next_tag(state, level)
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False):
        raise VersionError(f"tag {tag} already exists")

    git("tag", "-a", tag, "-m", f"GDH release {tag.lstrip('v')}")
    print(f"created {tag}")

    if args.push:
        # `origin` is upstream on some checkouts and the fork on others, so the
        # remote is named explicitly by the caller's environment, not guessed.
        git("push", args.remote, tag)
        print(f"pushed {tag} to {args.remote}")
    else:
        print(f"not pushed; run: git push {args.remote} {tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the resolved version")
    group = show.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="print the full resolution")
    group.add_argument("--pep440", action="store_true", help="print the PEP 440 encoding")
    show.set_defaults(func=do_show)

    propose = sub.add_parser("propose", help="scan commits and print the next bump")
    propose.add_argument("--level", choices=("auto", "major", "minor"), default="auto")
    propose.set_defaults(func=do_propose)

    bump = sub.add_parser("bump", help="create the next gdh tag")
    bump.add_argument("--level", choices=("auto", "major", "minor"), default="auto")
    bump.add_argument("--push", action="store_true", help="push the tag")
    bump.add_argument(
        "--remote",
        default="origin",
        help="remote to push to (this checkout may call the GDH fork `fork`)",
    )
    bump.set_defaults(func=do_bump)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except VersionError as exc:
        print(f"gdh_version: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
