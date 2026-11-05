"""
install_sql.py — SQL*Plus দিয়ে schema.sql আর apex_install.sql চালায়।

কেন আলাদা স্ক্রিপ্ট: `sqlplus` নিজেই `/` (PL/SQL ব্লক) আর `;` (statement)
বুঝে, তাই ওটাই সবচেয়ে নির্ভরযোগ্য। কিন্তু `sqlplus ... @file` চালালে প্রথম
এররেই চুপচাপ থেমে যায় এবং কোন statement-এ আটকেছে বোঝা কষ্ট। এই স্ক্রিপ্ট:

  * ট্রিগার/ভিউ/INSERT সব আলাদা ব্লকে ভাগ করে চালায়,
  * যেখানে `ERROR` আসে সেখানেই থামে এবং **সেই statement-টা দেখায়**,
  * `ORA-00955` (আগেই আছে) জাতীয় এরর উপেক্ষা করে, যাতে আবার চালানো নিরাপদ হয়,
  * প্রতিটা ধাপের সারসংক্ষেপ দেয়।

ব্যবহার (যেকোনো একটা):

  python tools/install_sql.py --sqlplus "sqlplus -S lis_user/pass@//localhost:1521/XEPDB1"
  python tools/install_sql.py --sqlplus "sqlplus -S / as sysdba" --target "lis_user/pass@XEPDB1"
  python tools/install_sql.py --sqlplus "sqlplus"            # নিজে ইউজার/পাস জিজ্ঞেস করবে
  python tools/install_sql.py --sqlplus "sqlplus -S lis/pw@db" --file database/schema.sql
  python tools/install_sql.py --sqlplus "..." --dry-run      # শুধু দেখাবে, চালাবে না

sqlplus না থাকলে:  python tools/apex_setup.py   (python-oracledb দিয়ে একই কাজ)
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [ROOT / "database" / "schema.sql", ROOT / "database" / "apex_install.sql"]

# "already installed" — expected when the script is run a second time
IGNORABLE = ("ORA-00955", "ORA-01430", "ORA-01408", "ORA-04081", "ORA-02260",
             "ORA-02275", "ORA-04043")
# worth showing but not fatal on a re-run: duplicate seed rows, a missing
# object the next statement creates
WARNINGS = ("ORA-00001", "ORA-00942")


def split_blocks(sql: str) -> List[Tuple[str, bool]]:
    """Split into units sqlplus can run.

    Returns (text, is_plsql) pairs. Plain SQL ends with `;`; a PL/SQL block
    ends with `END;` and needs a `/` line of its own to be executed.
    """
    blocks: List[Tuple[str, bool]] = []
    buffer: List[str] = []
    in_string = False
    plsql = False

    for line in sql.split("\n"):
        stripped = line.strip()
        if not in_string and (stripped.startswith("--") or stripped.startswith(("PROMPT", "SET ", "SPOOL ",
                                                                              "WHENEVER "))):
            continue
        if stripped == "/" and not plsql and not buffer:
            continue
        buffer.append(line)
        text = "\n".join(buffer)
        if not in_string:
            words = [w.upper() for w in text.split()[:6]]
            first = words[0] if words else ""
            two = " ".join(words[:2])
            three = " ".join(words[:3])
            four = " ".join(words[:4])
            is_block = (
                first in ("BEGIN", "DECLARE")
                or two.startswith("CREATE TRIGGER")
                or three.startswith("CREATE OR REPLACE") and len(words) > 3
                   and words[3] in ("TRIGGER", "PROCEDURE", "FUNCTION", "PACKAGE", "TYPE")
                or four.startswith("CREATE OR REPLACE TRIGGER")
            )
            if not plsql and is_block:
                plsql = True
        if line.count("'") % 2:
            in_string = not in_string
        if in_string:
            continue

        if stripped == "/" and buffer:
            # a lone slash always ends a unit — never let it be swallowed into
            # the next statement if the head detection guessed wrong
            blocks.append(("\n".join(buffer[:-1]).strip(), plsql))
            buffer, plsql = [], False
        elif plsql and stripped == "/":
            blocks.append(("\n".join(buffer[:-1]).strip(), True))
            buffer, plsql = [], False
        elif not plsql and stripped.endswith(";"):
            blocks.append((text.strip(), False))
            buffer = []

    if "".join(buffer).strip():
        blocks.append(("\n".join(buffer).strip(), plsql))
    return [(text, is_plsql) for text, is_plsql in blocks if text.strip()]


def run_block(command: List[str], block: str, is_plsql: bool, login: str) -> "subprocess.CompletedProcess":
    """Run one unit through sqlplus. sqlplus reports ORA- errors on stdout."""
    payload = block if is_plsql else block.rstrip(";")
    script = f"WHENEVER SQLERROR CONTINUE\n{payload}\n/" + ("\n" if is_plsql else "\n/\n") + "EXIT;\n"
    if not is_plsql:
        # a terminated SQL statement is already executed; the trailing `/` would
        # run it a second time (ORA-00955), so drop it
        script = f"WHENEVER SQLERROR CONTINUE\n{payload};\nEXIT;\n"
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        return subprocess.run(command + ["-S", login, f"@{path}"], text=True,
                              capture_output=True,
                              env={**os.environ, "NLS_LANG": ".AL32UTF8"})
    finally:
        os.unlink(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the LIS SQL scripts through SQL*Plus and report the failing statement")
    parser.add_argument("--sqlplus", default="sqlplus",
                        help='কমান্ড, যেমন "sqlplus -S lis/pw@//host:1521/XEPDB1"')
    parser.add_argument("--target", help='user/pass@dsn — --sqlplus-এ লগইন না থাকলে')
    parser.add_argument("--user", help="টার্গেট ইউজার")
    parser.add_argument("--password", help="না দিলে LIS_DB_PASSWORD বা প্রম্পট")
    parser.add_argument("--dsn", help="host:port/service (CONNECT-এ ব্যবহৃত হবে)")
    parser.add_argument("--file", action="append", help="শুধু এই ফাইল চালাও (বারবার দেওয়া যায়)")
    parser.add_argument("--dry-run", action="store_true", help="চালাবে না, ব্লকগুলো গুনে দেখাবে")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="প্রথম আসল এররেই থামো (ডিফল্ট: বাকিগুলো চালিয়ে শেষে রিপোর্ট)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    command = shlex.split(args.sqlplus)
    if command[0] in ("sqlplus",) and not shutil.which(command[0]):
        print("❌ sqlplus পাওয়া গেল না (Oracle Client / Instant Client-এর PATH দেখুন)")
        print("   বিকল্প:  python tools/apex_setup.py     (python-oracledb, Oracle Client লাগে না)")
        return 2

    login = ""
    if len(command) > 1 and ("@" in args.sqlplus or "/" in command[-1]):
        login = command[-1]
        command = command[:-1]
    elif args.target:
        login = args.target
    elif args.user:
        password = (args.password or os.environ.get("LIS_DB_PASSWORD")
                    or input(f"{args.user} পাসওয়ার্ড: "))
        login = f"{args.user}/{password}" + (f"@{args.dsn}" if args.dsn else "")
    if not login:
        print("ℹ️  লগইন তথ্য দেওয়া হয়নি — sqlplus নিজেই জিজ্ঞেস করবে (SQL> CONNECT লাগবে)")
        print('   ভালো উপায়:  --sqlplus "sqlplus -S lis_user/pass@//localhost:1521/XEPDB1"')
        return 2

    files = [Path(f) for f in args.file] if args.file else SCRIPTS
    total_ok = total_skipped = 0
    warnings = 0
    failures = []

    for path in files:
        if not path.exists():
            print(f"❌ {path} নেই")
            return 1
        blocks = split_blocks(path.read_text(encoding="utf-8"))
        print(f"\n→ {path.name}  ({len(blocks)} block)")
        if args.dry_run:
            for number, (block, _) in enumerate(blocks, start=1):
                print(f"   {number:2d}. {' '.join(block.split())[:78]}")
            continue

        for number, (block, is_plsql) in enumerate(blocks, start=1):
            result = run_block(command, block, is_plsql, login)
            output = (result.stdout or "") + (result.stderr or "")
            first = " ".join(block.split())[:90]
            if "ORA-" in output or result.returncode not in (0, None):
                error = next((line for line in output.splitlines() if "ORA-" in line), "")
                if any(code in output for code in IGNORABLE):
                    total_skipped += 1
                    continue
                if any(code in output for code in WARNINGS):
                    warnings += 1
                    print(f"   ⚠️  {number:2d}. {first[:60]}\n        {error.strip()[:100]}")
                    continue
                print(f"   ✗ {number:2d}. {first}")
                print(f"        {error.strip()}")
                failures.append((path.name, number, block, error.strip()))
                if args.stop_on_error:
                    break
                continue
            total_ok += 1
            if args.verbose:
                print(f"   ✓ {number:2d}. {first}")

    if args.dry_run:
        print("\n(কিছু চালানো হয়নি — --dry-run)")
        return 0

    print(f"\n{total_ok} block চালানো হল · {total_skipped} আগেই ছিল · "
          f"{warnings} সতর্কতা · {len(failures)} ব্যর্থ")
    if failures:
        print("\n──────────── ব্যর্থ statement (কপি করে SQL Developer-এ চালান) ────────────")
        for name, number, block, error in failures:
            print(f"\n[{name} #{number}] {error}\n{block.strip()}\n")
        return 1

    print("\n✅ শেষ। এখন:")
    print("   python tools/apex_setup.py --verify-only     # object গুলো যাচাই (sqlplus ছাড়াই)")
    print("   APEX-এ পেজ বানান — docs/APEX_CONTROL.md §2")
    print("   python service.py                           # APEX-চালিত মোড")
    return 0


if __name__ == "__main__":
    sys.exit(main())
