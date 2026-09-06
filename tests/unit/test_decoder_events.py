"""Unit tests for lddecode.decoder_events (stdlib only, no decoder wiring).

The emission sites in readfield/buildmetadata/writeout are exercised by
decoding real captures; this pins the log's arithmetic, its JSON and db
projections, and the --resume seed/unsent bookkeeping.
"""
import json
import sqlite3

import pytest

from lddecode.decoder_events import SEAM_KINDS, SOURCE_DECODER, DecoderEventLog
from lddecode.tbc_db import (
    DECODER_EVENT_COLUMNS,
    DECODER_EVENT_INSERT_SQL,
    SCHEMA_SQL,
    load_events_from_db,
)

NTSC_FPS = 30 / 1.001
RF_HZ = 40e6
# rf.freq_hz / (2 x FPS): the exact float, never the +1 bytes_per_field.
SAMPLES_PER_FIELD = RF_HZ / (2 * NTSC_FPS)


class TestRfDeltaFields:
    def test_set_rate_is_freq_over_twice_fps(self):
        log = DecoderEventLog()
        log.set_rate(RF_HZ, NTSC_FPS)
        assert log.rf_samples_per_field == SAMPLES_PER_FIELD

    def test_delta_in_fields_is_samples_over_the_nominal(self):
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        event = log.append("no_sync_pulses", 3, rf_delta_samples=2 * SAMPLES_PER_FIELD)
        assert event["rfDeltaSamples"] == round(2 * SAMPLES_PER_FIELD)
        assert event["rfDeltaFields"] == 2.0

    def test_rounds_to_three_decimals_and_keeps_the_sign(self):
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        event = log.append("redo", 0, rf_delta_samples=-1000000)
        assert event["rfDeltaSamples"] == -1000000
        assert event["rfDeltaFields"] == round(-1000000 / SAMPLES_PER_FIELD, 3)
        assert event["rfDeltaFields"] == -1.499

    def test_without_a_rate_only_the_samples_are_stored(self):
        log = DecoderEventLog()
        event = log.append("no_field_start", 0, rf_delta_samples=5000)
        assert event["rfDeltaSamples"] == 5000
        assert "rfDeltaFields" not in event

    def test_without_a_delta_neither_key_appears(self):
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        event = log.append("resume_seam", 4, file_loc=10)
        assert "rfDeltaSamples" not in event
        assert "rfDeltaFields" not in event


class TestToJson:
    def test_shape_keys_alphabetical_and_int_casts(self):
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        # Decoders hand over numpy/float positions; the record is plain ints.
        log.append(
            "no_sync_pulses",
            7.0,
            file_loc=123456.0,
            rf_delta_samples=4000000.0,
            detail={"jumpSamples": 4000000},
        )

        [event] = log.to_json()

        assert list(event) == sorted(event)
        assert list(event) == [
            "detailJson",
            "field",
            "fileLoc",
            "kind",
            "rfDeltaFields",
            "rfDeltaSamples",
            "source",
        ]
        assert type(event["field"]) is int and event["field"] == 7
        assert type(event["fileLoc"]) is int and event["fileLoc"] == 123456
        assert type(event["rfDeltaSamples"]) is int and event["rfDeltaSamples"] == 4000000
        assert event["kind"] == "no_sync_pulses"
        assert event["source"] == SOURCE_DECODER == "decoder"
        assert event["detailJson"] == '{"jumpSamples":4000000}'
        json.dumps(log.to_json(), allow_nan=False)

    def test_detail_json_is_compact_and_key_sorted(self):
        log = DecoderEventLog()
        event = log.append(
            "skipped_field", 1, detail={"distanceFields": 2.0, "action": "flip"}
        )
        assert event["detailJson"] == '{"action":"flip","distanceFields":2.0}'
        assert json.loads(event["detailJson"]) == {"action": "flip", "distanceFields": 2.0}

    def test_no_detail_no_key(self):
        log = DecoderEventLog()
        assert "detailJson" not in log.append("redo", 0)

    def test_returns_copies(self):
        log = DecoderEventLog()
        log.append("redo", 0)
        projection = log.to_json()
        projection[0]["kind"] = "tampered"
        projection.clear()
        assert log.to_json() == [{"field": 0, "kind": "redo", "source": "decoder"}]

    def test_empty_log_is_an_empty_list(self):
        assert DecoderEventLog().to_json() == []
        assert len(DecoderEventLog()) == 0


class TestDbRows:
    def test_row_order_matches_the_decoder_event_columns(self):
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        event = log.append(
            "dropped_field", 12, file_loc=999, rf_delta_samples=1500,
            detail={"distanceFields": 2.3},
        )
        [row] = log.to_db_rows(capture_id=1)
        assert row[0] == 1
        assert row[1:] == tuple(event.get(key) for key, _column in DECODER_EVENT_COLUMNS)

    def test_absent_members_are_null(self):
        log = DecoderEventLog()
        log.append("redo", 0)
        [row] = log.to_db_rows(capture_id=1)
        assert row == (1, 0, "redo", None, None, None, "decoder", None)

    def test_rows_round_trip_through_the_schema(self, tmp_path):
        db = tmp_path / "out.tbc.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(SCHEMA_SQL)
        conn.execute("INSERT INTO capture (capture_id, system, decoder) VALUES (1, 'NTSC', 'vhs-decode')")
        log = DecoderEventLog(SAMPLES_PER_FIELD)
        log.append("no_sync_pulses", 3, file_loc=100, rf_delta_samples=4000000, detail={"jumpSamples": 4000000})
        log.append("duplicate_field", 9, file_loc=200, rf_delta_samples=1400000, detail={"distanceFields": 2.1})
        log.append("redo", 9, file_loc=150, rf_delta_samples=-300)
        conn.executemany(DECODER_EVENT_INSERT_SQL, log.to_db_rows(1))
        conn.commit()
        conn.close()

        assert load_events_from_db(db, 1) == log.to_json()

    def test_to_db_rows_takes_a_subset(self):
        log = DecoderEventLog()
        log.append("redo", 0)
        new = [log.append("redo", 1)]
        assert [row[1] for row in log.to_db_rows(1, new)] == [1]


class TestSeedAndUnsent:
    def test_unsent_is_incremental(self):
        log = DecoderEventLog()
        assert log.unsent() == []
        first = log.append("redo", 0)
        second = log.append("redo", 1)
        assert log.unsent() == [first, second]
        assert log.unsent() == []
        third = log.append("redo", 2)
        assert log.unsent() == [third]
        assert len(log) == 3

    def test_seed_marks_recovered_events_as_sent(self):
        recovered = [
            {"field": 5, "kind": "no_sync_pulses", "source": "decoder", "fileLoc": 100},
            {"kind": "dropped_field", "field": 50, "source": "decoder"},
        ]
        log = DecoderEventLog(SAMPLES_PER_FIELD)

        log.seed(recovered)

        assert len(log) == 2
        assert log.unsent() == []  # already in the db
        # Keys come out alphabetical however they were stored.
        assert list(log[1]) == ["field", "kind", "source"]
        assert log.to_json()[0]["fileLoc"] == 100
        new = log.append("resume_seam", 94, file_loc=5000)
        assert log.unsent() == [new]
        assert [event["field"] for event in log] == [5, 50, 94]


class TestFieldSemantics:
    def test_field_is_the_index_of_the_next_written_field(self):
        # Callers pass len(fieldinfo) at emission: the 0-based output index
        # of the first field written at/after the event.
        log = DecoderEventLog()
        fields_written_so_far = 10
        event = log.append("no_field_start", fields_written_so_far)
        assert event["field"] == 10
        assert type(event["field"]) is int

    def test_kinds_are_free_text_for_the_two_writers(self):
        # No CHECK in the db: a later tbc-tools kind must not be refused.
        log = DecoderEventLog()
        assert log.append("gap", 0)["kind"] == "gap"

    def test_seam_class_kinds(self):
        assert SEAM_KINDS == {"no_sync_pulses", "no_field_start", "dropped_field", "resume_seam"}
        assert "duplicate_field" not in SEAM_KINDS
        assert "redo" not in SEAM_KINDS

    def test_a_missing_field_is_an_error_not_a_null_row(self):
        # field_id is NOT NULL in the db; refuse early rather than at commit.
        with pytest.raises(TypeError):
            DecoderEventLog().append("redo", field=None)
