"""Field numbering: one record per written field, numbered by its position.

Every consumer indexes a field by its number -- tbc-tools returns
``fields[n - 1]`` -- so the metadata is only readable while
``fields[i]["seqNo"] == i + 1`` and there is one record per field image on
disk. Decodes written before the stamp moved to writeout time broke both
halves of that, in two distinguishable shapes; the fleet assets named below
are the real files those shapes were read from.
"""

import sqlite3

import pytest

from lddecode.tbc_db import (
    OutputCounts,
    check_field_numbering,
    count_db_fields,
    count_output_fields,
    renumber_fields,
)
from lddecode.utils import FieldInfo
from vhsdecode.process import VHSDecode


def _seq_nos(info):
    return [field["seqNo"] for field in info.read()]


class TestFinalise:
    """FieldInfo.finalise is the single place a written field is numbered."""

    def test_numbers_each_written_field_from_its_position(self):
        info = FieldInfo()
        for _ in range(5):
            info.append(info.finalise({"isFirstField": True}))
        assert _seq_nos(info) == [1, 2, 3, 4, 5]

    def test_copies_so_one_record_cannot_be_written_twice(self):
        info = FieldInfo()
        built = {"fileLoc": 495575040}
        first = info.finalise(built)
        info.append(first)
        second = info.finalise(built)
        info.append(second)

        assert first is not second
        assert built.get("seqNo") is None  # the built record is left alone
        assert [first["seqNo"], second["seqNo"]] == [1, 2]

    def test_continues_a_seeded_resume(self):
        info = FieldInfo()
        info.seed([{"seqNo": n + 1} for n in range(4)])
        info.append(info.finalise({}))
        assert _seq_nos(info) == [1, 2, 3, 4, 5]


class TestDuplicateFiller:
    """The filler insertion is where the numbering used to break.

    A field whose parity repeats the previous one makes the caller write the
    field before it a second time and then the field itself. Both writes are
    real output -- two field images reach the .tbc -- so both need their own
    record and their own number.
    """

    def test_inserting_a_copy_keeps_the_numbering_contiguous(self):
        info = FieldInfo()
        for _ in range(60):
            info.append(info.finalise({"fileLoc": 0}))
        x = {"fileLoc": 495575040}  # the field the copy is taken from
        info.append(info.finalise(x))  # ... written normally as seqNo 61

        # Z repeats X's parity: the caller writes X again, then Z.
        z = {"fileLoc": 497018880}
        info.append(info.finalise(x))
        info.append(info.finalise(z))
        info.append(info.finalise({"fileLoc": 497664000}))

        written = _seq_nos(info)
        assert written == list(range(1, len(written) + 1))

    def test_the_pre_fix_shape_is_what_this_prevents(self):
        """VHS-C_03_Recital, entries 58-62: ``59, 60, 59, 61, 63``.

        Reproduced by numbering at buildmetadata time and appending that
        record as-is, which is what the JSON path did before the stamp moved
        to writeout. Pinned so a revert to it is visible as a failure rather
        than as 45 damaged decodes on the fleet.
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

    def test_the_copy_carries_the_duplicate_flag_not_the_trigger(self):
        original = {"seqNo": 59, "isDuplicateField": False, "fileLoc": 495575040}
        dataset = ("field", original, "picture", "audio", "efm")

        f, fi, picture, audio, efm = VHSDecode.duplicate_of(dataset)

        assert fi["isDuplicateField"] is True
        assert fi["fileLoc"] == original["fileLoc"]
        # The field's own record, already written, is not retro-flagged.
        assert original["isDuplicateField"] is False
        assert (f, picture, audio, efm) == ("field", "picture", "audio", "efm")


class TestCheckFieldNumbering:
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


class TestOutputCounts:
    def test_agreement_is_silent(self):
        counts = OutputCounts(
            fields_written=6, records=6, video_fields=6, chroma_fields=6,
            db_rows=6, db_span=6,
        )
        assert counts.is_valid
        assert counts.summary() == ""

    def test_unmeasured_sides_do_not_count_against_it(self):
        counts = OutputCounts(fields_written=6, records=6)
        assert counts.is_valid

    def test_a_short_payload_is_reported(self):
        counts = OutputCounts(fields_written=6, records=6, video_fields=5)
        assert not counts.is_valid
        assert "metadata holds 6 field records but found 5 video" in counts.summary()

    def test_records_missing_for_written_fields_is_reported(self):
        """Shape B seen from the writer's side: images written, records not."""
        counts = OutputCounts(
            fields_written=81653, records=81641, video_fields=81653,
        )
        assert not counts.is_valid
        summary = counts.summary()
        assert "81653 writeouts" in summary
        assert "81653 video" in summary

    def test_every_disagreeing_side_is_named(self):
        counts = OutputCounts(
            fields_written=6, records=6, video_fields=5, chroma_fields=5,
            db_rows=4, db_span=7,
        )
        summary = counts.summary()
        for expected in ("5 video", "5 chroma", "4 db rows", "7 db field_id span"):
            assert expected in summary


class TestCountOutputFields:
    def test_whole_fields_in_a_payload(self, tmp_path):
        tbc = tmp_path / "capture.tbc"
        tbc.write_bytes(b"\x00" * (910 * 263 * 2 * 3))
        assert count_output_fields(tbc, 910 * 263 * 2) == 3

    @pytest.mark.parametrize(
        "path, field_bytes",
        [(None, 100), ("/nonexistent/capture.tbc", 100), ("x", 0)],
    )
    def test_unmeasurable_is_none(self, path, field_bytes):
        assert count_output_fields(path, field_bytes) is None


class TestCountDbFields:
    def _db(self, tmp_path, field_ids):
        path = tmp_path / "capture.tbc.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE field_record (capture_id INTEGER, field_id INTEGER,"
            " PRIMARY KEY (capture_id, field_id))"
        )
        conn.executemany(
            "INSERT INTO field_record VALUES (1, ?)", [(i,) for i in field_ids]
        )
        conn.commit()
        conn.close()
        return path

    def test_rows_and_span_agree_on_a_sound_table(self, tmp_path):
        assert count_db_fields(self._db(tmp_path, range(6)), 1) == (6, 6)

    def test_a_hole_shows_as_a_span_past_the_rows(self, tmp_path):
        assert count_db_fields(self._db(tmp_path, [0, 1, 2, 4]), 1) == (4, 5)

    def test_an_unknown_capture_holds_no_fields(self, tmp_path):
        """Zero rows is a finding, not a missing measurement.

        The close-time check only asks about a capture the decode wrote, so
        an id with nothing under it means the rows went somewhere else.
        """
        assert count_db_fields(self._db(tmp_path, range(3)), 2) == (0, None)

    def test_an_unreadable_db_is_none(self, tmp_path):
        junk = tmp_path / "not.tbc.db"
        junk.write_text("not sqlite")
        assert count_db_fields(junk, 1) == (None, None)
