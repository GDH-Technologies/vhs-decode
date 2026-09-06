"""Pure helpers for the .tbc.db SQLite metadata sidecar (schema user_version 2).

Import-light on purpose (stdlib only): these are shared by the ld-decode,
vhs-decode and cvbs-decode writers, by --resume, and by their tests.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

# capture.decoder CHECK vocabulary: (decoder IN ('ld-decode','vhs-decode')).
# Downstream tooling (decode-orc) selects its processing pipeline from this
# column, so vhs-decode -- and cvbs-decode, which ships with it -- must not
# masquerade as ld-decode.
DECODER_LD = "ld-decode"
DECODER_VHS = "vhs-decode"

# PRAGMA user_version written by a fresh schema and reached by migrate_schema.
#   1: the original field_record + child tables
#   2: capture.rf_source_sample_rate_hz, picture_metrics, decoder_event, segment
SCHEMA_USER_VERSION = 2

# Schema v2 additions, shared verbatim with tbc-tools (its sqliteio.cpp
# carries the same text) so both writers create identical tables. Every
# statement is IF NOT EXISTS so the block doubles as the migration.
SEGMENTATION_DDL = """\
CREATE TABLE IF NOT EXISTS picture_metrics (                      -- one row per written field, when any metric is finite
    capture_id INTEGER NOT NULL, field_id INTEGER NOT NULL,
    luma_mean_ire REAL, field_diff_ire REAL, blanking_dev_ire REAL,
    sync_tip_dev_ire REAL, noise_ire REAL, burst_amp_ire REAL,      -- IRE, 2 dp; NULL = unmeasurable
    FOREIGN KEY (capture_id, field_id) REFERENCES field_record(capture_id, field_id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, field_id));

CREATE TABLE IF NOT EXISTS decoder_event (                        -- immutable facts the decoder knew; append-only
    event_id INTEGER PRIMARY KEY,
    capture_id INTEGER NOT NULL REFERENCES capture(capture_id) ON DELETE CASCADE,
    field_id INTEGER NOT NULL,                                     -- first written field at/after the event (0-based)
    kind TEXT NOT NULL,                                            -- vocabulary below; no CHECK (two writers)
    file_loc INTEGER, rf_delta_samples INTEGER, rf_delta_fields REAL,
    source TEXT NOT NULL,                                          -- 'decoder' | 'tbc-segments'
    detail_json TEXT);
CREATE INDEX IF NOT EXISTS decoder_event_field ON decoder_event(capture_id, field_id);

CREATE TABLE IF NOT EXISTS segment (                              -- the editable layer (decision 2)
    segment_id INTEGER PRIMARY KEY,                                -- stable; never renumbered; new = max + 1
    capture_id INTEGER NOT NULL REFERENCES capture(capture_id) ON DELETE CASCADE,
    start_field INTEGER NOT NULL, end_field_exclusive INTEGER NOT NULL,   -- 0-based, half-open
    kind TEXT NOT NULL,                                            -- 'clip' | 'blank' | 'noise' | 'unknown'
    source TEXT NOT NULL,                                          -- 'derived' | 'user'
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    title TEXT, comment TEXT, created_by TEXT, updated_at TEXT, derived_from TEXT);
CREATE INDEX IF NOT EXISTS segment_start ON segment(capture_id, start_field);
"""

# The canonical DDL (decode-orc mirrors it column-for-column). One home for
# the schema so the writers, --resume and the tests never drift apart.
SCHEMA_SQL = f"""\
PRAGMA user_version = {SCHEMA_USER_VERSION};

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
    capture_notes TEXT,
    rf_source_sample_rate_hz REAL
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

""" + SEGMENTATION_DDL

# Tables keyed (capture_id, field_id) hanging off field_record. Truncation
# deletes from them explicitly: the writers never enable PRAGMA
# foreign_keys, so ON DELETE CASCADE cannot be relied on.
_FIELD_CHILD_TABLES = (
    "vits_metrics",
    "vbi",
    "drop_outs",
    "vitc",
    "closed_caption",
    "picture_metrics",
)

# picture_metrics columns in the order the JSON keys sort, so one tuple
# serves the INSERT, the SELECT and the JSON projection.
PICTURE_METRIC_COLUMNS = (
    ("blankingDevIre", "blanking_dev_ire"),
    ("burstAmpIre", "burst_amp_ire"),
    ("fieldDiffIre", "field_diff_ire"),
    ("lumaMeanIre", "luma_mean_ire"),
    ("noiseIre", "noise_ire"),
    ("syncTipDevIre", "sync_tip_dev_ire"),
)

# decoder_event columns as vhs-decode writes them (JSON key, column). The
# event_id is the insertion order and is never carried in the JSON.
DECODER_EVENT_COLUMNS = (
    ("field", "field_id"),
    ("kind", "kind"),
    ("fileLoc", "file_loc"),
    ("rfDeltaSamples", "rf_delta_samples"),
    ("rfDeltaFields", "rf_delta_fields"),
    ("source", "source"),
    ("detailJson", "detail_json"),
)

# segment columns (JSON key, column); ``id``/``enabled`` need casts.
SEGMENT_COLUMNS = (
    ("id", "segment_id"),
    ("startField", "start_field"),
    ("endFieldExclusive", "end_field_exclusive"),
    ("kind", "kind"),
    ("source", "source"),
    ("enabled", "enabled"),
    ("title", "title"),
    ("comment", "comment"),
    ("createdBy", "created_by"),
    ("updatedAt", "updated_at"),
    ("derivedFrom", "derived_from"),
)


def _existing_tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def migrate_schema(conn):
    """Bring an open .tbc.db up to :data:`SCHEMA_USER_VERSION` in place.

    Idempotent: a v1 db gains the ``capture.rf_source_sample_rate_hz``
    column and the segmentation tables; a v2 db is left untouched. Rows
    are never rewritten. Run by the vhs-decode writer on ``--resume`` so a
    decode interrupted under the old schema continues under the new one.
    """
    tables = _existing_tables(conn)
    if "capture" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(capture)")}
        if "rf_source_sample_rate_hz" not in columns:
            conn.execute("ALTER TABLE capture ADD COLUMN rf_source_sample_rate_hz REAL")
    conn.executescript(SEGMENTATION_DDL)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_USER_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
    conn.commit()


def picture_metrics_row(capture_id, field_id, metrics):
    """The picture_metrics INSERT tuple for one field (NULL for absent keys)."""
    return (capture_id, field_id) + tuple(
        metrics.get(key) for key, _column in PICTURE_METRIC_COLUMNS
    )


PICTURE_METRICS_INSERT_SQL = (
    "INSERT INTO picture_metrics (capture_id, field_id, "
    + ", ".join(column for _key, column in PICTURE_METRIC_COLUMNS)
    + ") VALUES (?, ?, " + ", ".join("?" for _ in PICTURE_METRIC_COLUMNS) + ")"
)

DECODER_EVENT_INSERT_SQL = (
    "INSERT INTO decoder_event (capture_id, "
    + ", ".join(column for _key, column in DECODER_EVENT_COLUMNS)
    + ") VALUES (?, " + ", ".join("?" for _ in DECODER_EVENT_COLUMNS) + ")"
)

SEGMENT_INSERT_SQL = (
    "INSERT INTO segment (capture_id, "
    + ", ".join(column for _key, column in SEGMENT_COLUMNS)
    + ") VALUES (?, " + ", ".join("?" for _ in SEGMENT_COLUMNS) + ")"
)


def segment_row(capture_id, segment):
    """The segment INSERT tuple for one JSON-shaped segment dict."""
    values = []
    for key, _column in SEGMENT_COLUMNS:
        value = segment.get(key)
        if key == "enabled":
            value = 1 if (value is None or value) else 0
        elif key in ("id", "startField", "endFieldExclusive") and value is not None:
            value = int(value)
        values.append(value)
    return (capture_id,) + tuple(values)


def write_segments(conn, capture_id, segments):
    """Replace the capture's segment rows with ``segments`` (JSON-shaped dicts).

    Delete-then-insert inside one transaction, ids preserved verbatim; the
    caller commits.
    """
    conn.execute("DELETE FROM segment WHERE capture_id = ?", (capture_id,))
    if segments:
        conn.executemany(
            SEGMENT_INSERT_SQL, [segment_row(capture_id, s) for s in segments]
        )

# Decoded-but-not-written lead-in before the resume point, so sync/AGC
# re-lock on real signal instead of the seam landing on the first appended
# field.
RESUME_LEAD_IN_FIELDS = 10


def db_system_value(system):
    """Map a videoParameters ``system`` string to the capture.system CHECK.

    The legacy JSON metadata spells PAL-M with a hyphen, but the SQLite
    schema CHECK only admits 'PAL_M'; writing the JSON spelling raises an
    IntegrityError mid-decode.
    """
    return "PAL_M" if system == "PAL-M" else system


class ResumeError(ValueError):
    """--resume cannot proceed; the message says why and what to do."""


@dataclass(frozen=True)
class ResumePlan:
    """What a resume run keeps and where it rejoins the input.

    ``field_count`` fields survive truncation; the loader seeks to
    ``seek_sample`` (the file_loc of field ``field_count - warmup_fields``)
    and the first ``warmup_fields`` decoded fields are discarded so the
    decoder re-locks before the first appended field.
    """

    field_count: int
    seek_sample: int
    warmup_fields: int
    last_kept_sample: int
    field_number_seed: int
    capture_id: int
    system: str
    decoder: str
    field_width: int | None
    field_height: int | None


def plan_resume(
    db_path,
    *,
    video_bytes,
    chroma_bytes,
    video_field_bytes,
    chroma_field_bytes,
    lead_in_fields=RESUME_LEAD_IN_FIELDS,
):
    """Compute the resume point from the db and the on-disk output sizes.

    The binding field count is the minimum of the committed db rows and
    what each output file actually holds (a kill can leave either side
    ahead: the db commits per field but the .tbc handles are never
    fsynced). A trailing FIRST field is dropped so the kept output ends on
    a complete frame. Raises :class:`ResumeError` when there is nothing
    safe to resume from.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            captures = conn.execute(
                "SELECT capture_id, system, decoder, field_width, field_height"
                " FROM capture"
            ).fetchall()
            if len(captures) != 1:
                raise ResumeError(
                    f"expected exactly one capture row in {db_path}, found "
                    f"{len(captures)} -- re-decode from scratch"
                )
            capture_id, system, decoder, field_width, field_height = captures[0]

            db_count = conn.execute(
                "SELECT COUNT(*) FROM field_record WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()[0]
            if db_count == 0:
                raise ResumeError(
                    f"{db_path} holds no committed fields -- nothing to "
                    "resume; re-decode from scratch"
                )

            field_count = min(db_count, video_bytes // video_field_bytes)
            if chroma_bytes is not None and chroma_field_bytes:
                field_count = min(field_count, chroma_bytes // chroma_field_bytes)
            if field_count <= 0:
                raise ResumeError(
                    "output files hold no complete field -- nothing to "
                    "resume; re-decode from scratch"
                )

            last_first = conn.execute(
                "SELECT is_first_field FROM field_record"
                " WHERE capture_id = ? AND field_id = ?",
                (capture_id, field_count - 1),
            ).fetchone()
            if last_first is not None and last_first[0]:
                # Ends on a FIRST field: its pair is missing. Drop it so the
                # kept output ends on a complete frame.
                field_count -= 1
            if field_count <= 0:
                raise ResumeError(
                    "only an unpaired first field survives -- nothing to "
                    "resume; re-decode from scratch"
                )

            warmup_fields = min(lead_in_fields, field_count)
            seek_row = conn.execute(
                "SELECT file_loc FROM field_record"
                " WHERE capture_id = ? AND field_id = ?",
                (capture_id, field_count - warmup_fields),
            ).fetchone()
            if seek_row is None or seek_row[0] is None:
                raise ResumeError(
                    f"field {field_count - warmup_fields} carries no "
                    "file_loc -- cannot locate the resume point; re-decode "
                    "from scratch"
                )
            last_row = conn.execute(
                "SELECT file_loc FROM field_record"
                " WHERE capture_id = ? AND field_id = ?",
                (capture_id, field_count - 1),
            ).fetchone()
            if last_row is None or last_row[0] is None:
                raise ResumeError(
                    f"field {field_count - 1} carries no file_loc -- cannot "
                    "place the resume seam; re-decode from scratch"
                )

            phase_rows = conn.execute(
                "SELECT field_id, field_phase_id FROM field_record"
                " WHERE capture_id = ? AND field_id IN (?, ?)",
                (capture_id, field_count - 2, field_count - 1),
            ).fetchall()
            phases = {row[0]: row[1] for row in phase_rows}
            field_number_seed = _infer_field_number_seed(
                field_count=field_count,
                warmup_fields=warmup_fields,
                phase_last=phases.get(field_count - 1),
                phase_prev=phases.get(field_count - 2),
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"not a readable .tbc.db: {exc}") from exc

    return ResumePlan(
        field_count=field_count,
        seek_sample=int(seek_row[0]),
        warmup_fields=warmup_fields,
        last_kept_sample=int(last_row[0]),
        field_number_seed=field_number_seed,
        capture_id=int(capture_id),
        system=system,
        decoder=decoder,
        field_width=field_width,
        field_height=field_height,
    )


def _infer_field_number_seed(*, field_count, warmup_fields, phase_last, phase_prev):
    """Reconstruct the decoder's internal field counter for the warm-up start.

    The chroma rotation cycle is anchored on the decoder's ``field_number``
    -- which is NOT the absolute field index whenever the original run hit a
    "readloc didn't advance" event. The stored ``fieldPhaseID`` sequence
    encodes the counter's position mod 4 (the decoder maps
    ``(isFirstField, (field_number // 2) % 2)`` to phases 1..4, and whether
    two adjacent fields share the ``(fn // 2) % 2`` bit says whether the
    counter was even or odd on the later one). Falls back to the absolute
    index when phases were not recorded.
    """
    base = field_count - warmup_fields
    if phase_last not in (1, 2, 3, 4) or phase_prev not in (1, 2, 3, 4):
        return base
    bit_last = 1 if phase_last in (2, 3) else 0
    bit_prev = 1 if phase_prev in (2, 3) else 0
    # Shared bit -> the counter was odd on the later field; differing bit ->
    # it had just started a new (fn // 2) pair, i.e. it was even.
    fn_last_mod4 = 2 * bit_last + (1 if bit_prev == bit_last else 0)
    return base + ((fn_last_mod4 - ((base + warmup_fields - 1) % 4)) % 4)


def apply_resume_truncation(
    db_path,
    plan,
    *,
    video_path,
    chroma_path,
    video_field_bytes,
    chroma_field_bytes,
):
    """Truncate the db and output files back to ``plan.field_count`` fields.

    Idempotent: running it again with a fresh plan is a no-op. Child rows
    are deleted explicitly (the writers never enable PRAGMA foreign_keys,
    so the schema's cascades do not fire). Schema v2 tables are handled
    when present (a v1 db has none): decoder events at or after the seam
    go, derived segments go (the library re-derives them), user segments
    starting at or after the seam go and one spanning it is clamped to it.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            tables = _existing_tables(conn)
            for table in _FIELD_CHILD_TABLES:
                if table not in tables:
                    continue
                conn.execute(
                    f"DELETE FROM {table} WHERE capture_id = ? AND field_id >= ?",
                    (plan.capture_id, plan.field_count),
                )
            conn.execute(
                "DELETE FROM field_record WHERE capture_id = ? AND field_id >= ?",
                (plan.capture_id, plan.field_count),
            )
            if "decoder_event" in tables:
                conn.execute(
                    "DELETE FROM decoder_event WHERE capture_id = ? AND field_id >= ?",
                    (plan.capture_id, plan.field_count),
                )
            if "segment" in tables:
                conn.execute(
                    "DELETE FROM segment WHERE capture_id = ? AND source = 'derived'",
                    (plan.capture_id,),
                )
                conn.execute(
                    "DELETE FROM segment WHERE capture_id = ? AND start_field >= ?",
                    (plan.capture_id, plan.field_count),
                )
                conn.execute(
                    "UPDATE segment SET end_field_exclusive = ?"
                    " WHERE capture_id = ? AND end_field_exclusive > ?",
                    (plan.field_count, plan.capture_id, plan.field_count),
                )
            conn.execute(
                "UPDATE capture SET number_of_sequential_fields = ?"
                " WHERE capture_id = ?",
                (plan.field_count, plan.capture_id),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"could not truncate {db_path}: {exc}") from exc

    os.truncate(str(video_path), plan.field_count * video_field_bytes)
    if chroma_path is not None and chroma_field_bytes:
        os.truncate(str(chroma_path), plan.field_count * chroma_field_bytes)


def minimal_fields_from_db(db_path, capture_id, field_count):
    """Rebuild minimal per-field dicts from ``field_record``.

    Fallback for a resumed decode whose legacy .tbc.json is missing or
    short: covers the core keys the decoder writes per field plus the
    picture metrics (schema v2). VITS/VBI/dropout details are not
    reconstructed -- the db stays the canonical record for those.
    """
    metric_columns = ", ".join(
        f"pm.{column}" for _key, column in PICTURE_METRIC_COLUMNS
    )
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            has_metrics = "picture_metrics" in _existing_tables(conn)
            join = (
                " LEFT JOIN picture_metrics pm ON pm.capture_id = fr.capture_id"
                " AND pm.field_id = fr.field_id"
                if has_metrics
                else ""
            )
            select_metrics = f", {metric_columns}" if has_metrics else ""
            rows = conn.execute(
                "SELECT fr.field_id, fr.is_first_field, fr.sync_conf, fr.disk_loc,"
                " fr.file_loc, fr.field_phase_id, fr.decode_faults, fr.audio_samples"
                f"{select_metrics}"
                f" FROM field_record fr{join}"
                " WHERE fr.capture_id = ? AND fr.field_id < ?"
                " ORDER BY fr.field_id",
                (capture_id, field_count),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"not a readable .tbc.db: {exc}") from exc

    fields = []
    for row in rows:
        (
            field_id,
            is_first_field,
            sync_conf,
            disk_loc,
            file_loc,
            field_phase_id,
            decode_faults,
            audio_samples,
        ) = row[:8]
        field = {
            "seqNo": field_id + 1,
            "isFirstField": bool(is_first_field),
            # The writer stores NULL for zero faults.
            "decodeFaults": 0 if decode_faults is None else decode_faults,
        }
        if sync_conf is not None:
            field["syncConf"] = sync_conf
        if disk_loc is not None:
            field["diskLoc"] = disk_loc
        if file_loc is not None:
            field["fileLoc"] = file_loc
        if field_phase_id is not None:
            field["fieldPhaseID"] = field_phase_id
        if audio_samples is not None:
            field["audioSamples"] = audio_samples
        if has_metrics:
            metrics = {
                key: value
                for (key, _column), value in zip(PICTURE_METRIC_COLUMNS, row[8:])
                if value is not None
            }
            if metrics:
                field["pictureMetrics"] = metrics
        fields.append(field)
    return fields


def load_events_from_db(db_path, capture_id):
    """Return the capture's decoder events as JSON-shaped dicts.

    Ordered by field then insertion (event_id); keys as the ``.tbc.json``
    ``decoderEvents`` projection spells them, NULL columns omitted. An empty
    list for a v1 db (no ``decoder_event`` table).
    """
    columns = ", ".join(column for _key, column in DECODER_EVENT_COLUMNS)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if "decoder_event" not in _existing_tables(conn):
                return []
            rows = conn.execute(
                f"SELECT {columns} FROM decoder_event WHERE capture_id = ?"
                " ORDER BY field_id, event_id",
                (capture_id,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"not a readable .tbc.db: {exc}") from exc

    events = []
    for row in rows:
        event = {}
        for (key, _column), value in zip(DECODER_EVENT_COLUMNS, row):
            if value is None:
                continue
            if key in ("field", "fileLoc", "rfDeltaSamples"):
                value = int(value)
            event[key] = value
        events.append(dict(sorted(event.items())))
    return events


def load_segments_from_db(db_path, capture_id):
    """Return the capture's segments as JSON-shaped dicts, in field order.

    Keys as the ``.tbc.json`` ``segments`` projection spells them (``id``,
    ``startField``, ``endFieldExclusive``, ``kind``, ``source``, ``enabled``
    plus the optional text fields when set). An empty list for a v1 db.
    """
    columns = ", ".join(column for _key, column in SEGMENT_COLUMNS)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if "segment" not in _existing_tables(conn):
                return []
            rows = conn.execute(
                f"SELECT {columns} FROM segment WHERE capture_id = ?"
                " ORDER BY start_field, segment_id",
                (capture_id,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"not a readable .tbc.db: {exc}") from exc

    segments = []
    for row in rows:
        segment = {}
        for (key, _column), value in zip(SEGMENT_COLUMNS, row):
            if key == "enabled":
                value = bool(value)
            elif value is None:
                continue
            elif key in ("id", "startField", "endFieldExclusive"):
                value = int(value)
            segment[key] = value
        segments.append(dict(sorted(segment.items())))
    return segments


def load_json_fields(json_path, field_count):
    """Return the first ``field_count`` fields from a legacy .tbc.json.

    None when the file is missing, unparseable, or holds fewer fields --
    the caller falls back to rebuilding minimal field dicts from the db.
    """
    try:
        with open(str(json_path), encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    fields = payload.get("fields")
    if not isinstance(fields, list) or len(fields) < field_count:
        return None
    return fields[:field_count]
