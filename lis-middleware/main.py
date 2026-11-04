"""
Entry point — starts every enabled analyzer listener.

    python main.py                 # use config/config.yaml
    python main.py --config x.yaml
    python main.py --dry-run       # parse + log, write to memory instead of Oracle

Ctrl+C stops everything cleanly (listeners closed, DB pool released).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List

import yaml

from core.ack_handler import ACKHandler
from core.engine import IngestEngine
from core.validator import ResultValidator
from database.memory_store import MemoryStore
from listeners.serial_listener import SerialListener
from listeners.tcp_listener import TCPListener

logger = logging.getLogger("LIS.MAIN")


# --------------------------------------------------------------------- config
def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def setup_logging(config: Dict[str, Any]) -> None:
    app = config.get("app", {}) or {}
    level = getattr(logging, str(app.get("log_level", "INFO")).upper(), logging.INFO)
    log_file = app.get("log_file", "logs/lis_middleware.log")
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-14s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


# ---------------------------------------------------------------------- wiring
def build_store(config: Dict[str, Any], dry_run: bool) -> Any:
    if dry_run:
        logger.warning("DRY RUN: results go to memory, Oracle is not touched")
        return MemoryStore()
    from database.db_connection import OracleDB
    from database.db_writer import DBWriter

    db = OracleDB(config_path="config/config.yaml")
    db.connect()
    return DBWriter(db)


def build_engine(config: Dict[str, Any], store: Any, analyzer: Dict[str, Any]) -> IngestEngine:
    app = config.get("app", {}) or {}
    validation = config.get("validation", {}) or {}

    known = None
    if app.get("strict_test_codes"):
        known = set()
        for cfg in config.get("analyzers", []) or []:
            known.update(str(c).upper() for c in cfg.get("test_codes", []) or [])

    validator = ResultValidator(known, validation.get("ignore_sample_prefixes"))
    # Per-analyzer test-code map first, then the global one
    code_map = {**config.get("test_code_map", {}),
                **(analyzer.get("test_code_map", {}) or {})}

    return IngestEngine(
        store=store,
        validator=validator,
        ack_handler=ACKHandler(app.get("ack_mode", "ASTM")),
        test_code_map=code_map,
        known_test_codes=known,
    )


def start_listeners(config: Dict[str, Any], store: Any) -> List[Any]:
    started: List[Any] = []
    loop = asyncio.get_running_loop()

    for analyzer in config.get("analyzers", []) or []:
        if not analyzer.get("enabled", True):
            logger.info("skipped (disabled): %s", analyzer.get("name"))
            continue

        engine = build_engine(config, store, analyzer)
        conn_type = str(analyzer.get("connection_type", "TCP")).upper()

        if conn_type == "SERIAL":
            listener = SerialListener(analyzer, engine, loop=loop)
            listener.start()
        else:
            listener = TCPListener(analyzer, engine)
            asyncio.create_task(listener.start(), name=analyzer.get("name"))

        started.append(listener)

    return started


# ------------------------------------------------------------------------ main
async def run(config_path: str, dry_run: bool) -> None:
    config = load_config(config_path)
    setup_logging(config)

    logger.info("=" * 66)
    logger.info("LIS Middleware starting — Oracle APEX + Oracle DB integration")
    logger.info("=" * 66)

    store = build_store(config, dry_run)
    listeners = start_listeners(config, store)

    if not listeners:
        logger.error("no enabled analyzers in config — nothing to do")
        return

    logger.info("LIS Middleware is RUNNING (%d listener(s)). Press Ctrl+C to stop.", len(listeners))

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: stop.set())

    await stop.wait()

    logger.info("shutting down…")
    for listener in listeners:
        try:
            result = listener.stop()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("error stopping %s: %s", getattr(listener, "name", "?"), exc)

    if hasattr(store, "close"):
        store.close()
    logger.info("LIS Middleware stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LIS Middleware — ASTM/HL7 to Oracle")
    parser.add_argument("-c", "--config", default="config/config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and log everything, but keep results in memory")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run(args.config, args.dry_run))
    except KeyboardInterrupt:
        print("\nbye")
