"""
replay_capture — replay a recorded analyzer conversation against the middleware.

Feed it the file produced by ``tools/sniff_com.py``:

    # count what is inside, no traffic sent
    python tools/replay_capture.py logs/capture.jsonl --dry-run

    # replay every device chunk to a locally running middleware, honouring the
    # recorded timing (delays between chunks) and reading the replies
    python tools/replay_capture.py logs/capture.jsonl --port 6661 --timing

    # replay a single session only, as fast as possible
    python tools/replay_capture.py logs/capture.jsonl --port 6661 --session 2

This is how you regression-test the parser against real machine data without
touching the analyzer: capture once, replay forever.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

CONTROL = {0x05: "<ENQ>", 0x06: "<ACK>", 0x15: "<NAK>", 0x02: "<STX>",
           0x03: "<ETX>", 0x04: "<EOT>", 0x0B: "<VT>", 0x1C: "<FS>",
           0x0D: "<CR>", 0x0A: "<LF>"}


def load(path: Path) -> List[Dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def printable(data: bytes, limit: int = 140) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else CONTROL.get(b, f"\\x{b:02x}") for b in data)
    return text if len(text) <= limit else text[:limit] + "…"


def split_sessions(records: List[Dict]) -> List[List[Dict]]:
    """A new session starts when the device sends ENQ (or after a long gap)."""
    sessions: List[List[Dict]] = []
    current: List[Dict] = []
    last_ts = 0.0
    for record in records:
        data = bytes.fromhex(record["hex"])
        stamp = datetime.fromisoformat(record["ts"]).timestamp()
        new_session = record["dir"] == "device" and 0x05 in data[:1]
        if current and (new_session or stamp - last_ts > 30):
            sessions.append(current)
            current = []
        current.append(record)
        last_ts = stamp
    if current:
        sessions.append(current)
    return sessions


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a captured analyzer session")
    parser.add_argument("capture", help="path to capture.jsonl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6661)
    parser.add_argument("--session", type=int, help="only this session number (1-based)")
    parser.add_argument("--timing", action="store_true", help="honour recorded delays")
    parser.add_argument("--dry-run", action="store_true", help="print the payloads, send nothing")
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    args = parser.parse_args()

    path = Path(args.capture)
    if not path.exists():
        sys.exit(f"no such capture file: {path}")
    records = load(path)
    sessions = split_sessions(records)

    if args.list:
        for index, session in enumerate(sessions, start=1):
            device_bytes = sum(len(bytes.fromhex(r["hex"])) for r in session if r["dir"] == "device")
            print(f"session {index}: {len(session)} chunks, {device_bytes} bytes from the device")
        return 0

    chosen = sessions
    if args.session:
        if not 1 <= args.session <= len(sessions):
            sys.exit(f"session {args.session} not in capture (1..{len(sessions)})")
        chosen = [sessions[args.session - 1]]

    total_ok = 0
    for index, session in enumerate(chosen, start=1):
        print(f"\n--- session {index} ({len(session)} chunks) ---")
        sock = None
        if not args.dry_run:
            sock = socket.create_connection((args.host, args.port), timeout=10)
        previous = None
        try:
            for record in session:
                data = bytes.fromhex(record["hex"])
                stamp = datetime.fromisoformat(record["ts"]).timestamp()
                if args.timing and previous:
                    time.sleep(max(0.0, min(5.0, stamp - previous)))
                previous = stamp

                if record["dir"] != "device":
                    continue                        # host replies are re-read live
                print(f"  → {printable(data)}")
                if args.dry_run:
                    continue
                sock.sendall(data)
                time.sleep(0.05)
                try:
                    reply = sock.recv(4096)
                except socket.timeout:
                    reply = b""
                if reply:
                    print(f"  ← {printable(reply)}")
                    if reply[:1] == b"\x06":
                        total_ok += 1
        finally:
            if sock is not None:
                sock.close()

    if args.dry_run:
        print(f"\ndry run: {len(chosen)} session(s) parsed, nothing sent")
        return 0
    print(f"\nreplayed {len(chosen)} session(s); {total_ok} ACK(s) received")
    return 0 if total_ok else 1


if __name__ == "__main__":
    sys.exit(main())
