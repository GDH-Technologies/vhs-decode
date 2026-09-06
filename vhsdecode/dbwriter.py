import sqlite3
import os
from sqlite3 import Connection

import lddecode.tbc_db as tbc_db


class DBWriter:
    """Class for unifying sqlite writing between cvbs and vhs."""

    def __init__(self, fname_out, resume=False):
        # A fresh decode replaces any prior db; a resumed one continues it
        # (main has already truncated it to the reconciled field count).
        if not resume and os.path.exists(fname_out + ".tbc.db"):
            os.unlink(fname_out + ".tbc.db")
        self._db_connection = sqlite3.connect(fname_out + ".tbc.db")
        if resume:
            # The interrupted run may predate schema v2: add what is missing
            # so the per-field inserts below have their tables.
            tbc_db.migrate_schema(self._db_connection)

    @property
    def db_connection(self) -> Connection:
        return self._db_connection

    def write_field(self, field_data: dict, capture_id: int, do_dod):
        field_id = field_data["seqNo"] - 1

        decodeFaults = (
            None
            if field_data.get("decodeFaults") == 0
            else field_data.get("decodeFaults")
        )

        # Insert parent record into 'field_record'
        # We cast booleans to int because of the CHECK (val IN (0,1)) constraint
        self._db_connection.execute(
            """
            INSERT INTO field_record (
                capture_id, field_id, is_first_field, sync_conf, disk_loc,
                file_loc, field_phase_id, decode_faults,
                pad
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                capture_id,
                field_id,
                int(field_data["isFirstField"]),
                field_data["syncConf"],
                field_data["diskLoc"],
                field_data["fileLoc"],
                field_data["fieldPhaseID"],
                decodeFaults,
                0,
            ),
        )

        w_snr = field_data["vitsMetrics"].get("wSNR", 0)
        b_psnr = field_data["vitsMetrics"].get("bPSNR", 0)

        self._db_connection.execute(
            """
            INSERT INTO vits_metrics (
                capture_id, field_id, w_snr, b_psnr
            ) VALUES (?, ?, ?, ?)""",
            (capture_id, field_id, w_snr, b_psnr),
        )

        # Per-field picture metrics (schema v2); absent members are NULL,
        # and a field without any finite metric gets no row.
        metrics = field_data.get("pictureMetrics")
        if metrics:
            self._db_connection.execute(
                tbc_db.PICTURE_METRICS_INSERT_SQL,
                tbc_db.picture_metrics_row(capture_id, field_id, metrics),
            )

        # Insert VBI data if present
        vbi_data = field_data.get("vbi", {}).get("vbiData", [])
        if vbi_data:
            # Ensure we have exactly 3 values for the vbi0, vbi1, vbi2 columns
            # This pads with 0 if fewer than 3 are found
            vbi_row = (vbi_data + [0, 0, 0])[:3]

            self._db_connection.execute(
                """
                INSERT INTO vbi (
                    capture_id, field_id, vbi0, vbi1, vbi2
                ) VALUES (?, ?, ?, ?, ?)""",
                (capture_id, field_id, vbi_row[0], vbi_row[1], vbi_row[2]),
            )

        # Insert dropouts (if any) into 'drop_outs'
        if do_dod and field_data.get("dropOuts"):
            dropout_lines = field_data["dropOuts"]["fieldLine"]
            dropout_starts = field_data["dropOuts"]["startx"]
            dropout_ends = field_data["dropOuts"]["endx"]

            # Use executemany for cleaner/faster insertion of multiple rows
            dropout_data = [
                (capture_id, field_id, line, start, end)
                for line, start, end in zip(dropout_lines, dropout_starts, dropout_ends)
            ]

            self._db_connection.executemany(
                """
                INSERT INTO drop_outs (
                    capture_id, field_id, field_line, startx, endx
                ) VALUES (?, ?, ?, ?, ?)""",
                dropout_data,
            )
        # Skip committing for now it's called again afterwards in build_sqlite_metadata
        # db_connection.commit()

    def write_events(self, rows):
        """Append decoder_event rows (``DecoderEventLog.to_db_rows`` tuples).

        Called before the per-field commit so an event lands in the same
        transaction as the field it precedes; the caller commits.
        """
        if rows:
            self._db_connection.executemany(tbc_db.DECODER_EVENT_INSERT_SQL, rows)

    def write_segments(self, capture_id, segments):
        """Replace the capture's segment rows (used when --resume re-seeds them)."""
        tbc_db.write_segments(self._db_connection, capture_id, segments)
        self._db_connection.commit()
