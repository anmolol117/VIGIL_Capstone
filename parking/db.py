"""Supabase persistence for the parking pipeline.

Two rules shape this module:

1. Never write per frame. 30 fps across several lots is millions of rows a day
   that nobody reads. Transitions go to slot_events, periodic aggregates go to
   parking_snapshots, and per-track rows are upserted - the same shape the
   existing speeding_events table already uses.
2. Never lose a row to a flaky link. Drone ground stations sit on campus wifi.
   Failed batches spill to a JSONL file on disk and drain on the next success.

Writes use the service_role key, which bypasses RLS. That matches every other
table in this project - none of them define policies.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BUCKET = "parking"


def iso(ts: float) -> str:
    """Unix seconds -> ISO 8601 UTC, the format postgres timestamptz expects."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def load_env(path: str | Path = Path(__file__).with_name(".env")) -> None:
    """Fill os.environ from a local KEY=VALUE .env file; real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def connect(url: str | None = None, key: str | None = None):
    from supabase import create_client

    load_env()
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (service_role key - "
            "RLS is enabled with no write policies, so the publishable key can write nothing)"
        )
    return create_client(url, key)


def load_lot(client, lot_code: str) -> dict:
    """Fetch a lot and its active bays, polygons as float32 arrays."""
    lots = client.table("parking_lots").select("*").eq("code", lot_code).execute().data
    if not lots:
        raise RuntimeError(f"no parking_lots row with code {lot_code!r} - run annotate.py first")
    lot = lots[0]

    slots = (
        client.table("parking_slots")
        .select("id, code, polygon, slot_type")
        .eq("lot_id", lot["id"])
        .eq("active", True)
        .execute()
        .data
    )
    if not slots:
        raise RuntimeError(f"lot {lot_code!r} has no active bays - run annotate.py first")

    lot["slots"] = [
        {**s, "polygon": np.asarray(s["polygon"], dtype=np.float32)} for s in slots
    ]
    lot["boundary"] = np.asarray(lot.get("boundary") or [], dtype=np.float32)
    return lot


def start_session(client, lot_id: str, *, drone_name, source, model_name) -> str:
    row = {
        "lot_id": lot_id,
        "drone_name": drone_name,
        "source": source,
        "model_name": model_name,
    }
    return client.table("drone_sessions").insert(row).execute().data[0]["id"]


def end_session(client, session_id: str) -> None:
    client.table("drone_sessions").update(
        {"ended_at": iso(datetime.now(tz=timezone.utc).timestamp())}
    ).eq("id", session_id).execute()


def upload_frame(client, path: str, jpg: bytes) -> str | None:
    """Store an evidence crop and return its public URL.

    Evidence is nice to have - a failure here must never take down the pipeline.
    """
    try:
        client.storage.from_(BUCKET).upload(
            path, jpg, {"content-type": "image/jpeg", "upsert": "true"}
        )
        return client.storage.from_(BUCKET).get_public_url(path)
    except Exception as exc:  # noqa: BLE001 - evidence is optional, data is not
        print(f"[db] evidence upload failed ({exc}); continuing without frame_url")
        return None


class Writer:
    """Buffers rows and flushes them in bulk, spilling to disk on failure."""

    def __init__(self, client=None, *, queue_path: str = ".write_queue.jsonl", dry_run: bool = False):
        self.client = client
        self.dry_run = dry_run or client is None
        self.queue_path = Path(queue_path)
        self._buffer: list[tuple[str, str | None, dict]] = []
        self.written = 0
        self.queued = 0

    def add(self, table: str, row: dict, *, on_conflict: str | None = None) -> None:
        self._buffer.append((table, on_conflict, row))

    def flush(self) -> bool:
        """Send everything buffered. Returns False if anything had to be queued."""
        if not self._buffer:
            return self._drain()

        batches: dict[tuple[str, str | None], list[dict]] = {}
        for table, on_conflict, row in self._buffer:
            batches.setdefault((table, on_conflict), []).append(row)
        self._buffer.clear()

        if self.dry_run:
            self.written += sum(len(r) for r in batches.values())
            return True

        ok = self._drain()
        for (table, on_conflict), rows in batches.items():
            if not self._send(table, on_conflict, rows):
                self._spill(table, on_conflict, rows)
                ok = False
        return ok

    def _send(self, table: str, on_conflict: str | None, rows: list[dict]) -> bool:
        try:
            query = self.client.table(table)
            if on_conflict:
                query.upsert(rows, on_conflict=on_conflict).execute()
            else:
                query.insert(rows).execute()
            self.written += len(rows)
            return True
        except Exception as exc:  # noqa: BLE001 - any failure means queue, not lose
            print(f"[db] {table} write failed ({exc}); queued {len(rows)} row(s) to disk")
            return False

    def _spill(self, table, on_conflict, rows) -> None:
        with self.queue_path.open("a") as fh:
            fh.write(json.dumps({"table": table, "on_conflict": on_conflict, "rows": rows}) + "\n")
        self.queued += len(rows)

    def _drain(self) -> bool:
        """Replay queued batches. Stops at the first failure to preserve order."""
        if self.dry_run or not self.queue_path.exists():
            return True

        pending = [json.loads(line) for line in self.queue_path.read_text().splitlines() if line]
        if not pending:
            self.queue_path.unlink(missing_ok=True)
            return True

        for i, batch in enumerate(pending):
            if not self._send(batch["table"], batch["on_conflict"], batch["rows"]):
                remaining = pending[i:]
                self.queue_path.write_text("".join(json.dumps(b) + "\n" for b in remaining))
                return False

        self.queue_path.unlink(missing_ok=True)
        self.queued = 0
        print(f"[db] drained {len(pending)} queued batch(es)")
        return True

    def close(self) -> None:
        self.flush()
        if self.queued:
            print(f"[db] {self.queued} row(s) still queued in {self.queue_path}; rerun to drain")
