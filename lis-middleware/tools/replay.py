"""
Replay raw messages from Oracle (or from a file) back through the pipeline.

When a parser is fixed, the messages it used to choke on are sitting in
``lis_raw_messages``. This tool re-parses them so the results land in
``lab_results`` without asking the lab to re-run every sample.

    # see what is broken, change nothing
    python tools/replay.py --status ERROR --limit 50 --dry-run

    # actually re-process the last 50 failed messages
    python tools/replay.py --status ERROR --limit 50
"""

from __future__ import annotations

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ack_handler import ACKHandler      # noqa: E402
from core.engine import IngestEngine         # noqa: E402
from core.validator import ResultValidator   # noqa: E402


def fetch_messages(db, schema: str, status: str, limit: int):
    sql = f"""
        SELECT message_id, analyzer_name, protocol, raw_message
          FROM {schema}.lis_raw_messages
         WHERE status = :status
         ORDER BY received_at
         FETCH FIRST :limit ROWS ONLY
    """
    with db.cursor() as cur:
        cur.execute(sql, {"status": status, "limit": limit})
        return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay raw messages through the pipeline")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--status", default="ERROR", help="only rows with this status")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="parse but do not insert")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    schema = str((config.get("database") or {}).get("schema", "LIS_USER")).upper()

    from database.db_connection import OracleDB
    from database.db_writer import DBWriter

    db = OracleDB(args.config)
    db.connect()
    try:
        rows = fetch_messages(db, schema, args.status, args.limit)
        print(f"{len(rows)} message(s) with status {args.status}")

        store = DBWriter(db, schema)
        engine = IngestEngine(
            store=store,
            validator=ResultValidator(),
            ack_handler=ACKHandler("ASTM"),
            test_code_map=config.get("test_code_map", {}),
        )

        ok = failed = 0
        for message_id, analyzer_name, protocol, raw in rows:
            raw_text = raw.read() if hasattr(raw, "read") else str(raw)
            if args.dry_run:
                from core.parser_factory import parse_message
                parsed = parse_message(raw_text, protocol or "AUTO", analyzer_name)
                print(f"  #{message_id} {analyzer_name}: {len(parsed)} result(s)")
                continue
            response = engine.process(raw_text, {"name": analyzer_name, "protocol": protocol or "AUTO"},
                                      peer=f"replay:{message_id}")
            if response["ok"]:
                ok += 1
                print(f"  #{message_id} {analyzer_name}: {response['stored']} stored")
            else:
                failed += 1
                print(f"  #{message_id} {analyzer_name}: {response['note']}")

        if not args.dry_run:
            print(f"\nreplayed: {ok} ok, {failed} still failing")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
