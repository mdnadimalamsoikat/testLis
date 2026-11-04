"""
sniff_com — capture what the analyzer *actually* says, byte for byte.

A simulator can only prove the middleware handles the frames it is fed. The
guaranteed-real path is: put this tool between the analyzer and the PC, run one
control sample, and you have the machine's own bytes — handshake, checksums,
line endings and all.

    # 1. capture from the analyzer's cable AND forward it to the middleware
    python tools/sniff_com.py --port COM3 --baud 9600 --parity EVEN \
        --tap 127.0.0.1:6661 --out logs/capture.jsonl

    # 2. later, replay the same bytes over TCP as often as you like
    python tools/replay_capture.py logs/capture.jsonl --port 6661

Wiring options (pick what your hardware allows):

  * serial cable split through a second COM port (com0com, or a hardware tap)
  * RS-232 "Y" cable / breakout box, one branch to this tool, one to the LIS
  * if the analyzer already talks TCP: point it at this tool instead of the
    middleware and use --listen 6661 --tap 127.0.0.1:6662 (TCP tap mode)

In TCP tap mode the tool listens on --listen, accepts the analyzer, forwards
every byte to --tap untouched, and logs everything.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONTROL = {0x05: "<ENQ>", 0x06: "<ACK>", 0x15: "<NAK>", 0x02: "<STX>",
           0x03: "<ETX>", 0x04: "<EOT>", 0x0B: "<VT>", 0x1C: "<FS>",
           0x0D: "<CR>", 0x0A: "<LF>"}


class Capture:
    """Append-only JSONL log: one line per byte chunk, both directions."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.records = 0

    def write(self, direction: str, data: bytes) -> None:
        if not data:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "dir": direction,                       # "device" or "host"
            "hex": data.hex(),
            "printable": "".join(chr(b) if 32 <= b < 127 else CONTROL.get(b, f"\\x{b:02x}")
                                 for b in data),
        }
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            self.records += 1
        if self.records <= 200:                     # live feedback on the console
            print(f"  [{direction:6}] {len(data):4} B  {record['printable'][:120]}")


def serial_mode(args: argparse.Namespace, capture: Capture) -> None:
    try:
        import serial
    except ImportError:
        sys.exit("pyserial is required: pip install pyserial")

    parity = {"NONE": serial.PARITY_NONE, "EVEN": serial.PARITY_EVEN, "ODD": serial.PARITY_ODD}
    tap: Optional[socket.socket] = None
    if args.tap:
        host, _, port = args.tap.partition(":")
        tap = socket.create_connection((host, int(port)), timeout=10)
        print(f"tapping {args.port} → {args.tap} (middleware)")

    with serial.Serial(args.port, args.baud, bytesize=args.databits,
                       parity=parity.get(args.parity.upper(), serial.PARITY_NONE),
                       stopbits=args.stopbits, timeout=0.5) as port:
        print(f"listening on {args.port} @ {args.baud} {args.databits}"
              f"{args.parity[0]}{args.stopbits} — take one sample on the analyzer")
        while True:
            data = port.read(port.in_waiting or 1)
            if not data:
                continue
            capture.write("device", data)
            if tap is not None:
                tap.sendall(data)                     # byte-for-byte, no rewriting
                try:
                    reply = tap.recv(4096)
                except socket.timeout:
                    reply = b""
                if reply:
                    capture.write("host", reply)
                    port.write(reply)                 # give the ACK back to the machine


def tcp_mode(args: argparse.Namespace, capture: Capture) -> None:
    host, _, port = args.listen.partition(":")
    tap_host, _, tap_port = args.tap.partition(":")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host or "0.0.0.0", int(port)))
    server.listen(5)
    print(f"TCP tap: analyzer → {args.listen} → {args.tap}   (Ctrl+C to stop)")

    while True:
        client, peer = server.accept()
        print(f"connection from {peer[0]}:{peer[1]}")
        upstream = socket.create_connection((tap_host, int(tap_port)), timeout=10)

        def pump(src: socket.socket, dst: socket.socket, direction: str) -> None:
            try:
                while True:
                    data = src.recv(4096)
                    if not data:
                        break
                    capture.write(direction, data)
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for sock in (src, dst):
                    try:
                        sock.close()
                    except OSError:
                        pass

        threading.Thread(target=pump, args=(client, upstream, "device"), daemon=True).start()
        threading.Thread(target=pump, args=(upstream, client, "host"), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture raw analyzer traffic")
    parser.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--databits", type=int, default=8)
    parser.add_argument("--parity", default="NONE", choices=["NONE", "EVEN", "ODD", "none", "even", "odd"])
    parser.add_argument("--stopbits", type=int, default=1)
    parser.add_argument("--listen", help="TCP tap mode: listen on HOST:PORT (e.g. 0.0.0.0:6661)")
    parser.add_argument("--tap", help="forward to the middleware, HOST:PORT")
    parser.add_argument("--out", default="logs/capture.jsonl")
    args = parser.parse_args()

    capture = Capture(Path(args.out))
    print(f"writing capture to {args.out}")
    try:
        if args.listen:
            tcp_mode(args, capture)
        elif args.port:
            serial_mode(args, capture)
        else:
            parser.error("use --port (serial) or --listen (TCP tap)")
    except KeyboardInterrupt:
        print(f"\nstopped — {capture.records} chunks captured in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
