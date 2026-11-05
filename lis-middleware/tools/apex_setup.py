"""
apex_setup.py — এক কমান্ডে আপনার নিজের Oracle + APEX-এ সব তৈরি ও যাচাই।

আপনার ল্যাপটপে যেহেতু APEX আর 19c আগেই আছে, এটাই দরকার:

    python tools/apex_setup.py --user lis_user --service XEPDB1
    (পাসওয়ার্ড জিজ্ঞেস করবে, বা LIS_DB_PASSWORD / --password)

কী করে (ক্রম অনুযায়ী, প্রতিটা ধাপ আবার চালানো নিরাপদ):

  1. কানেকশন যাচাই (thin mode — Oracle Client লাগে না)
  2. database/schema.sql        → lab_results, lis_raw_messages, lis_audit_log, ভিউ
  3. database/apex_install.sql  → lis_config, lis_commands, lis_analyzer_status,
                                  v_lis_* ভিউ, version trigger, ডেমো ডেটা
  4. সব object আবার পড়ে যাচাই + রিপোর্ট
  5. APEX-এ কী কী পেজ বানাতে হবে, ছাপিয়ে দেখায়

যা চালাতে পারে না (ইচ্ছাকৃত): APEX পেজ নিজে বানানো — সেটা APEX Builder-এ
ক্লিক করে করতে হয়, কারণ `.sql` দিয়ে APEX পেজ ইমপোর্ট হয় না। স্ক্রিপ্ট চলে
শেষে ধাপগুলো ছাপিয়ে দেয়।

আরও:

    --create-user     lis_user না থাকলে বানিয়ে দেয় (admin ক্রেডেনশিয়াল লাগবে)
    --write-config    config/config.yaml-এ ডেটাবেসের ঠিকানা লিখে দেয়
    --sql-only        কিছু চালায় না, শুধু কতগুলো statement চালাত দেখায়
    --drop            কন্ট্রোল টেবিলগুলো (lis_*) মুছে নতুন করে বসায় (সতর্ক!)
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "database" / "schema.sql"
APEX_SQL = ROOT / "database" / "apex_install.sql"

SKIP_PREFIXES = ("PROMPT", "SET ", "SPOOL", "WHENEVER", "COLUMN ", "CLEAR ", "PAUSE")
# errors that mean "already installed" — safe to ignore on a re-run
IGNORABLE = ("ORA-00955",   # object already exists
             "ORA-01430",   # column already exists (we add conditionally now)
             "ORA-02260",   # only one primary key
             "ORA-02275",   # constraint already exists
             "ORA-04081",   # trigger already exists
             "ORA-01408",   # index on these columns exists
             "ORA-00001")   # duplicate seed row


# ---------------------------------------------------------------- SQL parsing
def split_statements(sql: str) -> List[str]:
    """Split a SQL*Plus script into units that can run one by one.

    The rules live in `tools/install_sql.py` (line based) and are shared with
    the SQL*Plus runner, so both tools always agree on where a statement ends.
    PL/SQL blocks come back without their closing `/`.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.install_sql import split_blocks

    return [text for text, _is_plsql in split_blocks(sql) if _runnable(text)]


def _runnable(statement: str) -> bool:
    stripped = statement.strip()
    if not stripped:
        return False
    first = stripped.split()[0].upper()
    if first in ("PROMPT", "SET", "SPOOL", "WHENEVER", "COLUMN", "CLEAR", "PAUSE", "COMMIT"):
        # COMMIT is harmless to skip: each statement is committed right after it
        return False
    if stripped.startswith("--"):
        return False
    return True


def preview(statement: str, width: int = 72) -> str:
    flat = " ".join(statement.split())
    return flat if len(flat) <= width else flat[:width - 1] + "…"


# ------------------------------------------------------------------- running
class ScriptFailed(RuntimeError):
    """A statement failed; run_script already printed which one."""


def run_script(cursor, path: Path, label: str, quiet: bool = False) -> Tuple[int, int]:
    """Execute every statement; returns (ok, skipped_already_exists)."""
    statements = split_statements(path.read_text(encoding="utf-8"))
    ok = skipped = 0
    for number, statement in enumerate(statements, start=1):
        try:
            cursor.execute(statement)
            ok += 1
            if not quiet:
                print(f"   ✓ {number:2d}. {preview(statement)}")
        except Exception as exc:                      # noqa: BLE001 - driver errors vary
            message = str(exc)
            head = message.split(":")[0]
            if any(code in message for code in IGNORABLE):
                skipped += 1
                if not quiet:
                    print(f"   • {number:2d}. already there — {head}")
                continue
            print(f"\n❌ {label}: statement {number} failed\n   {preview(statement, 200)}")
            print(f"   {message.splitlines()[0]}")
            print("   এখানেই থামলাম — উপরেরটা ঠিক করে আবার চালালেই বাকিগুলো চলবে।")
            raise ScriptFailed(f"{label} #{number}: {head}") from None
    return ok, skipped


def connect(args):
    import oracledb

    dsn = args.dsn or f"{args.host}:{args.port}/{args.service}"
    password = args.password or os.environ.get("LIS_DB_PASSWORD") or ""
    if not password:
        password = getpass.getpass(f"পাসওয়ার্ড ({args.user}@{dsn}): ")
    print(f"→ কানেক্ট করছি {args.user}@{dsn}")
    connection = oracledb.connect(user=args.user, password=password, dsn=dsn)
    with connection.cursor() as cur:
        cur.execute("SELECT SYS_CONTEXT('USERENV','DB_NAME'), SYS_CONTEXT('USERENV','CON_NAME') "
                    "FROM dual")
        db_name, container = cur.fetchone()
        cur.execute("SELECT version_full FROM product_component_version "
                    "WHERE product LIKE 'Oracle Database%' FETCH FIRST 1 ROWS ONLY")
        row = cur.fetchone()
        version = row[0] if row else "?"
    print(f"✅ connected — {db_name}/{container}, Oracle {version}")
    return connection, dsn, version


def create_user(args, dsn: str, version: str) -> None:
    """Create lis_user in the current container (run as a DBA)."""
    import oracledb

    admin_user = args.admin_user or "sys"
    admin_password = args.admin_password or getpass.getpass(f"{admin_user} পাসওয়ার্ড: ")
    print(f"→ {admin_user} দিয়ে কানেক্ট করছি (নতুন ইউজার বানাতে)")
    kwargs = {"user": admin_user, "password": admin_password, "dsn": dsn}
    if admin_user.lower() == "sys":
        kwargs["mode"] = oracledb.AUTH_MODE_SYSDBA
    conn = oracledb.connect(**kwargs)
    password = args.password or os.environ.get("LIS_DB_PASSWORD") or getpass.getpass(
        "নতুন ইউজারের পাসওয়ার্ড দিতে চান: ")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM all_users WHERE username = :u", {"u": args.user.upper()})
        if cur.fetchone()[0]:
            print(f"• ইউজার {args.user} আগেই আছে — গ্রান্ট ঠিক করছি")
            cur.execute(f"GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE TRIGGER, "
                        f"CREATE SEQUENCE, CREATE PROCEDURE TO {args.user}")
        else:
            cur.execute(f'CREATE USER {args.user} IDENTIFIED BY "{password}"')
            # RESOURCE alone has no CREATE VIEW — v_lis_* would fail with ORA-01031
            cur.execute(f"GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE TRIGGER, "
                        f"CREATE SEQUENCE, CREATE PROCEDURE TO {args.user}")
            cur.execute(f"ALTER USER {args.user} QUOTA UNLIMITED ON USERS")
            cur.execute(f"ALTER USER {args.user} DEFAULT TABLESPACE USERS")
            print(f"✅ ইউজার {args.user} তৈরি হয়েছে")
    conn.commit()
    conn.close()
    print("ℹ️  APEX-এ এই ইউজারকে workspace-এ যোগ করতে হবে (Workspace → Manage Users → Create User)।")
    print("    অথবা APEX-কে লগইন করতে বলুন — তাতে সব ল্যাব ইউজার একই ডেটাবেসে কাজ করবে।")


# ------------------------------------------------------------------ verifying
def apex_release(cursor) -> str:
    """Which APEX is installed here? (APEX_RELEASE is a public view.)"""
    for query in ("SELECT version_no FROM apex_release",
                  "SELECT version_no FROM APEX_050000.apex_release"):
        try:
            cursor.execute(query)
            row = cursor.fetchone()
            if row:
                return str(row[0])
        except Exception:
            continue
    return ""


def verify(cursor) -> bool:
    checks: List[Tuple[str, str, bool]] = []

    release = apex_release(cursor)
    if release:
        print(f"   ✓ APEX {release} এই ডেটাবেসেই আছে")
    else:
        print("   ⚠️  APEX পাওয়া গেল না এই স্কিমায় — অন্য PDB-তে থাকতে পারে "
              "(APEX চালানোর জন্য দরকার, টেবিলের জন্য নয়)")

    def has(object_type: str, name: str) -> bool:
        cursor.execute(f"SELECT COUNT(*) FROM all_objects WHERE object_name = :n "
                       f"AND object_type = :t", {"n": name.upper(), "t": object_type})
        return bool(cursor.fetchone()[0])

    for table in ("LAB_RESULTS", "LIS_RAW_MESSAGES", "LIS_AUDIT_LOG", "LIS_CONFIG",
                  "LIS_TEST_CODE_MAP", "LIS_COMMANDS", "LIS_ANALYZER_STATUS"):
        checks.append(("TABLE", table, has("TABLE", table)))
    for view in ("V_SAMPLE_RESULTS", "V_CRITICAL_RESULTS", "V_LIS_ANALYZERS",
                 "V_LIS_SETTINGS", "V_LIS_HEALTH", "V_LIS_COMMAND_LOG"):
        checks.append(("VIEW", view, has("VIEW", view)))
    checks.append(("TRIGGER", "TRG_LIS_CONFIG_TOUCH", has("TRIGGER", "TRG_LIS_CONFIG_TOUCH")))

    failed = 0
    for kind, name, present in checks:
        print(f"   {'✓' if present else '✗'} {kind:8s} {name}")
        failed += 0 if present else 1

    try:
        cursor.execute("SELECT COUNT(*) FROM lis_config WHERE config_type = 'ANALYZER'")
        analyzers = cursor.fetchone()[0]
        cursor.execute("SELECT analyzer_name, protocol, port FROM lis_config "
                       "WHERE config_type = 'ANALYZER' ORDER BY display_order")
        rows = cursor.fetchall()
        print(f"\n   {analyzers} অ্যানালাইজার:")
        for name, protocol, port in rows:
            print(f"     · {name:22s} {protocol:5s} port {port}")
    except Exception as exc:                          # noqa: BLE001
        print(f"   ⚠️  lis_config পড়া গেল না: {exc}")
        failed += 1

    try:
        cursor.execute("SELECT COUNT(*) FROM lis_test_code_map")
        print(f"   {cursor.fetchone()[0]} টেস্ট কোড ম্যাপ")
    except Exception:
        pass
    try:
        cursor.execute("SELECT COUNT(*) FROM lis_config WHERE last_error IS NOT NULL")
        errors = cursor.fetchone()[0]
        if errors:
            print(f"   ⚠️  {errors} রো-তে last_error আছে — APEX-এ লাল দেখাবে")
    except Exception:
        pass

    try:
        cursor.execute("SELECT owner, object_name FROM all_objects WHERE object_name = 'LIS_CONFIG' "
                       "AND object_type = 'TABLE'")
        rows = cursor.fetchall()
        if rows:
            print(f"   স্কিমা: {rows[0][0]}")
    except Exception:
        pass

    return failed == 0


def write_config(args, dsn: str, version: str) -> None:
    target = ROOT / "config" / "config.yaml"
    host, port, service = args.host, args.port, args.service
    if args.dsn:
        match = re.match(r"^(?P<host>[^:/]+):(?P<port>\d+)/(?P<service>.+)$", args.dsn)
        if match:
            host, port, service = match["host"], int(match["port"]), match["service"]
    text = f"""\
# এই ফাইলে শুধু ডেটাবেসের ঠিকানা। পাসওয়ার্ড এখানে নয় — LIS_DB_PASSWORD
# environment variable-এ রাখুন (ops/ ফোল্ডারে সার্ভিস ফাইল দেখুন)।
# অ্যানালাইজার, পোর্ট, প্রোটোকল, টেস্ট কোড ম্যাপ — সব APEX থেকে, ডেটাবেসে।
database:
  host: "{host}"
  port: {port}
  service_name: "{service}"
  username: "{args.user}"
  schema: "{args.user.upper()}"
  pool:
    min: 2
    max: 10
    increment: 1

app:
  ack_mode: "ASTM"
  default_protocol: "AUTO"
  log_level: "INFO"
  log_file: "logs/lis_service.log"

# ব্যাকআপ (classic) মোডে চালালে এই তালিকা ব্যবহার হয়; APEX মোডে ডেটাবেসই সত্য
analyzers:
  - name: "Sysmex_XN_1000"
    protocol: "ASTM"
    connection_type: "TCP"
    host: "0.0.0.0"
    port: 6661
    enabled: true
  - name: "Mindray_BS240"
    protocol: "HL7"
    connection_type: "TCP"
    host: "0.0.0.0"
    port: 6662
    enabled: true

test_code_map: {{}}
"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(".yaml.bak")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"• পুরোনো config সংরক্ষিত: {backup.name}")
    target.write_text(text, encoding="utf-8")
    print(f"✅ লিখলাম {target.relative_to(ROOT)}  (Oracle {version}, {host}:{port}/{service})")


APEX_STEPS = """
────────────────────────────────────────────────────────────────────────
এখন APEX-এ (App Builder) — প্রতিটি ২-৩ মিনিটের কাজ:

 1. নতুন অ্যাপ: Create → New Application → নাম "LIS" → Create
 2. পেজ ২ — অ্যানালাইজার (Interactive Report)
      Create Page → Interactive Report → source: SQL Query
      SELECT * FROM v_lis_analyzers
 3. পেজ ৩ — অ্যানালাইজার ফর্ম (এটাই আসল "কনফিগ")
      Create Page → Form → Form on a Table → Table: LIS_CONFIG
      Primary Key: CONFIG_ID,  Define "Analyzer Name = :P3_ANALYZER_NAME"
      Edit link পেজ ২ থেকে পেজ ৩-তে বসান
 4. পেজ ৪ — সেটিংস: Interactive Report on v_lis_settings + Form on lis_config
 5. পেজ ৫ — কোড ম্যাপ: Interactive Report/Form on lis_test_code_map
 6. পেজ ৬ — বাটন: Form on lis_commands (শুধু ৩ কলাম দেখান:
      command_type (Select List: RELOAD,START,STOP,RESTART,SELFTEST,PING),
      analyzer_name, payload)
      অথবা বাটন-প্রসেস: INSERT INTO lis_commands (command_type, analyzer_name)
                          VALUES ('RELOAD', NULL);
 7. পেজ ৭ — ড্যাশবোর্ড: Classic Report on v_lis_health (auto-refresh 10s)

তারপর সার্ভারে:      python service.py
আর APEX-এ ফর্ম খুলে পোর্ট বদলে Save → ~৫ সেকেন্ডে কার্যকর।

বিস্তারিত (প্রতিটি ফিল্ডের নিয়ম, DRAFT→ACTIVE কেন): docs/APEX_CONTROL.md
────────────────────────────────────────────────────────────────────────
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Oracle + APEX-এ LIS middleware-এর সব object তৈরি ও যাচাই")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1521)
    parser.add_argument("--service", default="XEPDB1",
                        help="PDB service name (XE: XEPDB1, 19c: ORCLPDB1)")
    parser.add_argument("--dsn", help="পুরো DSN একসাথে: host:port/service")
    parser.add_argument("--user", default="lis_user")
    parser.add_argument("--password", help="না দিলে LIS_DB_PASSWORD বা প্রম্পট")
    parser.add_argument("--create-user", action="store_true",
                        help="lis_user না থাকলে বানাবে (admin লাগবে)")
    parser.add_argument("--admin-user", default="sys")
    parser.add_argument("--admin-password")
    parser.add_argument("--write-config", action="store_true",
                        help="config/config.yaml-এ এই ডেটাবেসের ঠিকানা লেখে")
    parser.add_argument("--drop", action="store_true",
                        help="lis_* টেবিল/ভিউ/ট্রিগার আগে মুছে ফেলে (ডেটা হারাবে)")
    parser.add_argument("--sql-only", action="store_true",
                        help="ডেটাবেসে কিছু চালায় না, শুধু statement গুনে দেখায়")
    parser.add_argument("--verify-only", action="store_true",
                        help="কিছু তৈরি করবে না, শুধু object গুলো যাচাই করবে")
    parser.add_argument("--skip-schema", action="store_true")
    parser.add_argument("--skip-apex", action="store_true")
    args = parser.parse_args()

    for path in (SCHEMA_SQL, APEX_SQL):
        if not path.exists():
            print(f"❌ {path} পাওয়া গেল না")
            return 1

    if args.sql_only:
        for path in (SCHEMA_SQL, APEX_SQL):
            statements = split_statements(path.read_text(encoding="utf-8"))
            print(f"{path.name}: {len(statements)} statement")
            for statement in statements[:4]:
                print(f"   · {preview(statement)}")
            if len(statements) > 4:
                print("   …")
        print("\n(কিছুই চালানো হয়নি — --sql-only)")
        return 0

    try:
        import oracledb  # noqa: F401
    except ImportError:
        print("❌ python-oracledb নেই — চালান:  pip install oracledb")
        return 1

    dsn = args.dsn or f"{args.host}:{args.port}/{args.service}"

    if args.create_user:
        try:
            create_user(args, dsn, "?")
        except Exception as exc:                       # noqa: BLE001
            print(f"⚠️  ইউজার বানানো যায়নি: {str(exc).splitlines()[0]}")
            print("   (ইতিমধ্যে থাকলে এটা স্বাভাবিক — পরের ধাপ চলবে)")

    try:
        connection, dsn, version = connect(args)
    except Exception as exc:                           # noqa: BLE001
        print(f"\n❌ কানেক্ট করা গেল না: {str(exc).splitlines()[0]}")
        print("\nসাধারণ কারণগুলো:")
        print("  (নিচের কোড মূল এররের ভেতরেও থাকতে পারে — DPY-6005 মানে কানেকশনই হয়নি)")
        print("  ORA-01017  → ইউজার/পাসওয়ার্ড ভুল")
        print("  ORA-12514  → service name ভুল (XE: XEPDB1, 19c: ORCLPDB1)")
        print("  ORA-12541  → listener চলছে না (lsnrctl status)")
        print("  DPY-6005 / timeout → host/port ভুল, ফায়ারওয়াল")
        return 1

    failed = 0
    try:
        with connection.cursor() as cursor:
            if args.drop:
                print("→ পুরোনো কন্ট্রোল object সরাচ্ছি (--drop)")
                drops = [
                    "DROP TRIGGER trg_lis_config_touch",
                    "DROP TRIGGER trg_lis_code_map_bump",
                    "DROP VIEW v_lis_health", "DROP VIEW v_lis_command_log",
                    "DROP VIEW v_lis_settings", "DROP VIEW v_lis_analyzers",
                    "DROP TABLE lis_commands", "DROP TABLE lis_analyzer_status",
                    "DROP TABLE lis_test_code_map", "DROP TABLE lis_config",
                ]
                for statement in drops:
                    try:
                        cursor.execute(statement)
                        print(f"   • {statement}")
                    except Exception:
                        pass
                connection.commit()

            if args.verify_only:
                print("→ শুধু যাচাই (--verify-only)")
            if not args.skip_schema and not args.verify_only:
                print(f"\n→ {SCHEMA_SQL.name}")
                run_script(cursor, SCHEMA_SQL, "schema.sql")
                connection.commit()

            if not args.skip_apex and not args.verify_only:
                print(f"\n→ {APEX_SQL.name}")
                run_script(cursor, APEX_SQL, "apex_install.sql")
                connection.commit()

            print("\n→ যাচাই করছি")
            failed = 0 if verify(cursor) else 1
    except ScriptFailed:
        print("\nকিছু object আগেই তৈরি হয়ে গেছে — ঠিক করার পর পুরোটা আবার চালাতে দ্বিধা করবেন না।")
        print('হাতে দেখতে চাইলে:  python tools/install_sql.py --sqlplus "sqlplus -S user/pass@db"')
        return 1
    finally:
        try:
            connection.close()
        except Exception:
            pass

    if args.write_config:
        print()
        write_config(args, dsn, version)

    print(APEX_STEPS)
    if failed:
        print("⚠️  উপরে ✗ চিহ্ন আছে — script দুটো আবার চালান, বা লগ দেখুন।")
        return 1
    print("✅ সব object তৈরি আছে।")
    return 0


if __name__ == "__main__":
    sys.exit(main())
