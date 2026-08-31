"""Pure helpers for the .tbc.db SQLite metadata sidecar (schema user_version 1).

Import-light on purpose (stdlib only): these are shared by the ld-decode,
vhs-decode and cvbs-decode writers and by their tests.
"""

# capture.decoder CHECK vocabulary: (decoder IN ('ld-decode','vhs-decode')).
# Downstream tooling (decode-orc) selects its processing pipeline from this
# column, so vhs-decode -- and cvbs-decode, which ships with it -- must not
# masquerade as ld-decode.
DECODER_LD = "ld-decode"
DECODER_VHS = "vhs-decode"


def db_system_value(system):
    """Map a videoParameters ``system`` string to the capture.system CHECK.

    The legacy JSON metadata spells PAL-M with a hyphen, but the SQLite
    schema CHECK only admits 'PAL_M'; writing the JSON spelling raises an
    IntegrityError mid-decode.
    """
    return "PAL_M" if system == "PAL-M" else system
