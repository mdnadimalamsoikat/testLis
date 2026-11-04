"""
In-memory stand-in for :class:`database.db_writer.DBWriter`.

Same method names as the Oracle writer, so the whole pipeline (listener →
parser → validator → writer) runs unchanged without a database. This is what
the browser demo uses, and it is also handy for tests and for a first run on
a machine where Oracle is not set up yet.
"""

from __future__ import annotations

import itertools
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


class MemoryStore:
    def __init__(self, schema: str = "DEMO") -> None:
        self.schema = schema
        self._lock = threading.RLock()
        self._message_ids = itertools.count(1)
        self.messages: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []
        self.audit: List[Dict[str, Any]] = []

    # -------------------------------------------------------- DBWriter parity
    def log_raw_message(self, analyzer_name: str, protocol: str, raw_message: str,
                        peer: Optional[str] = None) -> int:
        with self._lock:
            message_id = next(self._message_ids)
            self.messages.append({
                "message_id": message_id,
                "analyzer_name": analyzer_name,
                "protocol": protocol,
                "peer": peer,
                "raw_message": raw_message,
                "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "RECEIVED",
                "note": "",
            })
            return message_id

    def mark_message(self, message_id: Optional[int], status: str, note: str = "") -> None:
        if message_id is None:
            return
        with self._lock:
            for msg in self.messages:
                if msg["message_id"] == message_id:
                    msg["status"] = status
                    msg["note"] = note[:500]
                    msg["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break

    def is_duplicate(self, result: Dict[str, Any]) -> bool:
        with self._lock:
            return any(
                row["sample_id"] == result.get("sample_id")
                and row["test_code"] == result.get("test_code")
                and row["analyzer_name"] == result.get("analyzer_name")
                and row.get("result_value") == result.get("result_value")
                for row in self.results
            )

    def insert_results(self, results: Iterable[Dict[str, Any]], raw_message: str = "") -> int:
        stored = 0
        for result in results:
            if self.is_duplicate(result):
                continue
            with self._lock:
                row = dict(result)
                row["result_id"] = len(self.results) + 1
                row["received_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row["raw_message"] = (raw_message or result.get("raw_message") or "")[:4000]
                self.results.append(row)
                stored += 1
        return stored

    def insert_audit(self, event: str, analyzer: str, detail: str = "") -> None:
        with self._lock:
            self.audit.append({
                "event": event,
                "analyzer": analyzer,
                "detail": detail,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            return {
                "total": len(self.results),
                "today": sum(1 for r in self.results if r.get("received_at", "").startswith(today)),
                "abnormal": sum(1 for r in self.results if r.get("is_abnormal") == "Y"),
                "critical": sum(1 for r in self.results if r.get("is_critical") == "Y"),
                "messages": len(self.messages),
            }

    # ------------------------------------------------------------- demo extras
    def list_results(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self.results))[:limit]

    def list_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self.messages))[:limit]

    def samples(self) -> List[Dict[str, Any]]:
        """Group results per sample — the shape an APEX report needs."""
        grouped: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for row in self.results:
                key = row["sample_id"]
                entry = grouped.setdefault(key, {
                    "sample_id": key,
                    "patient_name": row.get("patient_name", ""),
                    "patient_id": row.get("patient_id", ""),
                    "analyzer_name": row.get("analyzer_name", ""),
                    "received_at": row.get("received_at", ""),
                    "tests": [],
                })
                entry["tests"].append({
                    "test_code": row["test_code"],
                    "result_value": row.get("result_value"),
                    "result_text": row.get("result_text"),
                    "result_unit": row.get("result_unit"),
                    "ref_range": row.get("ref_range"),
                    "abnormal_flag": row.get("abnormal_flag"),
                })
        rows = list(grouped.values())
        rows.sort(key=lambda r: r["received_at"], reverse=True)
        return rows
