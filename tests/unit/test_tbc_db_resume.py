"""Unit tests for the --resume planning/truncation helpers in lddecode.tbc_db.

Import-light on purpose (sqlite3 + tmp files only, no numpy/compiled
extensions) so they run on any checkout. The live seek/warm-up/append side
is covered by the kill-and-resume decode proof.
"""
import json
import sqlite3

import pytest

from lddecode.tbc_db import (
    SCHEMA_SQL,
    ResumeError,
    apply_resume_truncation,
    load_json_fields,
    minimal_fields_from_db,
    plan_resume,
)

FIELD_BYTES = 100  # tiny synthetic fields; the helpers never assume a size
STRIDE = 1000  # file_loc delta per field


def _phase_for(field_id, fn_offset=0):
    """The decoder's 4-cycle phase for a field, given field_number =
    field_id + fn_offset (mirrors vhsdecode's (isFirstField, (fn//2)%2)
    mapping)."""
    fn = field_id + fn_offset
    first = 1 - field_id % 2
    bit = (fn // 2) % 2
    return {(1, 0): 1, (0, 1): 2, (1, 1): 3, (0, 0): 4}[(first, bit)]


def _make_db(path, *, fields, system="NTSC", captures=1, stride=STRIDE, fn_offset=0):
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_SQL)
    capture_id = None
    for _ in range(captures):
        cur = conn.execute(
            "INSERT INTO capture (system, decoder, field_width, field_height,"
            " number_of_sequential_fields) VALUES (?, 'vhs-decode', 10, 5, ?)",
            (system, fields),
        )
        capture_id = cur.lastrowid
    for field_id in range(fields):
        conn.execute(
            "INSERT INTO field_record (capture_id, field_id, file_loc,"
            " is_first_field, field_phase_id) VALUES (?, ?, ?, ?, ?)",
            (
                capture_id,
                field_id,
                field_id * stride,
                1 - field_id % 2,
                _phase_for(field_id, fn_offset),
            ),
        )
        # Child rows so truncation must clean them up too (FK enforcement is
        # OFF on the writer's connections, so cascades cannot be relied on).
        conn.execute(
            "INSERT INTO vits_metrics (capture_id, field_id, b_psnr, w_snr)"
            " VALUES (?, ?, 40.0, 20.0)",
            (capture_id, field_id),
        )
        conn.execute(
            "INSERT INTO drop_outs (capture_id, field_id, field_line, startx,"
            " endx) VALUES (?, ?, 10, 0, 5)",
            (capture_id, field_id),
        )
    conn.commit()
    conn.close()
    return path


def _write_outputs(tmp_path, *, video_fields, chroma_fields=None):
    video = tmp_path / "out.tbc"
    video.write_bytes(b"\x00" * video_fields * FIELD_BYTES)
    chroma = None
    if chroma_fields is not None:
        chroma = tmp_path / "out_chroma.tbc"
        chroma.write_bytes(b"\x00" * chroma_fields * FIELD_BYTES)
    return video, chroma


class TestPlanResume:
    def test_happy_path_agreeing_db_and_files(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=100)
        video, chroma = _write_outputs(tmp_path, video_fields=100, chroma_fields=100)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=chroma.stat().st_size,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=FIELD_BYTES,
        )

        # field 99 is a second field -> the full 100 are kept.
        assert plan.field_count == 100
        assert plan.warmup_fields == 10
        assert plan.seek_sample == 90 * STRIDE
        # Suppression threshold: everything at or before the last kept
        # field's position is warm-up.
        assert plan.last_kept_sample == 99 * STRIDE
        assert plan.system == "NTSC"
        assert plan.decoder == "vhs-decode"

    def test_file_shorter_than_db_wins_and_parity_drops_a_trailing_first_field(
        self, tmp_path
    ):
        # A hard kill can lose buffered .tbc bytes while the per-field db
        # commits survived: the FILE is the binding constraint.
        db = _make_db(tmp_path / "out.tbc.db", fields=100)
        video, chroma = _write_outputs(tmp_path, video_fields=95, chroma_fields=95)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=chroma.stat().st_size,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=FIELD_BYTES,
        )

        # 95 fields would end on field 94 -- a FIRST field (its pair is
        # missing), so one more is dropped to end on a complete frame.
        assert plan.field_count == 94
        assert plan.seek_sample == 84 * STRIDE
        assert plan.last_kept_sample == 93 * STRIDE
        # An even warm-up preserves first/second-field alternation across
        # the seam (N always ends on a second field).
        assert plan.warmup_fields % 2 == 0

    def test_db_shorter_than_file_wins(self, tmp_path):
        # The tool writes the field's db row before its .tbc bytes on some
        # paths -- but a kill between commit and write leaves the file
        # behind; and the opposite (file ahead) happens when the JSON/db
        # thread lags. Either way min() rules.
        db = _make_db(tmp_path / "out.tbc.db", fields=80)
        video, chroma = _write_outputs(tmp_path, video_fields=100, chroma_fields=100)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=chroma.stat().st_size,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=FIELD_BYTES,
        )

        assert plan.field_count == 80

    def test_no_chroma_output_is_fine(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=50)
        video, _ = _write_outputs(tmp_path, video_fields=50)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=None,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=None,
        )

        assert plan.field_count == 50

    def test_short_decode_warms_up_from_zero(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=6)
        video, _ = _write_outputs(tmp_path, video_fields=6)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=None,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=None,
        )

        assert plan.field_count == 6
        assert plan.seek_sample == 0
        assert plan.warmup_fields == 6

    def test_field_number_seed_reconstructs_the_decoders_counter(self, tmp_path):
        """The chroma rotation cycle is anchored on the decoder's internal
        field_number, which is NOT the absolute field index whenever the
        original run hit a "readloc didn't advance" event. The stored
        fieldPhaseID sequence encodes its (mod 4) position -- the seed must
        reproduce it, not assume index == counter.

        Live-derived case: the GMV control run had field_number = index + 1;
        seeding by index alone flipped every second field's chroma phase.
        """
        for fn_offset in (0, 1, 2, 3):
            db = _make_db(
                tmp_path / f"out{fn_offset}.tbc.db", fields=100, fn_offset=fn_offset
            )
            video, _ = _write_outputs(tmp_path, video_fields=100)
            plan = plan_resume(
                db,
                video_bytes=video.stat().st_size,
                chroma_bytes=None,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=None,
            )
            # The warm-up start field must be numbered so the chain lands on
            # the same (fn // 2) % 2 sequence the prior run recorded.
            want = 90 + fn_offset
            assert plan.field_number_seed % 4 == want % 4, (
                f"fn_offset {fn_offset}: seed {plan.field_number_seed} !~ {want}"
            )

    def test_field_number_seed_falls_back_to_the_index_without_phases(self, tmp_path):
        conn_path = tmp_path / "nophase.tbc.db"
        conn = sqlite3.connect(str(conn_path))
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute(
            "INSERT INTO capture (system, decoder, field_width, field_height,"
            " number_of_sequential_fields) VALUES ('NTSC', 'vhs-decode', 10, 5, 20)"
        )
        cid = cur.lastrowid
        for field_id in range(20):
            conn.execute(
                "INSERT INTO field_record (capture_id, field_id, file_loc,"
                " is_first_field) VALUES (?, ?, ?, ?)",
                (cid, field_id, field_id * STRIDE, 1 - field_id % 2),
            )
        conn.commit()
        conn.close()
        video, _ = _write_outputs(tmp_path, video_fields=20)

        plan = plan_resume(
            conn_path,
            video_bytes=video.stat().st_size,
            chroma_bytes=None,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=None,
        )
        assert plan.field_number_seed == 20 - plan.warmup_fields

    def test_empty_db_refuses(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=0)
        video, _ = _write_outputs(tmp_path, video_fields=10)

        with pytest.raises(ResumeError):
            plan_resume(
                db,
                video_bytes=video.stat().st_size,
                chroma_bytes=None,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=None,
            )

    def test_unreadable_db_refuses(self, tmp_path):
        db = tmp_path / "out.tbc.db"
        db.write_bytes(b"not sqlite at all")
        video, _ = _write_outputs(tmp_path, video_fields=10)

        with pytest.raises(ResumeError):
            plan_resume(
                db,
                video_bytes=video.stat().st_size,
                chroma_bytes=None,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=None,
            )

    def test_two_capture_rows_refuse(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=10, captures=2)
        video, _ = _write_outputs(tmp_path, video_fields=10)

        with pytest.raises(ResumeError):
            plan_resume(
                db,
                video_bytes=video.stat().st_size,
                chroma_bytes=None,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=None,
            )


class TestApplyResumeTruncation:
    def test_truncates_files_rows_and_capture_count(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=100)
        video, chroma = _write_outputs(tmp_path, video_fields=95, chroma_fields=100)

        plan = plan_resume(
            db,
            video_bytes=video.stat().st_size,
            chroma_bytes=chroma.stat().st_size,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=FIELD_BYTES,
        )
        assert plan.field_count == 94

        apply_resume_truncation(
            db,
            plan,
            video_path=video,
            chroma_path=chroma,
            video_field_bytes=FIELD_BYTES,
            chroma_field_bytes=FIELD_BYTES,
        )

        assert video.stat().st_size == 94 * FIELD_BYTES
        assert chroma.stat().st_size == 94 * FIELD_BYTES
        conn = sqlite3.connect(str(db))
        try:
            assert conn.execute("SELECT COUNT(*) FROM field_record").fetchone()[0] == 94
            assert conn.execute("SELECT MAX(field_id) FROM field_record").fetchone()[0] == 93
            # Child rows past the cut are gone too -- FK enforcement is off
            # on the writer's connections, so this must not rely on cascades.
            assert conn.execute("SELECT COUNT(*) FROM vits_metrics").fetchone()[0] == 94
            assert conn.execute("SELECT COUNT(*) FROM drop_outs").fetchone()[0] == 94
            assert (
                conn.execute(
                    "SELECT number_of_sequential_fields FROM capture"
                ).fetchone()[0]
                == 94
            )
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()

    def test_is_idempotent(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=100)
        video, chroma = _write_outputs(tmp_path, video_fields=95, chroma_fields=100)

        for _ in range(2):
            plan = plan_resume(
                db,
                video_bytes=video.stat().st_size,
                chroma_bytes=chroma.stat().st_size,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=FIELD_BYTES,
            )
            apply_resume_truncation(
                db,
                plan,
                video_path=video,
                chroma_path=chroma,
                video_field_bytes=FIELD_BYTES,
                chroma_field_bytes=FIELD_BYTES,
            )

        assert plan.field_count == 94
        assert video.stat().st_size == 94 * FIELD_BYTES


class TestMinimalFieldsFromDb:
    def test_rebuilds_core_keys_in_order(self, tmp_path):
        db = _make_db(tmp_path / "out.tbc.db", fields=8)

        fields = minimal_fields_from_db(db, capture_id=1, field_count=6)

        assert len(fields) == 6
        assert [f["seqNo"] for f in fields] == [1, 2, 3, 4, 5, 6]
        assert fields[0]["isFirstField"] is True
        assert fields[1]["isFirstField"] is False
        assert fields[3]["fileLoc"] == 3 * STRIDE
        # decode_faults was stored as NULL-for-zero by the writer.
        assert fields[0]["decodeFaults"] == 0


class TestLoadJsonFields:
    def test_slices_a_parseable_fields_array(self, tmp_path):
        js = tmp_path / "out.tbc.json"
        js.write_text(json.dumps({"fields": [{"seqNo": i + 1} for i in range(20)]}))

        fields = load_json_fields(js, 15)

        assert fields is not None
        assert len(fields) == 15
        assert fields[-1]["seqNo"] == 15

    def test_too_short_or_missing_or_corrupt_returns_none(self, tmp_path):
        short = tmp_path / "short.tbc.json"
        short.write_text(json.dumps({"fields": [{}] * 5}))
        corrupt = tmp_path / "corrupt.tbc.json"
        corrupt.write_text("{not json")

        assert load_json_fields(short, 10) is None
        assert load_json_fields(corrupt, 3) is None
        assert load_json_fields(tmp_path / "missing.tbc.json", 3) is None
