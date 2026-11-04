"""
Oracle connection layer using ``python-oracledb`` in *thin mode*.

Thin mode means **no Oracle Client install, no tnsnames.ora** — just a DSN
string. Works with Oracle 19c, 21c and 23ai (and 12c+).

    db = OracleDB("config/config.yaml")
    db.connect()
    conn = db.get_connection()
    ...
    db.release_connection(conn)
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import yaml

logger = logging.getLogger("LIS.DB")

try:  # oracledb is only needed when you actually talk to Oracle
    import oracledb
except ImportError:  # pragma: no cover - allows the demo to run without it
    oracledb = None


class DatabaseUnavailable(RuntimeError):
    """Raised when the Oracle driver is missing or the DB is unreachable."""


class OracleDB:
    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        db_cfg: dict[str, Any] = config.get("database", {})

        self.host = str(db_cfg.get("host", "localhost"))
        self.port = int(db_cfg.get("port", 1521))
        self.service_name = str(db_cfg.get("service_name", "XEPDB1"))
        self.username = str(db_cfg.get("username", "lis_user"))
        # never keep production passwords in config.yaml — prefer the environment
        self.password = os.environ.get("LIS_DB_PASSWORD") or str(db_cfg.get("password", ""))
        self.schema = str(db_cfg.get("schema", self.username)).upper()

        pool_cfg = db_cfg.get("pool", {}) or {}
        self.min_pool = int(pool_cfg.get("min", 2))
        self.max_pool = int(pool_cfg.get("max", 10))
        self.increment = int(pool_cfg.get("increment", 1))

        self.dsn = f"{self.host}:{self.port}/{self.service_name}"
        self.pool: Optional["oracledb.ConnectionPool"] = None

    # ------------------------------------------------------------------ public
    def connect(self) -> None:
        if oracledb is None:
            raise DatabaseUnavailable(
                "python-oracledb is not installed — run: pip install oracledb"
            )
        if self.pool is not None:
            return
        logger.info("connecting to Oracle at %s", self.dsn)
        self.pool = oracledb.create_pool(
            user=self.username,
            password=self.password,
            dsn=self.dsn,
            min=self.min_pool,
            max=self.max_pool,
            increment=self.increment,
            timeout=30,
            getmode=oracledb.POOL_GETMODE_WAIT,
        )
        self._health_check()
        logger.info("Oracle DB connected: %s (schema %s)", self.dsn, self.schema)

    def get_connection(self) -> "oracledb.Connection":
        if self.pool is None:
            raise DatabaseUnavailable("call connect() first")
        return self.pool.acquire()

    def release_connection(self, conn: "oracledb.Connection") -> None:
        if self.pool is not None and conn is not None:
            self.pool.release(conn)

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self.release_connection(conn)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close(force=True)
            self.pool = None
            logger.info("Oracle DB disconnected")

    # ----------------------------------------------------------------- private
    def _health_check(self) -> None:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUAL")
            cur.fetchone()
            cur.close()
        finally:
            self.release_connection(conn)


if __name__ == "__main__":  # quick manual check: python -m database.db_connection
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = OracleDB("config/config.yaml")
    try:
        db.connect()
        print("OK:", db.dsn)
    finally:
        db.close()
