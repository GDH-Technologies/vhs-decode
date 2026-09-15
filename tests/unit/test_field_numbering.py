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
import json
import sqlite3
import types

import pytest

from lddecode.tbc_db import (
    OutputCounts,
    audit_outputs,
    check_field_numbering,
    count_db_fields,
    count_json_fields,
    count_output_fields,
    renumber_fields,
)
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


class TestOutputCounts:
    def test_agreement_is_silent(self):
        counts = OutputCounts(
            fields_written=6, records=6, video_fields=6, chroma_fields=6,
            db_rows=6, db_span=6, json_records=6,
            json_numbering=check_field_numbering([{"seqNo": n + 1} for n in range(6)]),
        )
        assert counts.is_valid
        assert counts.summary() == ""

    def test_unmeasured_sides_do_not_count_against_it(self):
        counts = OutputCounts(fields_written=6, records=6)
        assert counts.is_valid

    def test_the_json_that_landed_is_weighed(self):
        """An aborted decode leaves the .tbc.json at its last periodic flush.

        VHS_04_Martin_Luther_(1953) is the fleet's specimen: 279500 records
        written -- exactly 559 flushes of 500 -- against 279810 field images,
        the decode having been killed outright. Nothing else in the
        reconciliation sees that, because the decoder counted all 279810.
        """
        counts = OutputCounts(
            fields_written=279810,
            records=279810,
            json_records=279500,
            video_fields=279810,
            chroma_fields=279810,
        )
        assert not counts.is_valid
        assert "279500 json records" in counts.summary()

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

    def test_a_broken_json_numbering_is_reported(self):
        numbering = check_field_numbering([{"seqNo": 1}, {"seqNo": 1}, {"seqNo": 3}])
        counts = OutputCounts(
            fields_written=3, records=3, json_records=3, json_numbering=numbering
        )
        assert not counts.is_valid
        assert "json numbering broken (1 duplicate field number(s)" in counts.summary()

    def test_every_disagreeing_side_is_named(self):
        counts = OutputCounts(
            fields_written=6, records=6, json_records=3, video_fields=5,
            chroma_fields=5, db_rows=4, db_span=7,
        )
        summary = counts.summary()
        for expected in (
            "3 json records", "5 video", "5 chroma", "4 db rows",
            "7 db field_id span",
        ):
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


def _field_record_db(path, field_ids):
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


class TestCountDbFields:
    def test_rows_and_span_agree_on_a_sound_table(self, tmp_path):
        db = _field_record_db(tmp_path / "capture.tbc.db", range(6))
        assert count_db_fields(db, 1) == (6, 6)

    def test_a_hole_shows_as_a_span_past_the_rows(self, tmp_path):
        db = _field_record_db(tmp_path / "capture.tbc.db", [0, 1, 2, 4])
        assert count_db_fields(db, 1) == (4, 5)

    def test_an_unknown_capture_holds_no_fields(self, tmp_path):
        """Zero rows is a finding, not a missing measurement.

        The audit only asks about a capture the decode wrote, so an id with
        nothing under it means the rows went somewhere else.
        """
        db = _field_record_db(tmp_path / "capture.tbc.db", range(3))
        assert count_db_fields(db, 2) == (0, None)

    def test_an_unreadable_db_is_none(self, tmp_path):
        junk = tmp_path / "not.tbc.db"
        junk.write_text("not sqlite")
        assert count_db_fields(junk, 1) == (None, None)


def _write_json(path, fields, declared=None):
    payload = {
        "videoParameters": {
            "numberOfSequentialFields": len(fields) if declared is None else declared
        },
        "fields": fields,
    }
    path.write_text(json.dumps(payload))


class TestCountJsonFields:
    def test_records_and_numbering_from_a_sound_file(self, tmp_path):
        js = tmp_path / "out.tbc.json"
        _write_json(js, [{"seqNo": n + 1} for n in range(4)])
        records, numbering = count_json_fields(js)
        assert records == 4
        assert numbering.is_valid

    def test_the_declared_count_is_checked_against_the_array(self, tmp_path):
        js = tmp_path / "out.tbc.json"
        _write_json(js, [{"seqNo": n + 1} for n in range(4)], declared=5)
        _, numbering = count_json_fields(js)
        assert "declares 5 fields but holds 4" in numbering.summary()

    def test_missing_or_corrupt_is_none(self, tmp_path):
        assert count_json_fields(tmp_path / "missing.tbc.json") == (None, None)
        junk = tmp_path / "junk.tbc.json"
        junk.write_text("{not json")
        assert count_json_fields(junk) == (None, None)
        nofields = tmp_path / "nofields.tbc.json"
        nofields.write_text(json.dumps({"videoParameters": {}}))
        assert count_json_fields(nofields) == (None, None)


class TestAuditOutputs:
    """The post-run reconciliation vhsdecode/main.py runs from cleanup()."""

    FIELD_BYTES = 910 * 263 * 2

    def _outputs(self, tmp_path, *, video_fields, chroma_fields=None, json_fields=None):
        video = tmp_path / "out.tbc"
        video.write_bytes(b"\x00" * (self.FIELD_BYTES * video_fields))
        chroma = None
        if chroma_fields is not None:
            chroma = tmp_path / "out_chroma.tbc"
            chroma.write_bytes(b"\x00" * (self.FIELD_BYTES * chroma_fields))
        if json_fields is not None:
            _write_json(tmp_path / "out.tbc.json", json_fields)
        return str(tmp_path / "out"), str(video), (None if chroma is None else str(chroma))

    def test_agreement_is_valid(self, tmp_path):
        outname, video, chroma = self._outputs(
            tmp_path, video_fields=6, chroma_fields=6,
            json_fields=[{"seqNo": n + 1} for n in range(6)],
        )
        _field_record_db(tmp_path / "out.tbc.db", range(6))

        counts = audit_outputs(
            outname, fields_written=6, records=6, field_bytes=self.FIELD_BYTES,
            video_path=video, chroma_path=chroma, capture_id=1,
        )

        assert counts.is_valid
        assert counts.json_records == 6
        assert (counts.db_rows, counts.db_span) == (6, 6)

    def test_records_missing_for_written_images(self, tmp_path):
        """Shape B caught at the source: 6 field images, 5 records."""
        outname, video, _ = self._outputs(
            tmp_path, video_fields=6, json_fields=[{"seqNo": n + 1} for n in range(5)]
        )
        counts = audit_outputs(
            outname, fields_written=5, records=5, field_bytes=self.FIELD_BYTES,
            video_path=video,
        )
        assert not counts.is_valid
        assert "5 field records but found 6 video" in counts.summary()

    def test_a_lost_final_json_write(self, tmp_path):
        outname, video, _ = self._outputs(
            tmp_path, video_fields=6, json_fields=[{"seqNo": n + 1} for n in range(5)]
        )
        counts = audit_outputs(
            outname, fields_written=6, records=6, field_bytes=self.FIELD_BYTES,
            video_path=video,
        )
        assert not counts.is_valid
        assert "5 json records" in counts.summary()

    def test_a_misnumbered_json(self, tmp_path):
        fields = [{"seqNo": n + 1} for n in range(4)] + [{"seqNo": 4}, {"seqNo": 6}]
        outname, video, _ = self._outputs(tmp_path, video_fields=6, json_fields=fields)
        counts = audit_outputs(
            outname, fields_written=6, records=6, field_bytes=self.FIELD_BYTES,
            video_path=video,
        )
        assert not counts.is_valid
        assert "json numbering broken" in counts.summary()
        assert "first break at entry 4" in counts.summary()

    def test_no_db_and_no_json_is_still_measurable(self, tmp_path):
        outname, video, _ = self._outputs(tmp_path, video_fields=6)
        counts = audit_outputs(
            outname, fields_written=6, records=6, field_bytes=self.FIELD_BYTES,
            video_path=video,
        )
        assert counts.is_valid
        assert counts.json_records is None
        assert counts.db_rows is None

    def test_a_holed_db_is_reported(self, tmp_path):
        outname, video, _ = self._outputs(tmp_path, video_fields=5)
        _field_record_db(tmp_path / "out.tbc.db", [0, 1, 2, 4])
        counts = audit_outputs(
            outname, fields_written=5, records=5, field_bytes=self.FIELD_BYTES,
            video_path=video, capture_id=1,
        )
        assert not counts.is_valid
        assert "4 db rows" in counts.summary()
