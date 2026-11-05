"""
Database-driven control — the APEX side of the middleware.

Design rule
-----------
**APEX is the control panel. Oracle tables are the only interface between APEX
and Python.** The operator never edits a file, never opens a terminal and never
sees Python: they change rows in an APEX form, and the service picks the change
up within a few seconds, applies it, and writes back "applied" or the error.

Two directions:

    APEX  →  lis_config      (analyzers, settings)      →  service applies
    APEX  →  lis_commands    (reload / test / restart)  →  service executes
    APEX  ←  lis_analyzer_status (online, last message, counters)
    APEX  ←  lis_config        (applied_at, applied_by, last_error)
    APEX  ←  lab_results, lis_raw_messages

Safety rules baked in
---------------------
* the service **never trusts an unfinished edit** — only ``status='ACTIVE'``
  rows are applied, so a half-typed port never reaches a live analyzer;
* every applied change is recorded (``applied_at``, ``applied_by``,
  ``applied_version``) so you can see who changed what and when;
* an invalid value is refused and written to ``last_error`` — APEX shows it in
  red instead of the middleware dying;
* a failed reload keeps the previous, working configuration running.

If the control tables do not exist (fresh install, or someone wants a
file-based setup) the service falls back to ``config/config.yaml``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("LIS.CONTROL")

VALID_PROTOCOLS = ("ASTM", "HL7", "AUTO")
VALID_TRANSPORTS = ("TCP", "SERIAL")
VALID_ACK_MODES = ("ASTM", "MLLP")


@dataclass
class AnalyzerConfig:
    """One analyzer row, as edited in APEX."""
    name: str
    enabled: bool = True
    protocol: str = "AUTO"
    transport: str = "TCP"
    host: str = "0.0.0.0"
    port: int = 0
    serial_port: Optional[str] = None
    baud_rate: int = 9600
    data_bits: int = 8
    parity: str = "NONE"
    stop_bits: int = 1
    brand: str = ""
    model: str = ""
    test_codes: List[str] = field(default_factory=list)
    test_code_map: Dict[str, str] = field(default_factory=dict)
    version: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """The shape the listeners/parsers already expect."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "protocol": self.protocol,
            "connection_type": self.transport,
            "host": self.host,
            "port": self.port,
            "serial_port": self.serial_port,
            "baud_rate": self.baud_rate,
            "data_bits": self.data_bits,
            "parity": self.parity,
            "stop_bits": self.stop_bits,
            "brand": self.brand,
            "model": self.model,
            "test_codes": self.test_codes,
            "test_code_map": self.test_code_map,
        }

    def validate(self) -> List[str]:
        problems: List[str] = []
        if not self.name.strip():
            problems.append("name is required")
        if self.protocol.upper() not in VALID_PROTOCOLS:
            problems.append(f"protocol must be one of {VALID_PROTOCOLS}")
        if self.transport.upper() not in VALID_TRANSPORTS:
            problems.append(f"connection_type must be one of {VALID_TRANSPORTS}")
        if self.transport.upper() == "TCP":
            if not (1024 <= int(self.port or 0) <= 65535):
                problems.append(f"port {self.port} is outside 1024-65535")
        else:
            if not self.serial_port:
                problems.append("serial_port is required for SERIAL")
            if self.parity.upper() not in ("NONE", "EVEN", "ODD", "MARK", "SPACE"):
                problems.append(f"parity {self.parity!r} is invalid")
        return problems


@dataclass
class RuntimeSettings:
    ack_mode: str = "ASTM"
    default_protocol: str = "AUTO"
    strict_test_codes: bool = False
    log_level: str = "INFO"
    ignore_sample_prefixes: List[str] = field(default_factory=lambda: ["QC", "CTRL", "CAL"])

    def validate(self) -> List[str]:
        problems: List[str] = []
        if self.ack_mode.upper() not in VALID_ACK_MODES:
            problems.append(f"ack_mode must be one of {VALID_ACK_MODES}")
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            problems.append("log_level must be DEBUG/INFO/WARNING/ERROR")
        return problems


@dataclass
class ConfigSnapshot:
    """A complete, validated configuration taken from the database."""
    analyzers: List[AnalyzerConfig] = field(default_factory=list)
    settings: RuntimeSettings = field(default_factory=RuntimeSettings)
    test_code_map: Dict[str, str] = field(default_factory=dict)
    version: str = ""                       # e.g. "2026-11-04T09:12:00|7"
    source: str = "file"                    # database | file
    loaded_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def analyzer_dicts(self) -> List[Dict[str, Any]]:
        return [a.as_dict() for a in self.analyzers if a.enabled]

    def problems(self) -> List[str]:
        out: List[str] = []
        for analyzer in self.analyzers:
            out += [f"{analyzer.name}: {p}" for p in analyzer.validate()]
        out += [f"settings: {p}" for p in self.settings.validate()]
        ports: Dict[int, str] = {}
        for analyzer in self.analyzers:
            if analyzer.enabled and analyzer.transport.upper() == "TCP":
                if analyzer.port in ports:
                    out.append(f"port {analyzer.port} used by both {ports[analyzer.port]} and {analyzer.name}")
                ports[analyzer.port] = analyzer.name
        return out


# ---------------------------------------------------------------------------
# control-table adapter
# ---------------------------------------------------------------------------
class DatabaseControl:
    """Reads config/commands from Oracle, writes status back.

    Uses the same OracleDB pool as the writer, so there is one connection
    budget for the whole service.
    """

    def __init__(self, db, schema: Optional[str] = None):
        self.db = db
        self.schema = (schema or db.schema).upper()

    # -------------------------------------------------------------- read side
    def available(self) -> bool:
        try:
            with self.db.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = 'LIS_CONFIG'",
                    {"o": self.schema},
                )
                return bool(cur.fetchone()[0])
        except Exception as exc:  # pragma: no cover - driver/permission issues
            logger.warning("control tables unavailable: %s", exc)
            return False

    def config_version(self) -> str:
        """Cheap fingerprint; changes as soon as any row is edited.

        Only `config_version` (bumped by the DB trigger on a real edit) and the
        row count are used — NOT `applied_at`, which the service itself writes.
        Including it would make the service reload on every tick.
        """
        sql = f"""
            SELECT NVL(SUM(config_version), 0), NVL(MAX(config_version), 0), COUNT(*)
              FROM {self.schema}.lis_config
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
                total, highest, count = cur.fetchone()
            return f"{total}|{highest}|{count}"
        except Exception as exc:
            logger.debug("config_version failed: %s", exc)
            return ""

    def load_config(self) -> Optional[ConfigSnapshot]:
        """Build a snapshot, or None when APEX has nothing usable yet."""
        try:
            analyzers = self._load_analyzers()
            settings = self._load_settings()
            code_map = self._load_test_code_map()
        except Exception as exc:
            logger.error("could not read configuration from the database: %s", exc)
            return None

        if not analyzers and not settings:
            logger.warning("lis_config is empty — add an analyzer in APEX")
            return None

        snapshot = ConfigSnapshot(
            analyzers=analyzers,
            settings=settings,
            test_code_map=code_map,
            version=self.config_version(),
            source="database",
        )
        for problem in snapshot.problems():
            logger.warning("config problem: %s", problem)
        return snapshot

    def _load_analyzers(self) -> List[AnalyzerConfig]:
        sql = f"""
            SELECT analyzer_name, enabled, protocol, connection_type, host, port,
                   serial_port, baud_rate, data_bits, parity, stop_bits,
                   brand, model, test_codes, test_code_map, config_version
              FROM {self.schema}.lis_config
             WHERE config_type = 'ANALYZER'
               AND status = 'ACTIVE'
             ORDER BY display_order, analyzer_name
        """
        out: List[AnalyzerConfig] = []
        with self.db.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                (name, enabled, protocol, conn, host, port, serial_port, baud,
                 data_bits, parity, stop_bits, brand, model, test_codes,
                 code_map, version) = row
                out.append(AnalyzerConfig(
                    name=str(name),
                    enabled=str(enabled).upper() in ("Y", "1", "TRUE"),
                    protocol=str(protocol or "AUTO").upper(),
                    transport=str(conn or "TCP").upper(),
                    host=str(host or "0.0.0.0"),
                    port=int(port or 0),
                    serial_port=serial_port,
                    baud_rate=int(baud or 9600),
                    data_bits=int(data_bits or 8),
                    parity=str(parity or "NONE").upper(),
                    stop_bits=int(stop_bits or 1),
                    brand=str(brand or ""),
                    model=str(model or ""),
                    test_codes=_split_list(test_codes),
                    test_code_map=_parse_json(code_map),
                    version=int(version or 1),
                ))
        return out

    def _load_settings(self) -> Optional[RuntimeSettings]:
        sql = f"""
            SELECT config_key, config_value
              FROM {self.schema}.lis_config
             WHERE config_type = 'SETTING'
               AND status = 'ACTIVE'
        """
        values: Dict[str, str] = {}
        with self.db.cursor() as cur:
            cur.execute(sql)
            for key, value in cur.fetchall():
                values[str(key).upper()] = str(value or "")
        if not values:
            return None
        return RuntimeSettings(
            ack_mode=values.get("ACK_MODE", "ASTM").upper(),
            default_protocol=values.get("DEFAULT_PROTOCOL", "AUTO").upper(),
            strict_test_codes=values.get("STRICT_TEST_CODES", "N") in ("Y", "1", "TRUE"),
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
            ignore_sample_prefixes=_split_list(values.get("IGNORE_SAMPLE_PREFIXES", "QC,CTRL,CAL")),
        )

    def _load_test_code_map(self) -> Dict[str, str]:
        sql = f"""
            SELECT machine_code, lis_code
              FROM {self.schema}.lis_test_code_map
             WHERE status = 'ACTIVE'
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
                return {str(m).upper(): str(l) for m, l in cur.fetchall()}
        except Exception:
            return {}

    # ------------------------------------------------------------- write side
    def mark_applied(self, version: str, by: str) -> None:
        """Tell APEX the change is live (drives the green tick in the form)."""
        sql = f"""
            UPDATE {self.schema}.lis_config
               SET last_applied_at = SYSTIMESTAMP,
                   applied_at = SYSTIMESTAMP,
                   applied_by = {_lit(by)},
                   last_error = NULL
             WHERE status = 'ACTIVE'
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
        except Exception as exc:
            logger.warning("could not mark config applied: %s", exc)

    def set_last_error(self, message: str, analyzer: Optional[str] = None) -> None:
        sql = f"""
            UPDATE {self.schema}.lis_config
               SET last_error = {_lit(message, 500)},
                   last_error_at = SYSTIMESTAMP
             WHERE status = 'ACTIVE'
        """
        if analyzer:
            sql += f" AND analyzer_name = {_lit(analyzer)}"
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
        except Exception as exc:
            logger.warning("could not record config error: %s", exc)

    def write_status(self, statuses: List[Dict[str, Any]]) -> None:
        """Heartbeat: one row per analyzer, so APEX can show online/offline."""
        if not statuses:
            return
        try:  # noqa: SIM105 - a heartbeat must never break the control loop
            with self.db.cursor() as cur:
                for status in statuses:
                    seen = status.get("last_message_at")
                    last_seen = (f"TO_TIMESTAMP({_lit(seen, 19)}, 'YYYY-MM-DD HH24:MI:SS')"
                                 if seen else "NULL")
                    state = "ONLINE" if status.get("running") else "STOPPED"
                    name = _lit(status.get("name"))
                    cur.execute(f"""
                        MERGE INTO {self.schema}.lis_analyzer_status t
                        USING (SELECT {name} AS analyzer_name FROM dual) s
                           ON (t.analyzer_name = s.analyzer_name)
                        WHEN MATCHED THEN UPDATE SET
                             t.protocol = {_lit(status.get("protocol"), 10)},
                             t.connection_type = {_lit(status.get("connection_type"), 10)},
                             t.host = {_lit(status.get("host"), 120)},
                             t.port = {int(status.get("port") or 0)},
                             t.state = {_lit(state, 10)},
                             t.active_connections = {int(status.get("active_connections") or 0)},
                             t.messages_received = {int(status.get("messages_received") or 0)},
                             t.last_message_at = CASE WHEN {last_seen} IS NULL
                                                      THEN t.last_message_at ELSE {last_seen} END,
                             t.last_seen_at = SYSTIMESTAMP
                        WHEN NOT MATCHED THEN INSERT
                             (analyzer_name, protocol, connection_type, host, port, state,
                              active_connections, messages_received, last_message_at, last_seen_at)
                        VALUES ({name},
                                {_lit(status.get("protocol"), 10)},
                                {_lit(status.get("connection_type"), 10)},
                                {_lit(status.get("host"), 120)},
                                {int(status.get("port") or 0)},
                                {_lit(state, 10)},
                                {int(status.get("active_connections") or 0)},
                                {int(status.get("messages_received") or 0)},
                                {last_seen}, SYSTIMESTAMP)
                    """)
        except Exception as exc:
            logger.warning("could not write analyzer status: %s", exc)

    # ---------------------------------------------------------------- commands
    def claim_commands(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Atomically take PENDING commands so two workers never run one twice."""
        sql = f"""
            SELECT command_id, command_type, analyzer_name, payload
              FROM {self.schema}.lis_commands
             WHERE status = 'PENDING'
             ORDER BY requested_at
             FETCH FIRST {int(limit)} ROWS ONLY
        """
        claimed: List[Dict[str, Any]] = []
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                for command_id, command_type, analyzer_name, payload in rows:
                    update = f"""
                        UPDATE {self.schema}.lis_commands
                           SET status = 'RUNNING', started_at = SYSTIMESTAMP
                         WHERE command_id = {int(command_id)} AND status = 'PENDING'
                    """
                    cur.execute(update)
                    if cur.rowcount == 1:
                        claimed.append({
                            "command_id": int(command_id),
                            "command_type": str(command_type).upper(),
                            "analyzer_name": analyzer_name,
                            "payload": payload,
                        })
        except Exception as exc:
            logger.debug("no command queue (%s)", exc)
        return claimed

    def finish_command(self, command_id: int, ok: bool, message: str = "") -> None:
        outcome = "DONE" if ok else "FAILED"
        sql = f"""
            UPDATE {self.schema}.lis_commands
               SET status = {_lit(outcome, 10)},
                   finished_at = SYSTIMESTAMP,
                   result_message = {_lit(message, 1000)}
             WHERE command_id = {int(command_id)}
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
        except Exception as exc:
            logger.warning("could not finish command %s: %s", command_id, exc)

    def insert_audit(self, event: str, analyzer: str, detail: str) -> None:
        sql = f"""
            INSERT INTO {self.schema}.lis_audit_log (event_type, analyzer_name, detail, created_at)
            VALUES ({_lit(event, 50)}, {_lit(analyzer)}, {_lit(detail, 1000)}, SYSTIMESTAMP)
        """
        try:
            with self.db.cursor() as cur:
                cur.execute(sql)
        except Exception as exc:
            logger.debug("audit insert failed: %s", exc)


# ---------------------------------------------------------------------------
# file fallback
# ---------------------------------------------------------------------------
class FileControl:
    """Same interface, backed by config.yaml — used when the tables are absent."""

    def __init__(self, path: str):
        self.path = path
        self._last_mtime = 0.0

    def available(self) -> bool:
        return os.path.exists(self.path)

    def config_version(self) -> str:
        try:
            return str(os.path.getmtime(self.path))
        except OSError:
            return ""

    def load_config(self) -> Optional[ConfigSnapshot]:
        import yaml

        if not self.available():
            return None
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
        except Exception as exc:
            logger.error("%s is not valid YAML: %s", self.path, exc)
            return None

        app = raw.get("app") or {}
        validation = raw.get("validation") or {}
        snapshot = ConfigSnapshot(
            analyzers=[_analyzer_from_file(a) for a in raw.get("analyzers", [])],
            settings=RuntimeSettings(
                ack_mode=str(app.get("ack_mode", "ASTM")).upper(),
                default_protocol=str(app.get("default_protocol", "AUTO")).upper(),
                strict_test_codes=bool(app.get("strict_test_codes", False)),
                log_level=str(app.get("log_level", "INFO")).upper(),
                ignore_sample_prefixes=[str(p).upper() for p in validation.get("ignore_sample_prefixes", [])],
            ),
            test_code_map={str(k).upper(): str(v) for k, v in (raw.get("test_code_map") or {}).items()},
            version=self.config_version(),
            source="file",
        )
        return snapshot

    # no-op writers: the file is the source of truth
    def mark_applied(self, version: str, by: str) -> None:
        self._last_mtime = float(version or 0)

    def set_last_error(self, message: str, analyzer: Optional[str] = None) -> None:
        logger.error("configuration error: %s", message)

    def write_status(self, statuses: List[Dict[str, Any]]) -> None:
        for status in statuses:
            state = "ONLINE" if status.get("running") else "STOPPED"
            previous = getattr(self, "_states", {}).get(status.get("name"))
            if state != previous:                       # only log real transitions
                logger.info("%s is %s", status.get("name"), state)
                self._states = getattr(self, "_states", {})
                self._states[status.get("name")] = state

    def claim_commands(self, limit: int = 5) -> List[Dict[str, Any]]:
        return []

    def finish_command(self, command_id: int, ok: bool, message: str = "") -> None:
        pass

    def insert_audit(self, event: str, analyzer: str, detail: str) -> None:
        pass


def _analyzer_from_file(raw: Dict[str, Any]) -> AnalyzerConfig:
    return AnalyzerConfig(
        name=str(raw.get("name", "UNKNOWN")),
        enabled=bool(raw.get("enabled", True)),
        protocol=str(raw.get("protocol", "AUTO")).upper(),
        transport=str(raw.get("connection_type", "TCP")).upper(),
        host=str(raw.get("host", "0.0.0.0")),
        port=int(raw.get("port") or 0),
        serial_port=raw.get("serial_port"),
        baud_rate=int(raw.get("baud_rate") or 9600),
        data_bits=int(raw.get("data_bits") or 8),
        parity=str(raw.get("parity", "NONE")).upper(),
        stop_bits=int(raw.get("stop_bits") or 1),
        brand=str(raw.get("brand", "")),
        model=str(raw.get("model", "")),
        test_codes=[str(c).upper() for c in raw.get("test_codes", []) or []],
        test_code_map={str(k).upper(): str(v) for k, v in (raw.get("test_code_map") or {}).items()},
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
#: Oracle rejects a bind variable whose name is a reserved word — `applied_by =
#: :by` failed with ORA-01745 on 19.3. Renaming the bind is not enough (the
#: reserved list is long and version dependent: :name, :state, :host, :status …),
#: so no bind name here is a column name. Values that come from our own rows go
#: in as quoted literals via `_lit()`, numbers as validated `int()`.
def _lit(value: Any, limit: int = 100) -> str:
    """Quote a value for inlining. Returns NULL for None."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''")[:limit] + "'"



def _split_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip().upper() for v in value if str(v).strip()]
    text = str(value)
    if text.startswith("["):
        try:
            return [str(v).strip().upper() for v in json.loads(text) if str(v).strip()]
        except Exception:
            pass
    return [part.strip().upper() for part in text.replace(";", ",").split(",") if part.strip()]


def _parse_json(value: Any) -> Dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k).upper(): str(v) for k, v in value.items()}
    try:
        parsed = json.loads(str(value))
        return {str(k).upper(): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except Exception:
        return {}
