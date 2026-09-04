#!/usr/bin/env python3

from setuptools import setup
import os
import shlex
import subprocess
import sys
import sysconfig
import tempfile
import distutils.ccompiler
from distutils.extension import Extension
from Cython.Build import cythonize

# Uncomment to view C code generated from Cython files
# import Cython.Compiler.Options
# Cython.Compiler.Options.annotate = True

import numpy

def _executables_on_path(name):
    """Every executable called `name` on PATH, in PATH order, de-duplicated.

    Compares path strings rather than realpath() on purpose: toolchain shims
    (swiftly points clang, clang++ and a dozen other names at one `swiftly`
    binary) would otherwise collapse into a single candidate."""
    found = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, name)
        if candidate in found:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            found.append(candidate)
    return found


def _links_with_flto(cc):
    """Can this compiler actually complete an -flto link?

    -flto makes the compiler hand the linker an LTO plugin from its *own*
    toolchain. Toolchains that ship a compiler but no plugin -- Swift's swiftly
    is one, some conda and nix channels are others -- compile happily and then
    die at link time with

        LLVMgold.so: error loading plugin ... cannot open shared object file

    Since such a toolchain routinely shadows /usr/bin/clang on PATH, and the
    failure only appears at link time, actually trying it is the only reliable
    test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        source = os.path.join(tmpdir, "probe.c")
        with open(source, "w") as handle:
            handle.write("int probe(void) { return 0; }\n")
        try:
            subprocess.run(
                [cc, "-shared", "-fPIC", "-flto",
                 source, "-o", os.path.join(tmpdir, "probe.so")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return False
    return True


compiler = distutils.ccompiler.new_compiler()

if compiler.compiler_type == "unix":
    use_lto = True

    if os.environ.get("CC"):
        # The caller named a compiler; never second-guess it. Only check that
        # -flto won't sink their build.
        use_lto = _links_with_flto(shlex.split(os.environ["CC"])[0])
    else:
        # Prefer clang for the code it generates, but only a clang that can
        # finish an LTO link -- see _links_with_flto. Absolute paths, so the
        # choice survives any later PATH change.
        for clang in _executables_on_path("clang"):
            if not _links_with_flto(clang):
                continue
            os.environ["CC"] = clang
            if os.access(clang + "++", os.X_OK):
                os.environ["CXX"] = clang + "++"
            break
        else:
            # No usable clang. Fall back to whatever built Python, and keep
            # -flto only if that compiler can link with it.
            default_cc = sysconfig.get_config_var("CC") or "cc"
            use_lto = _links_with_flto(shlex.split(default_cc)[0])

    if not use_lto:
        print(
            "setup.py: building without -flto, no available compiler could "
            "complete an LTO link. Set CC (and CXX) to a compiler whose "
            "toolchain ships an LTO plugin to re-enable it.",
            file=sys.stderr,
        )

    extra_compile_args=["-O3"] + (["-flto"] if use_lto else [])
    extra_link_args=["-O3"] + (["-flto"] if use_lto else [])
else:
    extra_compile_args=[]
    extra_link_args=[]

# Release/performance safety: default Rust extension builds to cargo's
# release profile unless a caller explicitly overrides it.
os.environ.setdefault("SETUPTOOLS_RUST_CARGO_PROFILE", "release")

setup(
    # name='ld-decode',
    # version='7',
    # description='Software defined LaserDisc decoder',
    # url='https://github.com/happycube/ld-decode',
    # keywords=['video', 'LaserDisc'],
    # classifiers=[
    #    'Environment :: Console',
    #    'Environment :: X11 Applications :: Qt',
    #    'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
    #    'Programming Language :: C++',
    #    'Programming Language :: Python :: 3',
    #    'Topic :: Multimedia :: Video :: Capture',
    # ],
    setup_requires=["cython"],
    packages=[
        "lddecode",
        "vhsdecode",
        "vhsdecode/addons",
        "vhsdecode/format_defs",
        "cvbsdecode",
        "vhsdecode/hifi",
        "filter_tune",
    ],
    # TODO: should be done in pyproject.toml but did not find any way
    # of including without making them modules.
    scripts=[
        "ld-cut",
        "scripts/cx-expander",
        "decode.py",
    ],
    # scripts=[
    #    'cx-expander',
    #    'ld-cut',
    #    'ld-decode',
    #    'scripts/ld-compress',
    #    'vhs-decode',
    #    'cvbs-decode',
    #    'hifi-decode',
    # ],
    ext_modules=cythonize([
        Extension(
            "vhsdecode.sync",
            ["vhsdecode/sync.pyx"],
            language_level=3,
            extra_compile_args=extra_compile_args,
            extra_link_args=extra_link_args
        ),
        Extension(
            "vhsdecode.linear_filter",
            ["vhsdecode/linear_filter.pyx"],
            language_level=3,
            extra_compile_args=extra_compile_args,
            extra_link_args=extra_link_args
        )
    ]),
    # Needed for using numpy in cython.
    include_dirs=[numpy.get_include()],
    # These are just the minimal runtime dependencies for the Python scripts --
    # see the documentation for the full list of dependencies.
    provides=["lddecode"],
    requires=["matplotlib", "numba", "numpy", "scipy", "Cython"],
)
