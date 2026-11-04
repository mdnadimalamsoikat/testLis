"""
Mock analyzer — pretends to be a real lab instrument.

Why: you cannot test a middleware by waiting for a patient sample. This tool
opens a TCP connection to the middleware (or to any host), sends a real-shaped
ASTM/HL7 payload and waits for the acknowledgement, exactly like Sysmex or
Mindray would.

    # list the built-in scenarios
    python tools/mock_analyzer.py --list

    # send one CBC message to the middleware on this machine
    python tools/mock_analyzer.py --scenario astm_cbc_normal --host 127.0.0.1 --port 6661

    # send an HL7 biochem message every 10 seconds, 5 times (load test)
    python tools/mock_analyzer.py -s hl7_biochem -p 6662 --repeat 5 --interval 10

    # serial mode (needs a com0com virtual pair or a real cable)
    python tools/mock_analyzer.py -s astm_cbc_normal --serial COM4
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import yaml

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "samples" / "messages.yaml"


def load_samples() -> dict[str, str]:
    with SAMPLES_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def frame(raw: str, protocol: str) -> bytes:
    """Wrap the payload the way the target analyzer would."""
    text = raw.strip().replace("\r\n", "\n").replace("\n", "\r")
    if protocol.upper() == "HL7":
        return b"\x0b" + text.encode("utf-8") + b"\x1c\r"   # MLLP
    return b"\x05" + text.replace("\r", "\r").encode("utf-8") + b"\x03\x04"  # ASTM


def send_tcp(raw: str, host: str, port: int, protocol: str, timeout: float = 5.0) -> str:
    payload = frame(raw, protocol)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        print(f"→ {host}:{port}  {len(payload)} bytes ({protocol})")
        sock.sendall(payload)
        sock.settimeout(timeout)
        try:
            reply = sock.recv(1024)
        except socket.timeout:
            return "(no acknowledgement — middleware may be in basic mode)"
    if reply[:1] == b"\x06":
        return "ACK (0x06)"
    if reply[:1] == b"\x15":
        return "NAK (0x15)"
    return reply.decode("utf-8", errors="replace").replace("\x0b", "<VT>").replace("\x1c", "<FS>")


def send_serial(raw: str, port_name: str, baud: int = 9600) -> str:
    try:
        import serial
    except ImportError:
        return "pyserial missing — pip install pyserial"

    payload = raw.strip().replace("\n", "\r")
    with serial.Serial(port_name, baud, timeout=5) as port:
        print(f"→ {port_name} @ {baud} baud, {len(payload)} chars")
        port.write(payload.encode("ascii", errors="ignore"))
        reply = port.read(64).decode("ascii", errors="replace")
    return reply or "(no reply)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate a lab analyzer sending results")
    parser.add_argument("-s", "--scenario", default="astm_cbc_normal")
    parser.add_argument("-H", "--host", default="127.0.0.1")
    parser.add_argument("-p", "--port", type=int, default=6661)
    parser.add_argument("--protocol", choices=["ASTM", "HL7"], default=None,
                        help="override framing (default: inferred from the scenario name)")
    parser.add_argument("--serial", metavar="COM_PORT", help="send over RS-232 instead of TCP")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args()

    samples = load_samples()
    if args.list:
        print("available scenarios:")
        for name in samples:
            print(f"  - {name}")
        return 0

    if args.scenario not in samples:
        print(f"unknown scenario '{args.scenario}' — try --list")
        return 2

    raw = samples[args.scenario]
    protocol = args.protocol or ("HL7" if args.scenario.startswith("hl7") else "ASTM")

    for index in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\n[{index}/{args.repeat}] {args.scenario}")
        try:
            if args.serial:
                reply = send_serial(raw, args.serial)
            else:
                reply = send_tcp(raw, args.host, args.port, protocol)
            print(f"← {reply}")
        except ConnectionRefusedError:
            print(f"connection refused on {args.host}:{args.port} — is `python main.py` running?")
            return 1
        except OSError as exc:
            print(f"network error: {exc}")
            return 1

        if index < args.repeat:
            time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
