"""Field numbering: one record per written field, numbered by its position.

Every consumer indexes a field by its number -- tbc-tools returns
``fields[n - 1]`` -- so the metadata is only readable while
``fields[i]["seqNo"] == i + 1`` and there is one record per field image on
disk. ``seqNo`` is stamped in ``writeout()`` on a copy made per write; the
duplicate-field compensation in ``readfield`` writes an already-written field
a second time, and numbering that dict at build time (as upstream still does)
is what produced VHS-C_03_Recital's ``59, 60, 59, 61, 63``.
"""

import io
import types

from lddecode.utils import FieldInfo
from vhsdecode.process import VHSDecode


def _seq_nos(info):
    return [field["seqNo"] for field in info.read()]


def _vhs_writer():
    """A VHSDecode reduced to what writeout() touches, with the real method."""
    stub = types.SimpleNamespace(
        resume_suppress_before_sample=None,
        fieldinfo=FieldInfo(),
        measure_picture=lambda picturey, picturec: None,
        _db_writer=None,
        outfile_video=io.BytesIO(),
        outfile_chroma=io.BytesIO(),
        rf=types.SimpleNamespace(options=types.SimpleNamespace(write_chroma=True)),
        fields_written=0,
    )
    stub.writeout = VHSDecode.writeout.__get__(stub)
    return stub


def _dataset(fi):
    return ("field", fi, (b"\x00\x01", b"\x02\x03"), None, None)


class TestWriteoutNumbering:
    """VHSDecode.writeout numbers a copy of the built record per write."""

    def test_numbers_each_written_field_from_its_position(self):
        vhsd = _vhs_writer()
        for n in range(5):
            # buildmetadata stamps its own guess; writeout must not trust it.
            vhsd.writeout(_dataset({"seqNo": 99, "fileLoc": n, "isFirstField": n % 2 == 0}))
        assert _seq_nos(vhsd.fieldinfo) == [1, 2, 3, 4, 5]
        assert vhsd.fields_written == 5

    def test_the_built_record_is_left_alone(self):
        vhsd = _vhs_writer()
        built = {"seqNo": 7, "fileLoc": 495575040, "isFirstField": True}
        vhsd.writeout(_dataset(built))
        (record,) = vhsd.fieldinfo.read()
        assert record is not built
        assert built["seqNo"] == 7
        assert record["seqNo"] == 1

    def test_the_duplicate_filler_gets_its_own_record_and_number(self):
        """readfield's compensation, as it calls writeout: X, Y, X again, Z.

        Both writes of X put a field image in the .tbc, so both need their
        own record and their own number.
        """
        vhsd = _vhs_writer()
        for n in range(58):
            vhsd.writeout(_dataset({"fileLoc": n, "isFirstField": n % 2 == 0}))
        x = {"fileLoc": 495575040, "isFirstField": True}
        y = {"fileLoc": 496250880, "isFirstField": False}
        z = {"fileLoc": 497018880, "isFirstField": False, "isDuplicateField": True}
        vhsd.writeout(_dataset(x))
        vhsd.writeout(_dataset(y))
        vhsd.writeout(_dataset(x))  # the filler: X written a second time
        vhsd.writeout(_dataset(z))
        vhsd.writeout(_dataset({"fileLoc": 497664000, "isFirstField": True}))

        records = vhsd.fieldinfo.read()
        assert [r["seqNo"] for r in records] == list(range(1, 64))
        assert [r["fileLoc"] for r in records[58:61]] == [x["fileLoc"], y["fileLoc"], x["fileLoc"]]
        assert records[58] is not records[60]
        assert vhsd.fields_written == 63
        assert len(vhsd.outfile_video.getvalue()) == 63 * 2

    def test_the_pre_fix_shape_is_what_this_prevents(self):
        """VHS-C_03_Recital, entries 58-62: ``59, 60, 59, 61, 63``.

        Reproduced by numbering at buildmetadata time and appending that
        record as-is, which is what the JSON path did before the stamp moved
        to writeout (and what upstream still does). Pinned so a revert to it
        is visible as a failure rather than as 45 damaged decodes on the
        fleet.
        """
        info = FieldInfo()
        for _ in range(58):
            info.append({"seqNo": len(info) + 1})

        x = {"seqNo": len(info) + 1}  # built at len 58 -> 59
        info.append(x)
        y = {"seqNo": len(info) + 1}  # built at len 59 -> 60
        info.append(y)
        z = {"seqNo": len(info) + 1}  # built at len 60 -> 61, trips the filler
        info.append(x)  # X written again, still carrying 59
        info.append(z)
        info.append({"seqNo": len(info) + 1})  # built at len 62 -> 63

        assert _seq_nos(info)[58:] == [59, 60, 59, 61, 63]
