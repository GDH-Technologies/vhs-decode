"""Regression tests for setup.py's Rust build defaults.

Cargo.toml's release profile sets ``strip = "symbols"``. On macOS rustc
implements that by running Apple's ``strip`` over the linked dylib, which leaves
the string table wherever the symbol tables happen to end. When that offset is
not a multiple of 8, macOS 27's dyld refuses the extension:

    dlopen(...vhsd_rust.cpython-314-darwin.so, 0x0002): mis-aligned LINKEDIT string pool

Whether a given build trips it depends on its symbol counts, so it comes and
goes between Python versions rather than failing everywhere at once. Cargo
profiles cannot be made conditional on the target OS, so setup.py turns the
strip pass off for darwin builds through Cargo's environment override and leaves
every other platform on Cargo.toml's setting.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("setuptools", reason="setup.py cannot be evaluated without setuptools")

# Evaluates setup.py with its build-time dependencies stubbed and setup() itself
# neutered, then drives the defaults helper with explicit platforms so the
# result does not depend on the OS running the tests.
_HARNESS = textwrap.dedent(
    """
    import json, os, sys, types
    for name in ("Cython", "Cython.Build", "numpy"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["Cython.Build"].cythonize = lambda *a, **k: []
    sys.modules["numpy"].get_include = lambda: ""
    import setuptools
    setuptools.setup = lambda **kw: None
    ns = {"__name__": "setup_probe"}
    exec(compile(open("setup.py").read(), "setup.py", "exec"), ns)

    def defaults(platform, environ):
        ns["_apply_cargo_defaults"](platform, environ)
        return environ

    print("@@" + json.dumps({
        "darwin": defaults("darwin", {}),
        "linux": defaults("linux", {}),
        "win32": defaults("win32", {}),
        "darwin_explicit": defaults("darwin", {"CARGO_PROFILE_RELEASE_STRIP": "symbols"}),
        "this_platform": sys.platform,
        "process_strip": os.environ.get("CARGO_PROFILE_RELEASE_STRIP"),
        "process_profile": os.environ.get("SETUPTOOLS_RUST_CARGO_PROFILE"),
    }))
    """
)


@pytest.fixture(scope="module")
def evaluated():
    env = dict(os.environ)
    env.pop("CARGO_PROFILE_RELEASE_STRIP", None)
    env.pop("SETUPTOOLS_RUST_CARGO_PROFILE", None)
    result = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "setup.py failed to evaluate:\n" + result.stdout + result.stderr
    )
    marker = [ln for ln in result.stdout.splitlines() if ln.startswith("@@")]
    assert marker, "harness produced no result:\n" + result.stdout + result.stderr
    return json.loads(marker[-1][2:])


def test_darwin_builds_skip_the_strip_pass(evaluated):
    assert evaluated["darwin"]["CARGO_PROFILE_RELEASE_STRIP"] == "none"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_other_platforms_keep_cargo_tomls_strip_setting(evaluated, platform):
    assert "CARGO_PROFILE_RELEASE_STRIP" not in evaluated[platform]


def test_an_explicit_strip_setting_is_respected(evaluated):
    """Someone re-testing a fixed Xcode must be able to turn the pass back on."""
    assert evaluated["darwin_explicit"]["CARGO_PROFILE_RELEASE_STRIP"] == "symbols"


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_every_platform_still_builds_the_release_profile(evaluated, platform):
    assert evaluated[platform]["SETUPTOOLS_RUST_CARGO_PROFILE"] == "release"


def test_the_defaults_reach_the_build_environment(evaluated):
    """Cargo runs in a child of the build backend and only sees os.environ."""
    expected_strip = "none" if evaluated["this_platform"] == "darwin" else None

    assert evaluated["process_strip"] == expected_strip
    assert evaluated["process_profile"] == "release"
