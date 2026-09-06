"""Unit tests for the .tbc.db schema v2 additions and the v1 -> v2 migration.

Import-light on purpose (sqlite3 only) so they run on any checkout. The v1
DDL is embedded here verbatim -- the module only ever carries the current
schema, and the migration must keep working against what old decodes
actually wrote.
"""
import sqlite3

import pytest

from lddecode.tbc_db import (
    SCHEMA_SQL,
    SCHEMA_USER_VERSION,
    SEGMENTATION_DDL,
    _FIELD_CHILD_TABLES,
    migrate_schema,
)

# The schema every --write_db decode created before this change.
V1_SCHEMA_SQL = """\
PRAGMA user_version = 1;

CREATE TABLE capture (
    capture_id INTEGER PRIMARY KEY,
    system TEXT NOT NULL CHECK (system IN ('NTSC','PAL','PAL_M')),
    decoder TEXT NOT NULL CHECK (decoder IN ('ld-decode','vhs-decode')),
    git_branch TEXT,
    git_commit TEXT,
    video_sample_rate REAL,
    active_video_start INTEGER,
    active_video_end INTEGER,
    field_width INTEGER,
    field_height INTEGER,
    number_of_sequential_fields INTEGER,
    colour_burst_start INTEGER,
    colour_burst_end INTEGER,
    is_mapped INTEGER CHECK (is_mapped IN (0,1)),
    is_subcarrier_locked INTEGER CHECK (is_subcarrier_locked IN (0,1)),
    is_widescreen INTEGER CHECK (is_widescreen IN (0,1)),
    white_16b_ire INTEGER,
    black_16b_ire INTEGER,
    blanking_16b_ire INTEGER,
    capture_notes TEXT
);

CREATE TABLE pcm_audio_parameters (
    capture_id INTEGER PRIMARY KEY REFERENCES capture(capture_id) ON DELETE CASCADE,
    bits INTEGER,
    is_signed INTEGER CHECK (is_signed IN (0,1)),
    is_little_endian INTEGER CHECK (is_little_endian IN (0,1)),
    sample_rate REAL
);

CREATE TABLE field_record (
    capture_id INTEGER NOT NULL REFERENCES capture(capture_id) ON DELETE CASCADE,
    field_id INTEGER NOT NULL,
    audio_samples INTEGER,
    decode_faults INTEGER,
    disk_loc REAL,
    efm_t_values INTEGER,
    field_phase_id INTEGER,
    file_loc INTEGER,
    is_first_field INTEGER CHECK (is_first_field IN (0,1)),
    median_burst_ire REAL,
    pad INTEGER CHECK (pad IN (0,1)),
    sync_conf INTEGER,
    ntsc_is_fm_code_data_valid INTEGER CHECK (ntsc_is_fm_code_data_valid IN (0,1)),
    ntsc_fm_code_data INTEGER,
    ntsc_field_flag INTEGER CHECK (ntsc_field_flag IN (0,1)),
    ntsc_is_video_id_data_valid INTEGER CHECK (ntsc_is_video_id_data_valid IN (0,1)),
    ntsc_video_id_data INTEGER,
    ntsc_white_flag INTEGER CHECK (ntsc_white_flag IN (0,1)),
    PRIMARY KEY (capture_id, field_id)
);

CREATE TABLE vits_metrics (
    capture_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    b_psnr REAL,
    w_snr REAL,
    FOREIGN KEY (capture_id, field_id)
        REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id)
);

CREATE TABLE vbi (
    capture_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    vbi0 INTEGER NOT NULL,
    vbi1 INTEGER NOT NULL,
    vbi2 INTEGER NOT NULL,
    FOREIGN KEY (capture_id, field_id)
        REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id)
);

CREATE TABLE drop_outs (
    capture_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    field_line INTEGER NOT NULL,
    startx INTEGER NOT NULL,
    endx INTEGER NOT NULL,
    FOREIGN KEY (capture_id, field_id)
        REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id, field_line, startx, endx)
);

CREATE TABLE vitc (
    capture_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    vitc0 INTEGER NOT NULL,
    vitc1 INTEGER NOT NULL,
    vitc2 INTEGER NOT NULL,
    vitc3 INTEGER NOT NULL,
    vitc4 INTEGER NOT NULL,
    vitc5 INTEGER NOT NULL,
    vitc6 INTEGER NOT NULL,
    vitc7 INTEGER NOT NULL,
    FOREIGN KEY (capture_id, field_id)
        REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id)
);

CREATE TABLE closed_caption (
    capture_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    data0 INTEGER,
    data1 INTEGER,
    FOREIGN KEY (capture_id, field_id)
        REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id)
);
"""

NEW_TABLES = ("picture_metrics", "decoder_event", "segment")
NEW_INDEXES = ("decoder_event_field", "segment_start")


def _names(conn, kind):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        )
    }


def _capture_columns(conn):
    return [row[1] for row in conn.execute("PRAGMA table_info(capture)")]


def _make_v1_db(path, fields=5):
    conn = sqlite3.connect(str(path))
    conn.executescript(V1_SCHEMA_SQL)
    cur = conn.execute(
        "INSERT INTO capture (system, decoder, field_width, field_height,"
        " number_of_sequential_fields, white_16b_ire, black_16b_ire)"
        " VALUES ('NTSC', 'vhs-decode', 910, 263, ?, 51200, 17818)",
        (fields,),
    )
    capture_id = cur.lastrowid
    for field_id in range(fields):
        conn.execute(
            "INSERT INTO field_record (capture_id, field_id, file_loc,"
            " is_first_field) VALUES (?, ?, ?, ?)",
            (capture_id, field_id, field_id * 1000, 1 - field_id % 2),
        )
        conn.execute(
            "INSERT INTO vits_metrics (capture_id, field_id, b_psnr, w_snr)"
            " VALUES (?, ?, 40.0, 20.0)",
            (capture_id, field_id),
        )
    conn.commit()
    conn.close()
    return path


class TestFreshSchema:
    def test_is_version_2_with_the_column_tables_and_indexes(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA_SQL)

        assert SCHEMA_USER_VERSION == 2
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "rf_source_sample_rate_hz" in _capture_columns(conn)
        assert set(NEW_TABLES) <= _names(conn, "table")
        assert set(NEW_INDEXES) <= _names(conn, "index")

    def test_segmentation_ddl_is_the_if_not_exists_block(self):
        # The migration re-runs this block against a db that already has
        # the tables, so every statement in it must be IF NOT EXISTS.
        creates = [
            line for line in SEGMENTATION_DDL.splitlines() if line.startswith("CREATE ")
        ]
        assert len(creates) == 5  # three tables, two indexes
        for statement in creates:
            assert statement.startswith(("CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS")), statement
        assert SEGMENTATION_DDL in SCHEMA_SQL

    def test_picture_metrics_is_a_field_child_table(self):
        # --resume truncation deletes from every child table explicitly.
        assert "picture_metrics" in _FIELD_CHILD_TABLES

    def test_new_tables_take_the_rows_the_writers_produce(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO capture (capture_id, system, decoder,"
            " rf_source_sample_rate_hz) VALUES (1, 'NTSC', 'vhs-decode', 40e6)"
        )
        conn.execute(
            "INSERT INTO field_record (capture_id, field_id, is_first_field)"
            " VALUES (1, 0, 1)"
        )
        conn.execute(
            "INSERT INTO picture_metrics (capture_id, field_id, luma_mean_ire,"
            " noise_ire) VALUES (1, 0, 30.0, NULL)"
        )
        conn.execute(
            "INSERT INTO decoder_event (capture_id, field_id, kind, file_loc,"
            " rf_delta_samples, rf_delta_fields, source, detail_json)"
            " VALUES (1, 0, 'no_sync_pulses', 12345, 4000000, 5.994,"
            " 'decoder', '{\"jumpSamples\":4000000}')"
        )
        conn.execute(
            "INSERT INTO segment (capture_id, start_field, end_field_exclusive,"
            " kind, source) VALUES (1, 0, 100, 'clip', 'derived')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            # enabled is CHECKed to 0/1
            conn.execute(
                "INSERT INTO segment (capture_id, start_field, end_field_exclusive,"
                " kind, source, enabled) VALUES (1, 0, 100, 'clip', 'user', 2)"
            )
        assert conn.execute("SELECT enabled FROM segment").fetchone()[0] == 1
        assert conn.execute(
            "SELECT rf_source_sample_rate_hz FROM capture"
        ).fetchone()[0] == 40e6


class TestMigrateSchema:
    def test_v1_db_gains_the_column_and_tables_and_keeps_its_rows(self, tmp_path):
        db = _make_v1_db(tmp_path / "old.tbc.db", fields=5)
        conn = sqlite3.connect(str(db))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "rf_source_sample_rate_hz" not in _capture_columns(conn)
        assert not (set(NEW_TABLES) & _names(conn, "table"))

        migrate_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "rf_source_sample_rate_hz" in _capture_columns(conn)
        assert set(NEW_TABLES) <= _names(conn, "table")
        assert set(NEW_INDEXES) <= _names(conn, "index")
        # Existing rows survive untouched.
        assert conn.execute("SELECT COUNT(*) FROM field_record").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM vits_metrics").fetchone()[0] == 5
        assert conn.execute(
            "SELECT white_16b_ire, black_16b_ire FROM capture"
        ).fetchone() == (51200, 17818)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()

    def test_is_idempotent(self, tmp_path):
        db = _make_v1_db(tmp_path / "old.tbc.db", fields=3)
        conn = sqlite3.connect(str(db))
        migrate_schema(conn)
        conn.execute(
            "INSERT INTO decoder_event (capture_id, field_id, kind, source)"
            " VALUES (1, 2, 'redo', 'decoder')"
        )
        conn.commit()

        migrate_schema(conn)
        migrate_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _capture_columns(conn).count("rf_source_sample_rate_hz") == 1
        assert conn.execute("SELECT COUNT(*) FROM decoder_event").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM field_record").fetchone()[0] == 3
        conn.close()

    def test_fresh_v2_db_is_left_alone(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA_SQL)
        before = conn.execute(
            "SELECT sql FROM sqlite_master ORDER BY name"
        ).fetchall()

        migrate_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute(
            "SELECT sql FROM sqlite_master ORDER BY name"
        ).fetchall() == before
