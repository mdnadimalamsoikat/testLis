"""
service.py — the supervised worker.

`main.py` is the developer entry point: read config once, start listeners.
`service.py` is the *production* entry point: it watches the database, applies
changes made in APEX, reports health back, and runs forever.

    python service.py                          # watch Oracle control tables
    python service.py --mode file              # watch config/config.yaml instead
    python service.py --mode file --interval 5 # same, checking every 5s
    python service.py --dry-run                # never write to Oracle
    python service.py --once                   # one control cycle, then exit (CI/cron)

What happens on a change made in APEX
-------------------------------------
1. `poll` sees `lis_config`/`lis_test_code_map` changed (a cheap fingerprint).
2. A new ConfigSnapshot is built; invalid rows are refused and written to
   `last_error` — the old, working configuration keeps running.
3. A new IngestEngine is built with the new settings and test-code map.
4. Listeners are diffed: unchanged ones keep running (no dropped connection),
   changed ones are stopped and restarted, removed ones are closed.
5. `last_applied_at` is written back, so APEX shows a green "applied" tick.

Commands from APEX (`lis_commands`) are picked up on the same tick:
`RELOAD` · `START <analyzer>` · `STOP <analyzer>` · `RESTART <analyzer>` ·
`SELFTEST <analyzer>` (parse a sample through the live engine, store nothing) ·
`PING` (liveness). Results are written back to the command row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import yaml                                     # noqa: E402

from core.ack_handler import ACKHandler          # noqa: E402
from core.engine import IngestEngine             # noqa: E402
from core.validator import ResultValidator       # noqa: E402
from database.control import (ConfigSnapshot, DatabaseControl,  # noqa: E402
                             FileControl)
from database.memory_store import MemoryStore    # noqa: E402
from listeners.serial_listener import SerialListener  # noqa: E402
from listeners.tcp_listener import TCPListener    # noqa: E402

logger = logging.getLogger("LIS.SERVICE")
WORKER_NAME = f"worker-{os.getpid()}"


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------
def setup_logging(level: str = "INFO", log_file: str = "logs/lis_service.log") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        root.addHandler(stream)
        from logging.handlers import RotatingFileHandler
        rotating = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
        rotating.setFormatter(formatter)
        root.addHandler(rotating)


# ---------------------------------------------------------------------------
# the supervisor
# ---------------------------------------------------------------------------
class Supervisor:
    def __init__(self, control, store_factory, dry_run: bool = False):
        self.control = control
        self.store_factory = store_factory
        self.dry_run = dry_run

        self.snapshot: Optional[ConfigSnapshot] = None
        self.engine: Optional[IngestEngine] = None
        self.listeners: Dict[str, Any] = {}          # analyzer name → listener
        self._stopped_names: set[str] = set()
        self.started_at = datetime.now()
        self.reloads = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ startup
    async def start(self) -> None:
        snapshot = self.control.load_config()
        if snapshot is None:
            logger.error("no configuration available — nothing to start")
            return
        problems = snapshot.problems()
        if problems:
            self.last_error = "; ".join(problems[:3])
            self.control.set_last_error(self.last_error)
            logger.error("configuration refused: %s", self.last_error)
            return

        await self._apply(snapshot)
        self.control.mark_applied(snapshot.version, WORKER_NAME)
        logger.info("service started with %d listener(s) (%s)",
                    len(self.listeners), snapshot.source)

    # ------------------------------------------------------------------ polling
    async def tick(self) -> None:
        """One control cycle: commands first, then configuration."""
        for command in self.control.claim_commands():
            await self._run_command(command)

        version = self.control.config_version()
        if self.snapshot is None or version != self.snapshot.version:
            source = "APEX" if getattr(self.control, "schema", None) else "config.yaml"
            logger.info("configuration changed (%s) — reloading", source)
            await self.reload(triggered_by=source)

        await self._heartbeat()

    async def reload(self, triggered_by: str = "manual") -> Dict[str, Any]:
        snapshot = self.control.load_config()
        if snapshot is None:
            return {"ok": False, "message": "no configuration found"}
        problems = snapshot.problems()
        if problems:
            self.last_error = "; ".join(problems[:3])
            self.control.set_last_error(self.last_error)
            logger.error("reload refused, keeping the working config: %s", self.last_error)
            return {"ok": False, "message": self.last_error}

        await self._apply(snapshot)
        self.control.mark_applied(snapshot.version, WORKER_NAME)
        self.reloads += 1
        self.last_error = None
        message = (f"reloaded by {triggered_by}: {len(self.listeners)} listener(s), "
                   f"config {snapshot.version}")
        logger.info(message)
        self.control.insert_audit("RELOAD", "-", message)
        return {"ok": True, "message": message}

    # ------------------------------------------------------------------- apply
    async def _apply(self, snapshot: ConfigSnapshot) -> None:
        """Start/stop/restart only what actually changed."""
        wanted = {a.name: a for a in snapshot.analyzers if a.enabled}
        changed: List[str] = []

        # 1. stop listeners that disappeared or whose settings changed
        for name in list(self.listeners):
            listener = self.listeners[name]
            target = wanted.get(name)
            if target is None or target.as_dict() != getattr(listener, "_config", target.as_dict()):
                await self._stop_listener(name)
                changed.append(name)

        # 2. build a fresh engine for the whole snapshot
        self.engine = self._build_engine(snapshot)

        # 3. start the missing ones
        loop = asyncio.get_running_loop()
        for name, analyzer in wanted.items():
            if name in self.listeners:
                continue
            if name in self._stopped_names:
                logger.info("%s stayed stopped (STOP command from APEX)", name)
                continue
            try:
                listener = await self._start_listener(analyzer.as_dict(), loop)
                self.listeners[name] = listener
                changed.append(name)
            except Exception as exc:
                logger.error("could not start %s: %s", name, exc)
                self.control.set_last_error(f"{name}: {exc}", name)

        self.snapshot = snapshot
        if changed:
            logger.info("listeners now: %s", ", ".join(sorted(self.listeners)) or "-")

    def _build_engine(self, snapshot: ConfigSnapshot) -> IngestEngine:
        settings = snapshot.settings
        known = None
        if settings.strict_test_codes:
            known = {code for analyzer in snapshot.analyzers for code in analyzer.test_codes} or None

        analyzer_maps: Dict[str, Any] = {}
        for analyzer in snapshot.analyzers:
            analyzer_maps[analyzer.name] = analyzer.test_code_map

        engine = IngestEngine(
            store=self.store_factory(),
            validator=ResultValidator(known, settings.ignore_sample_prefixes),
            ack_handler=ACKHandler(settings.ack_mode),
            test_code_map=snapshot.test_code_map,
            known_test_codes=known,
        )
        engine.analyzer_code_maps = analyzer_maps       # per-machine overrides
        return engine

    async def _start_listener(self, analyzer: Dict[str, Any], loop) -> Any:
        if str(analyzer.get("connection_type")).upper() == "SERIAL":
            listener = SerialListener(analyzer, self.engine, loop=loop)
            listener.start()
        else:
            listener = TCPListener(analyzer, self.engine)
            listener._task = await listener.start_background()
        listener._config = analyzer
        return listener

    async def _stop_listener(self, name: str) -> None:
        listener = self.listeners.pop(name, None)
        if listener is None:
            return
        try:
            if isinstance(listener, TCPListener):
                if listener._task is not None and not listener._task.done():
                    listener._task.cancel()
                    try:
                        await listener._task
                    except (asyncio.CancelledError, Exception):
                        pass
                await listener.stop()
            else:
                listener.stop()
        except Exception as exc:
            logger.warning("error while stopping %s: %s", name, exc)

    async def _heartbeat(self) -> None:
        statuses: List[Dict[str, Any]] = []
        for name, listener in self.listeners.items():
            status = listener.status()
            status["state"] = "ONLINE" if status.get("running") else "STOPPED"
            statuses.append(status)
        for name in self._stopped_names:
            statuses.append({"name": name, "state": "STOPPED", "running": False})
        self.control.write_status(statuses)

    # ---------------------------------------------------------------- commands
    async def _run_command(self, command: Dict[str, Any]) -> None:
        kind = command["command_type"]
        payload = command.get("payload")
        name = command.get("analyzer_name") or ""
        logger.info("command %s: %s %s", command["command_id"], kind, name)

        try:
            if kind == "PING":
                ok, message = True, f"alive, {len(self.listeners)} listener(s), uptime {self.uptime()}"

            elif kind == "RELOAD":
                result = await self.reload(triggered_by=f"APEX command {command['command_id']}")
                ok, message = result["ok"], result["message"]

            elif kind in ("START", "STOP", "RESTART"):
                ok, message = await self._control_listener(kind, name, payload)

            elif kind == "SELFTEST":
                ok, message = await self._selftest(name, payload)

            else:
                ok, message = False, f"unknown command type {kind!r}"

        except Exception as exc:
            ok, message = False, f"{type(exc).__name__}: {exc}"
            logger.exception("command %s failed", command["command_id"])

        self.control.finish_command(command["command_id"], ok, message)
        self.control.insert_audit(f"CMD_{kind}", name or "-", message)

    async def _control_listener(self, kind: str, name: str, payload: Optional[str]):
        if not name:
            return False, "analyzer_name is required for this command"

        if kind == "STOP":
            await self._stop_listener(name)
            self._stopped_names.add(name)
            return True, f"{name} stopped"

        if kind == "START":
            self._stopped_names.discard(name)
            if name in self.listeners:
                return True, f"{name} already running"
            if self.snapshot is None:
                return False, "no configuration loaded"
            analyzer = next((a for a in self.snapshot.analyzers if a.name == name), None)
            if analyzer is None:
                return False, f"{name} is not in lis_config (or is inactive)"
            listener = await self._start_listener(analyzer.as_dict(), asyncio.get_running_loop())
            self.listeners[name] = listener
            return True, f"{name} started on {analyzer.port or analyzer.serial_port}"

        # RESTART
        await self._stop_listener(name)
        self._stopped_names.discard(name)
        if self.snapshot is None:
            return False, "no configuration loaded"
        analyzer = next((a for a in self.snapshot.analyzers if a.name == name), None)
        if analyzer is None:
            return False, f"{name} is not in lis_config"
        listener = await self._start_listener(analyzer.as_dict(), asyncio.get_running_loop())
        self.listeners[name] = listener
        return True, f"{name} restarted"

    async def _selftest(self, name: str, payload: Optional[str]):
        """Feed a sample through the live engine — proves the wiring without an analyzer."""
        if self.engine is None:
            return False, "engine not running"
        sample_path = ROOT / "samples" / "messages.yaml"
        samples = yaml.safe_load(sample_path.read_text(encoding="utf-8"))
        scenario = (payload or "astm_cbc_normal").strip()
        raw = samples.get(scenario)
        if not raw:
            return False, f"unknown scenario {scenario!r} (try: {', '.join(list(samples)[:3])} …)"

        analyzer_row = next((a for a in (self.snapshot.analyzers if self.snapshot else [])
                             if a.name == name), None)
        analyzer = analyzer_row.as_dict() if analyzer_row else {"name": name or "SELFTEST", "protocol": "AUTO"}
        analyzer = dict(analyzer)
        analyzer["protocol"] = "AUTO"
        result = await self.engine.handle_payload(raw, analyzer, peer="selftest")
        return bool(result.get("ok")), (
            f"{scenario}: {result['status']}, {result.get('stored', 0)} stored, "
            f"ack={result.get('ack_mode')} ({result.get('note')})"
        )

    # -------------------------------------------------------------------- misc
    def uptime(self) -> str:
        seconds = int((datetime.now() - self.started_at).total_seconds())
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"

    def health(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.listeners) and self.last_error is None,
            "listeners": {name: listener.status() for name, listener in self.listeners.items()},
            "stopped": sorted(self._stopped_names),
            "uptime": self.uptime(),
            "reloads": self.reloads,
            "last_error": self.last_error,
            "config_source": self.snapshot.source if self.snapshot else None,
            "config_version": self.snapshot.version if self.snapshot else None,
        }

    async def shutdown(self) -> None:
        for name in list(self.listeners):
            await self._stop_listener(name)
        if hasattr(self.control, "db") and self.control.db is not None:
            self.control.db.close()
        logger.info("service stopped")


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    config_path = ROOT / "config" / "config.yaml"
    settings = {}
    if config_path.exists():
        settings = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    app = settings.get("app") or {}
    setup_logging(str(app.get("log_level", "INFO")),
                  str(ROOT / str(app.get("log_file", "logs/lis_service.log"))).replace("lis_middleware", "lis_service"))

    # --- storage -----------------------------------------------------------
    store_factory = None
    db = None
    if args.dry_run:
        logger.warning("DRY RUN: results are kept in memory, Oracle is not written")
        shared = MemoryStore()
        store_factory = lambda: shared          # noqa: E731
    else:
        from database.db_connection import OracleDB
        from database.db_writer import DBWriter

        db = OracleDB(str(config_path))
        db.connect()
        writer = DBWriter(db)
        store_factory = lambda: writer          # noqa: E731

    # --- what drives the configuration ------------------------------------
    control = None
    if args.mode == "db" and db is not None:
        candidate = DatabaseControl(db, settings.get("database", {}).get("schema"))
        if candidate.available():
            control = candidate
            logger.info("configuration source: Oracle control tables (%s.lis_config)", candidate.schema)
        else:
            logger.warning("lis_config not found in Oracle — falling back to config/config.yaml")
            logger.warning("run database/apex_install.sql to move control into APEX")
    if control is None:
        control = FileControl(str(config_path))
        logger.info("configuration source: %s", config_path)

    supervisor = Supervisor(control, store_factory, dry_run=args.dry_run)
    await supervisor.start()

    if args.once:
        await supervisor.tick()
        print(json.dumps(supervisor.health(), indent=2, default=str))
        await supervisor.shutdown()
        return 0

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:           # Windows
            signal.signal(sig, lambda *_: stop.set())

    logger.info("watching for APEX changes every %ds (Ctrl+C to stop)", args.interval)
    try:
        while not stop.is_set():
            try:
                await supervisor.tick()
            except Exception as exc:          # a broken control cycle must not kill the service
                logger.exception("control cycle failed: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except asyncio.TimeoutError:
                pass
    finally:
        await supervisor.shutdown()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LIS middleware service (APEX-driven)")
    parser.add_argument("--mode", choices=["db", "file"], default="db",
                        help="db = read configuration from APEX tables (default)")
    parser.add_argument("--interval", type=int, default=5, help="seconds between checks")
    parser.add_argument("--dry-run", action="store_true", help="keep results in memory")
    parser.add_argument("--once", action="store_true",
                        help="run one control cycle and print the health JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
