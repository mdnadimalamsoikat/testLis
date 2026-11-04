"""
preflight — everything that must be true *before* you drive to the lab.

Run this on the machine that will host the middleware, the evening before, and
again on the day. It checks the boring things that otherwise cost you a trip:

    python tools/preflight.py                 # full check (needs Oracle)
    python tools/preflight.py --offline       # skip the database
    python tools/preflight.py --json out.json # machine readable report

Exit code 0 = all good, 1 = something must be fixed first, 2 = warnings only.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    sys.exit("PyYAML missing — pip install -r requirements.txt")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results: List[Tuple[str, str, str, str]] = []   # status, area, message, fix


def record(status: str, area: str, message: str, fix: str = "") -> None:
    results.append((status, area, message, fix))


# ---------------------------------------------------------------- checks
def check_python() -> None:
    version = sys.version_info
    if version >= (3, 9):
        record(PASS, "python", f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        record(FAIL, "python", f"Python {version.major}.{version.minor} is too old",
               "install Python 3.10 or newer")


def check_dependencies(offline: bool) -> None:
    required = [("yaml", "PyYAML")] + ([] if offline else [("oracledb", "oracledb")])
    optional = [("serial", "pyserial — only for RS-232 analyzers"),
                ("fastapi", "fastapi — only for the browser demo")]
    for module, label in required:
        try:
            __import__(module)
            record(PASS, "deps", f"{label} installed")
        except ImportError:
            record(FAIL, "deps", f"{label} missing", f"pip install {label.split(' ')[0]}")
    for module, label in optional:
        try:
            __import__(module)
            record(PASS, "deps", label.split(" — ")[0] + " installed")
        except ImportError:
            record(WARN, "deps", label.split(" — ")[0] + " not installed", label)


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        record(FAIL, "config", f"{path} not found", "copy config/config.yaml.example → config/config.yaml")
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        record(PASS, "config", f"{path.name} parses")
        return config
    except Exception as exc:
        record(FAIL, "config", f"{path.name} is not valid YAML: {exc}", "fix the indentation")
        return {}


def check_config_values(config: Dict[str, Any], offline: bool) -> None:
    db = config.get("database") or {}
    for key in ("host", "port", "service_name", "username"):
        if not db.get(key):
            record(FAIL, "config", f"database.{key} is empty", "fill it in config/config.yaml")
        else:
            record(PASS, "config", f"database.{key} = {db[key]}")

    password = os.environ.get("LIS_DB_PASSWORD") or str(db.get("password", ""))
    if not password or password in ("CHANGE_ME", ""):
        record(WARN if offline else FAIL, "config", "database password is not set",
               "export LIS_DB_PASSWORD=... (preferred) or set database.password")
    elif str(db.get("password")) == password and password != "CHANGE_ME":
        record(WARN, "config", "password sits in config.yaml in plain text",
               "use the LIS_DB_PASSWORD environment variable")

    analyzers = config.get("analyzers") or []
    enabled = [a for a in analyzers if a.get("enabled", True)]
    if not enabled:
        record(WARN, "analyzers", "no analyzer is enabled", "set enabled: true for the machine you are adding")

    ports: Dict[int, str] = {}
    for analyzer in analyzers:
        name = analyzer.get("name", "?")
        protocol = str(analyzer.get("protocol", "")).upper()
        conn = str(analyzer.get("connection_type", "TCP")).upper()
        if not analyzer.get("enabled", True):
            record(WARN, "analyzers", f"{name} disabled", "enable it at the lab")
            continue
        if protocol not in ("ASTM", "HL7", "AUTO"):
            record(FAIL, "analyzers", f"{name}: protocol {protocol!r} is invalid", "use ASTM, HL7 or AUTO")
        if conn not in ("TCP", "SERIAL"):
            record(FAIL, "analyzers", f"{name}: connection_type {conn!r} is invalid", "use TCP or SERIAL")
        if conn == "TCP":
            port = int(analyzer.get("port", 0))
            if not 1024 <= port <= 65535:
                record(FAIL, "analyzers", f"{name}: port {port} is not usable", "pick e.g. 6661")
            elif port in ports:
                record(FAIL, "analyzers", f"{name}: port {port} already used by {ports[port]}",
                       "give each analyzer its own port")
            else:
                ports[port] = name
                record(PASS, "analyzers", f"{name}: {protocol} on TCP {port}")
        else:
            record(PASS, "analyzers", f"{name}: {protocol} on SERIAL {analyzer.get('serial_port', '?')}")

    code_map = config.get("test_code_map") or {}
    record(PASS if code_map else WARN, "config",
           f"test_code_map has {len(code_map)} entries",
           "" if code_map else "add HGB→HB, GLU→FBS … to match your report codes")


def check_ports_free(config: Dict[str, Any]) -> None:
    """The middleware must be able to bind its ports — or already be running."""
    for analyzer in config.get("analyzers") or []:
        if not analyzer.get("enabled", True):
            continue
        if str(analyzer.get("connection_type", "TCP")).upper() != "TCP":
            continue
        port = int(analyzer.get("port", 0))
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
            record(PASS, "ports", f"port {port} can be opened")
        except OSError as exc:
            # is it *our* middleware already running?
            reachable = _is_open("127.0.0.1", port)
            if reachable:
                record(WARN, "ports", f"port {port} already in use — is the middleware running?",
                       "that is fine if it is; stop it before restarting")
            else:
                record(FAIL, "ports", f"port {port} cannot be opened ({exc})",
                       "another program is using it — netstat -ano | findstr " + str(port))
        finally:
            probe.close()


def _is_open(host: str, port: int, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def check_logs() -> None:
    log_dir = ROOT / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        test = log_dir / ".write-test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        record(PASS, "logs", f"{log_dir} is writable")
    except OSError as exc:
        record(FAIL, "logs", f"cannot write logs: {exc}", "fix folder permissions")


def check_database(config: Dict[str, Any]) -> None:
    db = config.get("database") or {}
    host = str(db.get("host", ""))
    port = int(db.get("port", 1521))
    if not _is_open(host, port, timeout=1.5):
        record(FAIL, "oracle", f"no listener at {host}:{port}",
               "check the DB host/port, the firewall, and that the listener is up")
        return
    record(PASS, "oracle", f"TCP reachable at {host}:{port}")

    try:
        from database.db_connection import OracleDB
    except Exception as exc:
        record(FAIL, "oracle", f"cannot import the Oracle layer: {exc}", "pip install oracledb")
        return

    db_obj = OracleDB(str(ROOT / "config" / "config.yaml"))
    try:
        db_obj.connect()
    except Exception as exc:
        message = str(exc)
        hint = "ORA-01017 → wrong user/password; ORA-12514 → wrong service_name"
        record(FAIL, "oracle", f"connection failed: {message[:120]}", hint)
        return

    record(PASS, "oracle", f"connected as {db_obj.username} to {db_obj.dsn}")
    schema = db_obj.schema
    try:
        with db_obj.cursor() as cur:
            cur.execute("SELECT table_name FROM all_tables WHERE owner = :o", {"o": schema})
            tables = {row[0] for row in cur.fetchall()}
            cur.execute("SELECT view_name FROM all_views WHERE owner = :o", {"o": schema})
            views = {row[0] for row in cur.fetchall()}
        for name in ("LAB_RESULTS", "LIS_RAW_MESSAGES", "LIS_AUDIT_LOG"):
            if name in tables:
                record(PASS, "oracle", f"table {name} exists")
            else:
                record(FAIL, "oracle", f"table {name} missing in {schema}",
                       "run: sqlplus user/pass@//host:port/service @database/schema.sql")
        for name in ("V_SAMPLE_RESULTS", "V_CRITICAL_RESULTS"):
            if name in views:
                record(PASS, "oracle", f"view {name} exists (APEX can use it)")
            else:
                record(WARN, "oracle", f"view {name} missing", "re-run database/schema.sql")
        with db_obj.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.lab_results")
            (count,) = cur.fetchone()
        record(PASS, "oracle", f"lab_results currently holds {count} row(s)")
    except Exception as exc:
        record(FAIL, "oracle", f"schema check failed: {str(exc)[:120]}", "check grants and schema name")
    finally:
        db_obj.close()


def check_apm_notes() -> None:
    """Things a script cannot verify but that ruin the day if forgotten."""
    record(WARN, "manual", "analyzer IP + port must match config.yaml (verify at the machine)",
           "see docs/LAB_DAY.md → machine settings table")
    record(WARN, "manual", "firewall must allow the analyzer→middleware port",
           'New-NetFirewallRule -DisplayName "LIS" -Direction Inbound -LocalPort 6661 -Protocol TCP -Action Allow')


# ---------------------------------------------------------------- report
def print_report() -> int:
    order = {FAIL: 0, WARN: 1, PASS: 2}
    results.sort(key=lambda r: (order[r[0]], r[1]))
    width = max((len(r[2]) for r in results), default=10)
    print("=" * (width + 22))
    print("PREFLIGHT — LIS middleware")
    print("=" * (width + 22))
    for status, area, message, fix in results:
        icon = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}[status]
        print(f"{icon} [{area:<9}] {message}")
        if fix and status != PASS:
            print(f"                → {fix}")
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    print("-" * (width + 22))
    print(f"{sum(1 for r in results if r[0] == PASS)} pass · {warns} warning(s) · {fails} failure(s)")
    if fails:
        print("\nFix the ❌ items before going to the lab.")
        return 1
    if warns:
        print("\nOnly warnings left — you can go, but read them first.")
        return 2
    print("\nAll clear. At the lab you only need to point the machine at this host.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-flight checks for the LIS middleware")
    parser.add_argument("--config", default=str(ROOT / "config" / "config.yaml"))
    parser.add_argument("--offline", action="store_true", help="skip the Oracle checks")
    parser.add_argument("--json", metavar="FILE", help="also write the report as JSON")
    args = parser.parse_args()

    check_python()
    check_dependencies(args.offline)
    config = load_config(Path(args.config))
    if config:
        check_config_values(config, args.offline)
        check_ports_free(config)
        if not args.offline:
            check_database(config)
        else:
            record(WARN, "oracle", "database checks skipped (--offline)", "run again without --offline")
    check_logs()
    check_apm_notes()

    if args.json:
        Path(args.json).write_text(json.dumps(
            [{"status": s, "area": a, "message": m, "fix": f} for s, a, m, f in results], indent=2),
            encoding="utf-8")

    code = print_report()
    return code


if __name__ == "__main__":
    sys.exit(main())
