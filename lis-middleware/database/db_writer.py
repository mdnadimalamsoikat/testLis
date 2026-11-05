"""
Writes parsed results into Oracle.

Two tables are used, both created by ``database/schema.sql``:

* ``lis_raw_messages`` — every raw payload, verbatim, with a processing status.
  Keeping it means a parser bug never loses data: you can re-run the parse
  later from this table alone.
* ``lab_results`` — one row per test result, unique on
  (sample_id, test_code, analyzer_name) so analyzer re-transmissions are
  ignored instead of duplicated.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .db_connection import OracleDB

from database.control import _lit

logger = logging.getLogger("LIS.DBW")


class DBWriter:
    def __init__(self, db: OracleDB, schema: Optional[str] = None):
        self.db = db
        self.schema = (schema or db.schema).upper()

    # ------------------------------------------------------------------ public
    def log_raw_message(
        self,
        analyzer_name: str,
        protocol: str,
        raw_message: str,
        peer: Optional[str] = None,
    ) -> Optional[int]:
        """Insert the raw payload; returns the generated message id."""
        sql = f"""
            INSERT INTO {self.schema}.lis_raw_messages
                (analyzer_name, protocol, peer, raw_message, received_at, status)
            VALUES
                (:analyzer_name, :protocol, :peer, :raw_message, SYSTIMESTAMP, 'RECEIVED')
            RETURNING message_id INTO :message_id
        """
        message_id_var = None
        try:
            with self.db.cursor() as cur:
                # OUT binds go inside the dict — passing both a positional dict
                # and a keyword argument raises DPY-2005 and the raw message is
                # lost (which is the one thing this layer must never do).
                message_id_var = cur.var(int)
                cur.execute(
                    sql,
                    {
                        "analyzer_name": analyzer_name,
                        "protocol": protocol,
                        "peer": peer,
                        "raw_message": raw_message,
                        "message_id": message_id_var,
                    },
                )
                value = message_id_var.getvalue()
                return int(value[0]) if isinstance(value, list) else int(value)
        except Exception as exc:  # never let logging break the pipeline
            logger.error("could not store raw message from %s: %s", analyzer_name, exc)
            return None

    def mark_message(self, message_id: Optional[int], status: str, note: str = "") -> None:
        if message_id is None:
            return
        sql = f"""
            UPDATE {self.schema}.lis_raw_messages
               SET status = :status, processed_at = SYSTIMESTAMP, note = :note
             WHERE message_id = :message_id
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql, {"status": status, "note": note[:500], "message_id": message_id})
        except Exception as exc:
            logger.error("could not update message %s: %s", message_id, exc)

    def is_duplicate(self, result: Dict[str, Any]) -> bool:
        sql = f"""
            SELECT COUNT(*)
              FROM {self.schema}.lab_results
             WHERE sample_id     = :sample_id
               AND test_code     = :test_code
               AND analyzer_name = :analyzer_name
               AND result_value  = :result_value
        """
        with self.db.cursor() as cur:
            cur.execute(
                sql,
                {
                    "sample_id": result["sample_id"],
                    "test_code": result["test_code"],
                    "analyzer_name": result["analyzer_name"],
                    "result_value": result.get("result_value"),
                },
            )
            (count,) = cur.fetchone()
        return count > 0

    def insert_results(self, results: Iterable[Dict[str, Any]], raw_message: str = "") -> int:
        """Insert results, skipping duplicates. Returns how many were stored."""
        stored = 0
        for result in results:
            try:
                if self.is_duplicate(result):
                    logger.warning(
                        "duplicate skipped: %s / %s",
                        result["sample_id"], result["test_code"],
                    )
                    continue
            except Exception as exc:
                logger.error("duplicate check failed for %s: %s", result.get("test_code"), exc)
                continue

            sql = f"""
                INSERT INTO {self.schema}.lab_results (
                    sample_id, patient_id, patient_name, test_code,
                    result_value, result_text, result_unit, ref_range,
                    abnormal_flag, is_abnormal, is_critical, analyzer_name,
                    protocol, result_datetime, received_at, raw_message
                ) VALUES (
                    :sample_id, :patient_id, :patient_name, :test_code,
                    :result_value, :result_text, :result_unit, :ref_range,
                    :abnormal_flag, :is_abnormal, :is_critical, :analyzer_name,
                    :protocol, TO_TIMESTAMP(:result_datetime, 'YYYY-MM-DD HH24:MI:SS'),
                    SYSTIMESTAMP, :raw_message
                )
            """
            payload = {
                "sample_id": result.get("sample_id"),
                "patient_id": result.get("patient_id"),
                "patient_name": result.get("patient_name"),
                "test_code": result.get("test_code"),
                "result_value": result.get("result_value"),
                "result_text": result.get("result_text"),
                "result_unit": result.get("result_unit"),
                "ref_range": result.get("ref_range"),
                "abnormal_flag": result.get("abnormal_flag"),
                "is_abnormal": result.get("is_abnormal", "N"),
                "is_critical": result.get("is_critical", "N"),
                "analyzer_name": result.get("analyzer_name"),
                "protocol": result.get("protocol"),
                "result_datetime": result.get("result_datetime")
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "raw_message": (raw_message or result.get("raw_message") or "")[:4000],
            }
            try:
                with self.db.cursor() as cur:
                    cur.execute(sql, payload)
                stored += 1
                logger.info(
                    "inserted %s / %s = %s %s",
                    payload["sample_id"], payload["test_code"],
                    payload["result_text"], payload["result_unit"],
                )
            except Exception as exc:
                logger.error("insert failed for %s: %s", result.get("test_code"), exc)
        return stored

    # --------------------------------------------------------------- helpers
    def stats(self) -> Dict[str, Any]:
        """Row counts used by the status endpoint / APEX dashboard."""
        sql = f"""
            SELECT COUNT(*),
                   SUM(CASE WHEN TRUNC(received_at) = TRUNC(SYSDATE) THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_abnormal = 'Y' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_critical = 'Y' THEN 1 ELSE 0 END)
              FROM {self.schema}.lab_results
        """
        with self.db.cursor() as cur:
            cur.execute(sql)
            total, today, abnormal, critical = cur.fetchone()
        return {
            "total": total or 0,
            "today": today or 0,
            "abnormal": abnormal or 0,
            "critical": critical or 0,
        }

    def insert_audit(self, event: str, analyzer: str, detail: str = "") -> None:
        # values are inlined: `:event`, `:detail` are reserved-word shaped risks
        # (see database/control.py — ORA-01745 taught us this the hard way)
        sql = f"""
            INSERT INTO {self.schema}.lis_audit_log
                (event_type, analyzer_name, detail, created_at)
            VALUES ({_lit(event, 50)}, {_lit(analyzer, 100)},
                    {_lit(json.dumps(detail), 1000)}, SYSTIMESTAMP)
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
        except Exception as exc:
            logger.error("audit insert failed: %s", exc)
