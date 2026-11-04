"""
The pipeline: raw payload in, validated rows in the database out.

This is deliberately the only place that knows the order of operations:

    1. store the raw message        (so nothing is ever lost)
    2. sniff / confirm the protocol
    3. parse                        (ASTM or HL7)
    4. validate + normalise         (validator)
    5. insert, skipping duplicates  (writer)
    6. mark the raw message         (PARSED / DUPLICATE / ERROR)
    7. build the ACK

Both the TCP/Serial listeners and the browser demo call
``IngestEngine.handle_payload`` — one code path, so the demo really does test
the same logic that will run in the lab.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.ack_handler import ACKHandler
from core.parser_factory import parse_message, sniff_protocol
from core.validator import ResultValidator

logger = logging.getLogger("LIS.ENGINE")


class IngestEngine:
    """Shared sink used by every listener."""

    def __init__(
        self,
        store: Any,
        validator: ResultValidator,
        ack_handler: Optional[ACKHandler] = None,
        test_code_map: Optional[Dict[str, str]] = None,
        known_test_codes: Optional[set[str]] = None,
        ignore_sample_prefixes: Optional[List[str]] = None,
    ):
        self.store = store
        self.validator = validator
        self.ack = ack_handler or ACKHandler("ASTM")
        self.test_code_map = test_code_map or {}
        self.known_test_codes = known_test_codes
        self.ignore_sample_prefixes = ignore_sample_prefixes
        # optional per-analyzer overrides, filled by service.py
        self.analyzer_code_maps: Dict[str, Dict[str, str]] = {}
        self.history: List[Dict[str, Any]] = []
        self.counters = {"messages": 0, "results": 0, "duplicates": 0, "errors": 0}

    # ------------------------------------------------------------------ public
    async def handle_payload(self, raw: str, analyzer: Dict[str, Any], peer: str = "n/a") -> Dict[str, Any]:
        return self.process(raw, analyzer, peer=peer)

    def process(self, raw: str, analyzer: Dict[str, Any], peer: str = "n/a") -> Dict[str, Any]:
        """Synchronous core — easy to unit test, easy to call from a thread."""
        analyzer_name = analyzer.get("name", "UNKNOWN")
        configured = str(analyzer.get("protocol", "AUTO")).upper()
        started = datetime.now()

        message_id = self.store.log_raw_message(analyzer_name, configured, raw, peer)
        self.counters["messages"] += 1

        entry: Dict[str, Any] = {
            "message_id": message_id,
            "analyzer_name": analyzer_name,
            "peer": peer,
            "received_at": started.strftime("%Y-%m-%d %H:%M:%S"),
            "configured_protocol": configured,
            "raw": raw,
        }

        try:
            protocol = sniff_protocol(raw)
            entry["detected_protocol"] = protocol

            code_map = dict(self.test_code_map)
            code_map.update(self.analyzer_code_maps.get(analyzer_name, {}))
            parsed = parse_message(
                raw,
                protocol=configured if configured != "AUTO" else "AUTO",
                analyzer_name=analyzer_name,
                test_code_map=code_map,
            )
            entry["parsed_count"] = len(parsed)

            if not parsed:
                entry.update(status="ERROR", note="no results parsed", stored=0)
                self.counters["errors"] += 1
                self.store.mark_message(message_id, "ERROR", "no results parsed")
                self._record(entry)
                return self._response(False, entry, "no results parsed")

            validated = self.validator.validate(parsed)
            entry["valid_count"] = len(validated)
            if not validated and getattr(self.validator, "last_ignored", None):
                sample = getattr(self.validator, "last_ignored")[0].split("(")[-1].rstrip(")")
                note = f"control sample {sample} ignored (not patient data)"
                entry.update(status="IGNORED", note=note, stored=0)
                self.store.mark_message(message_id, "IGNORED", note)
                self.store.insert_audit("IGNORED", analyzer_name, note)
                self._record(entry)
                return self._response(True, entry, note)

            if not validated:
                entry.update(status="ERROR", note="all results failed validation", stored=0)
                self.counters["errors"] += 1
                self.store.mark_message(message_id, "ERROR", "validation failed")
                self._record(entry)
                return self._response(False, entry, "validation failed")

            stored = self.store.insert_results(validated, raw)
            duplicates = len(validated) - stored
            self.counters["results"] += stored
            self.counters["duplicates"] += duplicates

            status = "DUPLICATE" if stored == 0 else "PARSED"
            note = f"{stored} stored" + (f", {duplicates} duplicate(s)" if duplicates else "")
            entry.update(status=status, note=note, stored=stored, duplicates=duplicates,
                         results=validated)
            self.store.mark_message(message_id, status, note)
            self.store.insert_audit("INGEST", analyzer_name, note)
            self._record(entry)
            return self._response(True, entry, note)

        except Exception as exc:  # a bad message must never kill the listener
            logger.exception("%s: pipeline error: %s", analyzer_name, exc)
            self.counters["errors"] += 1
            entry.update(status="ERROR", note=str(exc))
            self.store.mark_message(message_id, "ERROR", str(exc))
            self._record(entry)
            return self._response(False, entry, str(exc))

    def stats(self) -> Dict[str, Any]:
        out = {"counters": dict(self.counters)}
        try:
            out["database"] = self.store.stats()
        except Exception as exc:  # pragma: no cover
            out["database"] = {"error": str(exc)}
        return out

    # ----------------------------------------------------------------- private
    def _record(self, entry: Dict[str, Any]) -> None:
        self.history.append(entry)
        del self.history[:-200]  # keep the last 200 for the demo UI

    def _response(self, ok: bool, entry: Dict[str, Any], note: str) -> Dict[str, Any]:
        ack = self.ack.build(ok and entry.get("status") != "ERROR", error="" if ok else note)
        return {
            "ok": bool(ok) and entry.get("status") != "ERROR",
            "status": entry.get("status"),
            "note": note,
            "ack": ack,
            "ack_mode": self.ack.mode,
            "message_id": entry.get("message_id"),
            "stored": entry.get("stored", 0),
            "duplicates": entry.get("duplicates", 0),
            "protocol": entry.get("detected_protocol"),
            "results": entry.get("results", []),
        }
