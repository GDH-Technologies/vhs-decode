"""The decoder's event log: immutable facts recorded while decoding.

stdlib only; shared by the ld-decode, vhs-decode and cvbs-decode decoders,
by --resume (which re-seeds it from the .tbc.db) and by the tests.

Each event names the first written field at/after it (``field``, the
0-based output index), the RF location it happened at (``fileLoc``, in
the same samples as the per-field ``fileLoc``) and, when it is a jump,
the RF distance in samples (``rfDeltaSamples``) and in nominal fields
(``rfDeltaFields`` = samples / (rf.freq_hz / (2 x FPS)), three decimals).
Kinds are a plain vocabulary -- no CHECK in the db, since tbc-tools writes
reconstructed events too:

    no_sync_pulses   readfield found no sync pulses and jumped ahead
    no_field_start   readfield found pulses but no field start and jumped
    skipped_field    two same-parity fields in a row; field order flipped
    duplicate_field  ...; the previous field was written again
                     (``field`` is the duplicated copy)
    dropped_field    ...; the field was not written
                     (``field`` is the next written one)
    resume_seam      --resume rejoined the input here
    redo             the field was decoded again after an AGC / MTF /
                     needrerun adjustment (``fileLoc`` = the rewind target)

Everything is a plain dict / int / float / str so the log serialises with
``json.dumps(allow_nan=False)`` and inserts into sqlite unchanged.
"""
from __future__ import annotations

import json
import numbers

SOURCE_DECODER = "decoder"

# Kinds a consumer treats as a recording seam (with a reconstructed gap).
SEAM_KINDS = frozenset(
    {"no_sync_pulses", "no_field_start", "dropped_field", "resume_seam"}
)


def _as_int(value):
    """int() for ints (numpy included); round-to-nearest for floats."""
    if isinstance(value, numbers.Integral):
        return int(value)
    return int(round(float(value)))


class DecoderEventLog:
    """Append-only log of decoder events with an "unsent" cursor for the db."""

    def __init__(self, rf_samples_per_field=None):
        # The exact float rf.freq_hz / (2 * FPS): the nominal RF length of
        # one field, never the rounded bytes_per_field.
        self.rf_samples_per_field = rf_samples_per_field
        self._events = []
        self._sent = 0

    def set_rate(self, freq_hz, fps):
        """Set the nominal samples per field from the RF rate and frame rate."""
        self.rf_samples_per_field = float(freq_hz) / (2.0 * float(fps))

    def __len__(self):
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def __getitem__(self, index):
        return self._events[index]

    def append(self, kind, field, *, file_loc=None, rf_delta_samples=None, detail=None):
        """Record one event; returns the stored dict (keys alphabetical)."""
        event = {
            "field": _as_int(field),
            "kind": str(kind),
            "source": SOURCE_DECODER,
        }
        if file_loc is not None:
            event["fileLoc"] = _as_int(file_loc)
        if rf_delta_samples is not None:
            delta = _as_int(rf_delta_samples)
            event["rfDeltaSamples"] = delta
            if self.rf_samples_per_field:
                event["rfDeltaFields"] = round(delta / self.rf_samples_per_field, 3)
        if detail is not None:
            event["detailJson"] = json.dumps(detail, separators=(",", ":"), sort_keys=True)
        event = dict(sorted(event.items()))
        self._events.append(event)
        return event

    def to_json(self):
        """The ``decoderEvents`` JSON projection: a list of plain dicts."""
        return [dict(event) for event in self._events]

    @staticmethod
    def db_row(capture_id, event):
        """One decoder_event INSERT tuple (see tbc_db.DECODER_EVENT_COLUMNS)."""
        return (
            capture_id,
            event["field"],
            event["kind"],
            event.get("fileLoc"),
            event.get("rfDeltaSamples"),
            event.get("rfDeltaFields"),
            event.get("source", SOURCE_DECODER),
            event.get("detailJson"),
        )

    def to_db_rows(self, capture_id, events=None):
        """INSERT tuples for ``events`` (default: every event in the log)."""
        if events is None:
            events = self._events
        return [self.db_row(capture_id, event) for event in events]

    def unsent(self):
        """Events appended since the last call (for incremental db inserts)."""
        new = self._events[self._sent :]
        self._sent = len(self._events)
        return new

    def seed(self, events):
        """Preload events recovered from the db on --resume; they count as sent."""
        for event in events:
            self._events.append(dict(sorted(event.items())))
        self._sent = len(self._events)
