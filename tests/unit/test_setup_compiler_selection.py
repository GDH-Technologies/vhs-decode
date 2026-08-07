"""Regression tests for setup.py's compiler selection.

setup.py compiles the Cython extensions with ``-flto``. LTO makes the compiler
hand the linker a plugin (``LLVMgold.so``) from its *own* toolchain, and some
toolchains ship a clang but no plugin -- Swift's swiftly, some conda and nix
channels. Those toolchains routinely land ahead of /usr/bin on PATH, compile
happily, and only fail at link time with

    LLVMgold.so: error loading plugin ... cannot open shared object file

An earlier fix set ``os.environ.setdefault("CC", "clang")``, which only helped
callers who explicitly set CC -- the plain ``pipx install .`` path still picked
the shadowing compiler by bare name and still failed. setup.py now probes a real
-flto link instead of trusting PATH, so these tests drive it with a synthetic
broken compiler rather than depending on any particular toolchain being present.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The harness re-executes setup.py with this same interpreter, so it needs
# setuptools. Deployment venvs (pipx) routinely lack it; skip rather than fail.
pytest.importorskip("setuptools", reason="setup.py cannot be evaluated without setuptools")

# Runs setup.py far enough to settle CC/CXX and the extension flags, with the
# build-time dependencies stubbed out and setup() itself neutered.
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
    print("@@" + json.dumps({
        "cc": os.environ.get("CC"),
        "cxx": os.environ.get("CXX"),
        "compile_args": ns["extra_compile_args"],
        "link_args": ns["extra_link_args"],
    }))
    """
)


def run_setup(path, cc=None):
    """Evaluate setup.py's compiler selection under a controlled PATH."""
    import json

    env = dict(os.environ)
    env["PATH"] = path
    env.pop("CC", None)
    env.pop("CXX", None)
    if cc is not None:
        env["CC"] = cc

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


@pytest.fixture
def broken_clang(tmp_path):
    """A clang that compiles fine but cannot complete an -flto link.

    Mimics the Swift/conda/nix toolchains: everything works until -flto asks for
    a linker plugin the toolchain never shipped.
    """
    bindir = tmp_path / "brokenbin"
    bindir.mkdir()
    for name in ("clang", "clang++"):
        shim = bindir / name
        shim.write_text(
            "#!/bin/sh\n"
            'for arg in "$@"; do\n'
            '    if [ "$arg" = "-flto" ]; then\n'
            "        echo 'LLVMgold.so: error loading plugin' >&2\n"
            "        exit 1\n"
            "    fi\n"
            "done\n"
            'exec /usr/bin/cc "$@"\n'
        )
        shim.chmod(0o755)
    return bindir


SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"


@pytest.fixture(scope="module")
def host_can_lto():
    """Several assertions are meaningless if the host itself cannot do LTO."""
    if "-flto" not in run_setup(SYSTEM_PATH)["compile_args"]:
        pytest.skip("host toolchain cannot complete an -flto link")


def test_shadowing_toolchain_is_skipped(host_can_lto, broken_clang):
    """The reported failure: a broken clang first on PATH must not be chosen.

    Compares the *resolved* compiler, not the recorded string. A bare name like
    "clang" looks harmless in the metadata and still resolves to the shadowing
    toolchain at build time -- which is exactly how the previous fix passed
    inspection while the build kept failing.
    """
    path = f"{broken_clang}:{SYSTEM_PATH}"
    result = run_setup(path)

    resolved = result["cc"]
    if resolved is not None and not os.path.isabs(resolved):
        resolved = shutil.which(resolved, path=path)

    assert resolved != str(broken_clang / "clang"), (
        f"build would run the broken toolchain (CC={result['cc']!r})"
    )
    assert "-flto" in result["compile_args"], "should keep LTO via the working clang"
    assert "-flto" in result["link_args"]


def test_chosen_compiler_is_an_absolute_path(host_can_lto, broken_clang):
    """A bare name would re-resolve through PATH and find the broken clang again."""
    result = run_setup(f"{broken_clang}:{SYSTEM_PATH}")

    assert result["cc"] is not None
    assert os.path.isabs(result["cc"])


def test_lto_dropped_when_nothing_can_link_it(broken_clang):
    """With no usable compiler, the build must degrade rather than fail."""
    result = run_setup(str(broken_clang))

    assert "-flto" not in result["compile_args"]
    assert "-flto" not in result["link_args"]
    assert "-O3" in result["compile_args"], "optimisation should survive"


def test_explicit_cc_is_never_overridden(broken_clang):
    """A caller who names a compiler keeps it; only -flto is reconsidered."""
    chosen = str(broken_clang / "clang")
    result = run_setup(f"{broken_clang}:{SYSTEM_PATH}", cc=chosen)

    assert result["cc"] == chosen
    assert "-flto" not in result["compile_args"]


def test_clean_path_keeps_lto(host_can_lto):
    """Fleet hosts without a shadowing toolchain must be unaffected."""
    result = run_setup(SYSTEM_PATH)

    assert "-flto" in result["compile_args"]
    assert "-flto" in result["link_args"]
