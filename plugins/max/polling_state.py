"""Persistent MAX Long Polling marker store."""

from __future__ import annotations

import sqlite3
import threading
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional


def _event_key(update: Mapping[str, Any]) -> str:
    update_id = update.get("update_id")
    if update_id is not None:
        return f"update:{update_id}"
    message = update.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return f"message:{body['mid']}"
    callback = update.get("callback")
    if isinstance(callback, Mapping) and callback.get("callback_id"):
        return f"callback:{callback['callback_id']}"
    encoded = json.dumps(update, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "payload:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PollingMarkerStore:
    """Store the last acknowledged MAX marker across gateway restarts."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS max_polling_state "
                "(name TEXT PRIMARY KEY, marker INTEGER)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS max_polling_inbox (
                    event_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    processed_at REAL,
                    last_error TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS max_polling_inbox_status_idx "
                "ON max_polling_inbox(status, created_at)"
            )
            self._conn.commit()

    def get(self) -> Optional[int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT marker FROM max_polling_state WHERE name = 'updates'"
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def set(self, marker: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO max_polling_state(name, marker) VALUES ('updates', ?) "
                "ON CONFLICT(name) DO UPDATE SET marker = excluded.marker",
                (int(marker),),
            )
            self._conn.commit()

    def accept_batch(
        self,
        updates: list[Mapping[str, Any]],
        marker: Optional[int],
    ) -> None:
        """Persist updates and the next marker in one SQLite transaction."""

        with self._lock:
            self._conn.execute("BEGIN")
            try:
                now = time.time()
                for update in updates:
                    if not isinstance(update, Mapping):
                        continue
                    key = _event_key(update)
                    payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
                    self._conn.execute(
                        "INSERT OR IGNORE INTO max_polling_inbox "
                        "(event_key, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                        (key, payload, now),
                    )
                if marker is not None:
                    self._conn.execute(
                        "INSERT INTO max_polling_state(name, marker) VALUES ('updates', ?) "
                        "ON CONFLICT(name) DO UPDATE SET marker = excluded.marker",
                        (int(marker),),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def claim_next(self) -> Optional[Mapping[str, Any]]:
        """Claim one pending update; in-flight work is never auto-replayed."""

        with self._lock:
            row = self._conn.execute(
                "SELECT event_key, payload FROM max_polling_inbox "
                "WHERE status = 'pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'processing', attempts = attempts + 1, "
                "started_at = ? WHERE event_key = ? AND status = 'pending'",
                (time.time(), row[0]),
            )
            self._conn.commit()
        return json.loads(row[1])

    def mark_processed(self, update: Mapping[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'processed', processed_at = ?, "
                "last_error = NULL WHERE event_key = ?",
                (time.time(), _event_key(update)),
            )
            self._conn.commit()

    def mark_failed(self, update: Mapping[str, Any], error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'failed', last_error = ? "
                "WHERE event_key = ?",
                (str(error)[:1000], _event_key(update)),
            )
            self._conn.commit()

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM max_polling_inbox GROUP BY status"
            ).fetchall()
            last_error = self._conn.execute(
                "SELECT last_error FROM max_polling_inbox "
                "WHERE last_error IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        summary = {str(status): int(count) for status, count in rows}
        for status in ("pending", "processing", "processed", "failed"):
            summary.setdefault(status, 0)
        summary["last_error"] = str(last_error[0]) if last_error else None
        summary["marker"] = self.get()
        return summary

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class MaxTargetStore:
    """Persist whether a target is a MAX user dialog or group chat."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS max_targets "
                "(chat_id TEXT PRIMARY KEY, target_type TEXT NOT NULL)"
            )
            self._conn.commit()

    def get(self, chat_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT target_type FROM max_targets WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return str(row[0]) if row else None

    def set(self, chat_id: str, target_type: str) -> None:
        if target_type not in {"user", "chat"}:
            raise ValueError(f"unsupported MAX target type: {target_type}")
        with self._lock:
            self._conn.execute(
                "INSERT INTO max_targets(chat_id, target_type) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET target_type = excluded.target_type",
                (str(chat_id), target_type),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
