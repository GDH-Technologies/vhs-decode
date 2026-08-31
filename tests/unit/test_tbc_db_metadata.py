"""Unit tests for the .tbc.db metadata vocabulary helpers.

Kept import-light on purpose (no numpy/scipy/compiled extensions) so they
run on any checkout; the decoder/system wiring into the live writer is
covered by decoding a real capture with --write_db and inspecting the
capture row.
"""
from lddecode.tbc_db import DECODER_LD, DECODER_VHS, db_system_value


def test_pal_m_json_spelling_maps_to_the_check_vocabulary():
    # The legacy JSON writes "PAL-M" (hyphen); the schema CHECK only admits
    # 'PAL_M'. Writing the JSON spelling trips the constraint mid-decode.
    assert db_system_value("PAL-M") == "PAL_M"


def test_plain_systems_pass_through():
    assert db_system_value("NTSC") == "NTSC"
    assert db_system_value("PAL") == "PAL"
    assert db_system_value("PAL_M") == "PAL_M"


def test_decoder_constants_match_the_schema_check_vocabulary():
    # capture.decoder CHECK (decoder IN ('ld-decode','vhs-decode')) --
    # decode-orc selects its pipeline from this column.
    assert DECODER_LD == "ld-decode"
    assert DECODER_VHS == "vhs-decode"
