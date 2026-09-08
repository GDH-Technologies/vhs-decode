"""Tests for the -v/--version flag on the shared decoder argument parser,
and for the GDH fork version encoding it reports."""
import importlib.util
from pathlib import Path

import pytest

from vhsdecode.cmdcommons import common_parser_cli, get_version_string, to_canonical_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_get_version_string_is_nonempty():
    version = get_version_string()
    assert isinstance(version, str)
    assert version


def test_version_flag_prints_version_and_exits_zero(capsys):
    parser, _ = common_parser_cli("test")
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    assert get_version_string() in capsys.readouterr().out


def test_short_version_flag_matches_long(capsys):
    parser, _ = common_parser_cli("test")
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["-v"])
    assert excinfo.value.code == 0
    assert get_version_string() in capsys.readouterr().out


def _load_gdh_version():
    """Load scripts/gdh_version.py the way setup.py does (it is not packaged)."""
    path = REPO_ROOT / "scripts" / "gdh_version.py"
    spec = importlib.util.spec_from_file_location("gdh_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "pep440, canonical",
    [
        ("0.4.0+gdh.1.0", "0.4.0-gdh-1.0"),
        ("0.4.0+gdh.1.0.13.g143a89f8", "0.4.0-gdh-1.0+13.g143a89f8"),
        ("0.4.0+gdh.0.0.129.g143a89f8.dirty", "0.4.0-gdh-0.0+129.g143a89f8.dirty"),
        # The literal written to _version.py keeps the pre-normalisation form.
        ("0.4.0+gdh.1-0.13.g143a89f8", "0.4.0-gdh-1.0+13.g143a89f8"),
        # Non-GDH versions must pass through untouched.
        ("0.4.1.dev129", "0.4.1.dev129"),
        ("0.4.0", "0.4.0"),
        ("unknown", "unknown"),
    ],
)
def test_to_canonical_version(pep440, canonical):
    assert to_canonical_version(pep440) == canonical


def test_display_helper_matches_the_build_side_implementation():
    """cmdcommons carries its own copy because scripts/ is not installed."""
    gdh = _load_gdh_version()
    for version in (
        "0.4.0+gdh.1.0",
        "0.4.0+gdh.2.3.7.gdeadbeef",
        "0.4.0+gdh.1-0.13.g143a89f8.dirty",
        "0.4.1.dev129",
        "0.4.0",
    ):
        assert to_canonical_version(version) == gdh.to_canonical(version)


def test_local_segment_survives_setuptools_scm_dedup():
    """Counters must stay intact through combine_version_with_local_parts.

    That helper drops any local segment it has already seen, so dot-separated
    counters silently lose one: gdh.1.1.5.g<sha> would become gdh.1.5.g<sha>.
    scm_local_segment joins them with "-" to keep them a single segment.
    """
    gdh = _load_gdh_version()
    state = {
        "upstream": "0.4.0",
        "major": 1,
        "minor": 1,
        "distance": 5,
        "node": "g143a89f8",
        "dirty": False,
        "exact": False,
    }
    assert gdh.local_segment(state) == "+gdh.1.1.5.g143a89f8"
    assert gdh.scm_local_segment(state) == "+gdh.1-1.5.g143a89f8"
    # The hyphen form normalises back to what local_segment describes.
    assert gdh.to_canonical(gdh.scm_local_segment(state).replace("+gdh", "0.4.0+gdh", 1)) == (
        gdh.to_canonical(gdh.pep440(state))
    )


def test_exact_release_has_no_distance_suffix():
    gdh = _load_gdh_version()
    state = {
        "upstream": "0.4.0",
        "major": 2,
        "minor": 0,
        "distance": 0,
        "node": "g143a89f8",
        "dirty": False,
        "exact": True,
    }
    assert gdh.pep440(state) == "0.4.0+gdh.2.0"
    assert gdh.canonical(state) == "0.4.0-gdh-2.0"
