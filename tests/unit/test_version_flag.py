"""Tests for the -v/--version flag on the shared decoder argument parser."""
import pytest

from vhsdecode.cmdcommons import common_parser_cli, get_version_string


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
