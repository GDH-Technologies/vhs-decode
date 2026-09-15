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

from lddecode.tbc_db import check_field_numbering, renumber_fields
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


class TestCheckFieldNumbering:
    """tbc_db.check_field_numbering mirrors tbc-tools' checkFieldNumbering."""

    def test_a_clean_array_is_valid_and_says_nothing(self):
        report = check_field_numbering([{"seqNo": n + 1} for n in range(6)])
        assert report.is_valid
        assert report.summary() == ""

    def test_shape_a_repeats_a_number_and_skips_one(self):
        """VHS-C_03_Recital: 113827 records, one repeat, one gap.

        The array is as long as the payload -- nothing was lost, only
        misnumbered -- which is why renumbering mends it.
        """
        fields = [{"seqNo": n + 1} for n in range(60)]
        fields[60:] = [{"seqNo": 59}, {"seqNo": 61}, {"seqNo": 63}]

        report = check_field_numbering(fields)

        assert not report.is_valid
        assert report.duplicates == 1
        assert report.gaps == 1
        assert report.first_bad_index == 60
        assert report.first_bad_seq_no == 59
        assert (
            "first break at entry 60, numbered 59 rather than 61"
            in report.summary()
        )

    def test_shape_b_declares_more_than_it_holds(self):
        """VHS-C_04_JJ_Promo: 81641 records for 81653 field images.

        Records genuinely missing, so the count disagreement is the finding
        and renumbering would only hide it.
        """
        fields = [{"seqNo": n + 1} for n in range(720)]
        fields += [{"seqNo": n} for n in (722, 723, 724)]

        report = check_field_numbering(fields, declared_fields=724)

        assert not report.is_valid
        assert report.declared_fields == 724
        assert report.actual_fields == 723
        assert report.gaps == 1
        assert "declares 724 fields but holds 723" in report.summary()

    def test_an_empty_array_is_valid(self):
        assert check_field_numbering([]).is_valid

    def test_a_record_with_no_number_is_a_break_at_its_position(self):
        report = check_field_numbering([{"seqNo": 1}, {"fileLoc": 7}, {"seqNo": 3}])
        assert not report.is_valid
        assert report.first_bad_index == 1


class TestRenumberFields:
    def test_restamps_from_position_and_keeps_the_rest(self):
        fields = [{"seqNo": 59, "fileLoc": 1}, {"seqNo": 59, "fileLoc": 2}]
        mended = renumber_fields(fields)

        assert [f["seqNo"] for f in mended] == [1, 2]
        assert [f["fileLoc"] for f in mended] == [1, 2]
        assert check_field_numbering(mended).is_valid

    def test_leaves_the_source_untouched(self):
        fields = [{"seqNo": 59}, {"seqNo": 59}]
        renumber_fields(fields)
        assert [f["seqNo"] for f in fields] == [59, 59]

    def test_keeps_every_record_because_each_one_is_a_written_field(self):
        """Dropping the repeat instead would shift the payload by one.

        The filler copy is a field image on disk like any other, so an array
        one record shorter would name a different image for every field after
        the break.
        """
        fields = [{"seqNo": 1}, {"seqNo": 2}, {"seqNo": 1}, {"seqNo": 3}]
        assert len(renumber_fields(fields)) == len(fields)
