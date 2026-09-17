"""Regression tests for importing rust_utils when vhsd_rust cannot be loaded.

``vhsd_rust`` can be present on disk and still fail to import: on macOS 27 the
dynamic loader rejects the stripped extension with

    dlopen(...vhsd_rust.cpython-314-darwin.so, 0x0002): mis-aligned LINKEDIT string pool

and an ABI or architecture mismatch fails the same way. That is a plain
``ImportError``, not the ``ModuleNotFoundError`` of an extension that was never
built. ``rust_utils`` used to catch only the latter, so the error escaped at
import time and took ``vhs-decode``, ``hifi-decode`` and ``cvbs-decode`` down
with it, even though a scipy fallback for exactly this case sits a few lines
below.

The module is loaded from its source file under a throwaway name, so these
tests never disturb the ``vhsdecode.rust_utils`` other tests hold on to.
"""

import importlib.abc
import importlib.util
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import cheby2, sosfiltfilt

RUST_UTILS = Path(__file__).resolve().parents[2] / "vhsdecode" / "rust_utils.py"

LOADER_MESSAGE = (
    "dlopen(vhsd_rust.cpython-314-darwin.so, 0x0002): "
    "mis-aligned LINKEDIT string pool"
)


class _RejectsExtension(importlib.abc.MetaPathFinder):
    """Makes ``import vhsd_rust`` fail the way a chosen loader failure would."""

    def __init__(self, error):
        self.error = error

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "vhsd_rust":
            raise self.error
        return None


def _import_rust_utils_with(monkeypatch, error):
    monkeypatch.delitem(sys.modules, "vhsd_rust", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_RejectsExtension(error)] + sys.meta_path)
    spec = importlib.util.spec_from_file_location("rust_utils_under_test", RUST_UTILS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExtensionPresentButUnloadable:
    def test_import_survives(self, monkeypatch):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rust_utils = _import_rust_utils_with(
                monkeypatch, ImportError(LOADER_MESSAGE, name="vhsd_rust")
            )

        assert rust_utils._HAS_VHSD_RUST is False

    def test_filtering_takes_the_scipy_path(self, monkeypatch):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rust_utils = _import_rust_utils_with(
                monkeypatch, ImportError(LOADER_MESSAGE, name="vhsd_rust")
            )
        sos = cheby2(N=4, rs=60, Wn=[0.1, 0.3], btype="bandpass", output="sos")
        data = np.random.default_rng(0).standard_normal(4096).astype(np.float32)

        result = rust_utils.sosfiltfilt_rust(sos, data)

        assert result.dtype == np.float32
        assert np.allclose(result, sosfiltfilt(sos, data), atol=1e-5)

    def test_the_loader_error_is_reported(self, monkeypatch):
        """The scipy path is far slower, so a node that lands on it must say why."""
        with pytest.warns(RuntimeWarning, match="mis-aligned LINKEDIT string pool"):
            _import_rust_utils_with(
                monkeypatch, ImportError(LOADER_MESSAGE, name="vhsd_rust")
            )


class TestExtensionNeverBuilt:
    def test_a_missing_extension_falls_back_without_a_warning(self, monkeypatch):
        """Editable installs never build vhsd_rust; that is expected, not news."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            rust_utils = _import_rust_utils_with(
                monkeypatch,
                ModuleNotFoundError("No module named 'vhsd_rust'", name="vhsd_rust"),
            )

        assert rust_utils._HAS_VHSD_RUST is False
