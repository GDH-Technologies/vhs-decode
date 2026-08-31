"""Pure helpers for the .tbc.db SQLite metadata sidecar (schema user_version 1).

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

# The canonical DDL (decode-orc mirrors it column-for-column). One home for
# the schema so the writers, --resume and the tests never drift apart.
SCHEMA_SQL = """\
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

# Tables keyed (capture_id, field_id) hanging off field_record. Truncation
# deletes from them explicitly: the writers never enable PRAGMA
# foreign_keys, so ON DELETE CASCADE cannot be relied on.
_FIELD_CHILD_TABLES = ("vits_metrics", "vbi", "drop_outs", "vitc", "closed_caption")

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
    so the schema's cascades do not fire).
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            for table in _FIELD_CHILD_TABLES:
                conn.execute(
                    f"DELETE FROM {table} WHERE capture_id = ? AND field_id >= ?",
                    (plan.capture_id, plan.field_count),
                )
            conn.execute(
                "DELETE FROM field_record WHERE capture_id = ? AND field_id >= ?",
                (plan.capture_id, plan.field_count),
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
    short: covers the core keys the decoder writes per field. VITS/VBI/
    dropout details are not reconstructed -- the db stays the canonical
    record for those.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT field_id, is_first_field, sync_conf, disk_loc,"
                " file_loc, field_phase_id, decode_faults, audio_samples"
                " FROM field_record WHERE capture_id = ? AND field_id < ?"
                " ORDER BY field_id",
                (capture_id, field_count),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResumeError(f"not a readable .tbc.db: {exc}") from exc

    fields = []
    for (
        field_id,
        is_first_field,
        sync_conf,
        disk_loc,
        file_loc,
        field_phase_id,
        decode_faults,
        audio_samples,
    ) in rows:
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
        fields.append(field)
    return fields


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
