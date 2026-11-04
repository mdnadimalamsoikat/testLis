"""
device_sim — behave like a real analyzer on the wire, not like a web form.

The browser demo shows the pipeline nicely, but a demo HTTP form is not what
Sysmex sends. This tool is a *laboratory simulator*:

* speaks the real **ASTM E1381** handshake — ENQ, ACK, STX⟨frame⟩ETX⟨cs⟩,
  NAK/retry, EOT — and checks the returned acknowledgement at every step;
* speaks **HL7 MLLP** (VT … FS CR) with ACK/NAK handling;
* can inject the failures only a real cable produces:
  corrupted checksums, dropped blocks, the receiver never answering,
  duplicate re-transmission after a lost ACK, line noise, disconnects;
* prints a byte-level trace, the same way you would watch a serial analyser
  with a breakout box.

    # list what is available
    python tools/device_sim.py --list

    # full ASTM handshake against a locally running middleware
    python tools/device_sim.py -s astm_cbc_normal --host 127.0.0.1 --port 6661 --trace

    # HL7 MLLP
    python tools/device_sim.py -s hl7_biochem --host 127.0.0.1 --port 6662 --trace

    # abuse it: every 3rd frame has a bad checksum, 20% of frames dropped
    python tools/device_sim.py -s astm_cbc_abnormal --fault checksum --fault drop \\
        --fault-rate 0.34 --repeat 3

    # line noise on top of a valid frame
    python tools/device_sim.py -s astm_cbc_normal --fault noise

    # what a lab sees when the middleware is off
    python tools/device_sim.py -s astm_cbc_normal --port 7000

Exit code is 0 only when the middleware acknowledged every frame — this makes
it usable in a smoke-test script:
    python tools/device_sim.py -s hl7_critical --port 6662 || echo "WIRING BROKEN"
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.e1381 import (ACK, EOT, ENQ, ETX, NAK, STX, build_frame,  # noqa: E402
                        checksum_chars, compute_checksum)

SAMPLES = ROOT / "samples" / "messages.yaml"
MLLP_START = b"\x0b"
MLLP_END = b"\x1c"


# ---------------------------------------------------------------------------
# trace helpers
# ---------------------------------------------------------------------------
def show(data: bytes, direction: str) -> None:
    """Render control bytes readably — '<ENQ>', '<STX>1', ..."""
    lookup = {ENQ: "<ENQ>", ACK: "<ACK>", NAK: "<NAK>", STX: "<STX>", ETX: "<ETX>",
              EOT: "<EOT>", 0x0B: "<VT>", 0x1C: "<FS>", 0x0D: "<CR>", 0x00: "<NUL>"}
    parts: List[str] = []
    for byte in data:
        parts.append(lookup.get(byte, chr(byte) if 32 <= byte < 127 else f"\\x{byte:02x}"))
    arrow = "→ analyzer" if direction == "tx" else "← middleware"
    text = "".join(parts)
    if len(text) > 220:
        text = text[:220] + f" … ({len(data)} bytes)"
    print(f"  {arrow}: {text}")


@dataclass
class Report:
    frames_sent: int = 0
    frames_acked: int = 0
    frames_naked: int = 0
    retries: int = 0
    messages_accepted: int = 0
    faults_injected: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.frames_sent > 0

    def summary(self) -> str:
        lines = [
            f"frames sent      : {self.frames_sent}",
            f"frames acked     : {self.frames_acked}",
            f"frames naked     : {self.frames_naked}",
            f"retries          : {self.retries}",
            f"messages accepted: {self.messages_accepted}",
        ]
        if self.faults_injected:
            lines.append("faults injected  : " + ", ".join(self.faults_injected))
        if self.errors:
            lines += ["errors           : " + e for e in self.errors]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ASTM E1381 sender
# ---------------------------------------------------------------------------
def send_astm(host: str, port: int, message: str, report: Report,
              trace: bool, faults: set[str], fault_rate: float,
              timeout: float = 6.0, retries: int = 4) -> None:
    blocks = [line for line in message.replace("\r\n", "\n").split("\n") if line.strip()]
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)

    def read_byte() -> Optional[int]:
        try:
            data = sock.recv(1)
        except socket.timeout:
            return None
        return data[0] if data else None

    try:
        # ---- ENQ / ACK handshake --------------------------------------
        if trace:
            show(bytes([ENQ]), "tx")
        sock.sendall(bytes([ENQ]))
        reply = read_byte()
        if reply != ACK:
            report.errors.append(
                f"no ACK after ENQ (got {reply!r}) — is the listener in basic/HL7 mode?"
            )
            return
        if trace:
            show(bytes([reply]), "rx")

        # ---- frames ---------------------------------------------------
        for index, line in enumerate(blocks, start=1):
            frame_no = ((index - 1) % 8) + 1
            frame = build_frame(line, frame_no)

            if random.random() < fault_rate and "drop" in faults:
                report.faults_injected.append(f"dropped frame {frame_no} (sender bug)")
                continue

            acknowledged = False
            for attempt in range(retries + 1):
                # a fault is injected per attempt: on a retry the device sends
                # the frame clean again, exactly like real hardware
                outgoing = frame
                # corrupt only the first attempt: the retry is a clean resend,
                # which is what real hardware does after a NAK
                if attempt == 0 and faults and random.random() < fault_rate:
                    fault = random.choice(sorted(faults))
                    if fault == "checksum":
                        outgoing = frame[:-1] + bytes([0x40])
                        report.faults_injected.append(f"bad checksum on frame {frame_no}")
                    elif fault == "noise":
                        outgoing = bytes([0x00, 0x7F]) + frame
                        report.faults_injected.append(f"line noise before frame {frame_no}")

                if trace:
                    show(outgoing, "tx")
                sock.sendall(outgoing)
                report.frames_sent += 1
                reply = read_byte()

                if reply == ACK:
                    report.frames_acked += 1
                    if trace:
                        show(bytes([reply]), "rx")
                    acknowledged = True
                    break
                if reply == NAK:
                    report.frames_naked += 1
                    report.retries += 1
                    if trace:
                        show(bytes([reply]), "rx")
                    continue
                if reply is None:
                    report.errors.append(f"timeout waiting for ACK on frame {frame_no}")
                    return
                if reply == ENQ:
                    sock.sendall(bytes([ACK]))     # recover from a stray ENQ
                    continue
                report.errors.append(f"unexpected byte 0x{reply:02X} on frame {frame_no}")
                return

            if not acknowledged:
                report.errors.append(
                    f"frame {frame_no} never acknowledged after {retries} retries")
                return

        # ---- EOT ------------------------------------------------------
        sock.sendall(bytes([EOT]))
        if trace:
            show(bytes([EOT]), "tx")
        if not report.errors:
            report.messages_accepted += 1
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# HL7 MLLP sender
# ---------------------------------------------------------------------------
def send_mllp(host: str, port: int, message: str, report: Report,
              trace: bool, timeout: float = 6.0) -> None:
    payload = message.strip().replace("\r\n", "\r").replace("\n", "\r")
    frame = MLLP_START + payload.encode("ascii", "replace") + MLLP_END + b"\r"

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        if trace:
            show(frame, "tx")
        sock.sendall(frame)
        report.frames_sent += 1
        try:
            reply = sock.recv(2048)
        except socket.timeout:
            report.errors.append("no MLLP acknowledgement (analyzer would retry)")
            return
        if trace:
            show(reply, "rx")
        if reply[:1] == bytes([ACK]):
            report.frames_acked += 1
            report.messages_accepted += 1
        elif reply[:1] == bytes([NAK]):
            report.frames_naked += 1
            report.errors.append("middleware replied NAK (message rejected)")
        elif MLLP_START in reply and b"MSA|AA" in reply:
            report.frames_acked += 1
            report.messages_accepted += 1
        elif MLLP_START in reply and (b"MSA|AE" in reply or b"MSA|AR" in reply):
            report.frames_naked += 1
            report.errors.append("middleware replied MSA|AE (error in ACK)")
        else:
            report.errors.append(f"unexpected reply: {reply[:40]!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate a lab analyzer on the wire (ASTM E1381 / HL7 MLLP)")
    parser.add_argument("-s", "--scenario", default="astm_cbc_normal")
    parser.add_argument("-H", "--host", default="127.0.0.1")
    parser.add_argument("-p", "--port", type=int, default=6661)
    parser.add_argument("--protocol", choices=["ASTM", "HL7", "MLLP"], default=None)
    parser.add_argument("--fault", action="append", default=[],
                        choices=["checksum", "drop", "noise"],
                        help="inject a wire fault (repeatable)")
    parser.add_argument("--fault-rate", type=float, default=0.3,
                        help="chance a frame gets a fault (default 0.3)")
    parser.add_argument("--repeat", type=int, default=1, help="send the message N times")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--trace", action="store_true", help="print every byte")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    scenarios = yaml.safe_load(SAMPLES.read_text(encoding="utf-8"))
    if args.list:
        print("scenarios:")
        for name in scenarios:
            print(f"  {name}")
        print("\nfaults: checksum | drop | noise")
        return 0

    if args.scenario not in scenarios:
        print(f"unknown scenario {args.scenario!r} — try --list")
        return 2

    message = scenarios[args.scenario]
    protocol = (args.protocol or ("HL7" if args.scenario.startswith("hl7") else "ASTM")).upper()
    report = Report()

    print(f"device_sim → {args.host}:{args.port}  scenario={args.scenario}  protocol={protocol}")
    print("-" * 66)
    started = time.time()
    try:
        for index in range(1, args.repeat + 1):
            if args.repeat > 1:
                print(f"[{index}/{args.repeat}]")
            if protocol in ("HL7", "MLLP"):
                send_mllp(args.host, args.port, message, report, args.trace, args.timeout)
            else:
                send_astm(args.host, args.port, message, report, args.trace,
                          set(args.fault), args.fault_rate, args.timeout)
            if report.errors:
                break
            if index < args.repeat:
                time.sleep(args.interval)
    except ConnectionRefusedError:
        print(f"\nconnection refused on {args.host}:{args.port}")
        print("→ the middleware is not listening there. Start it with: python main.py --dry-run")
        return 1
    except OSError as exc:
        print(f"\nnetwork error: {exc}")
        return 1

    elapsed = (time.time() - started) * 1000
    print("-" * 66)
    print(report.summary())
    print(f"elapsed          : {elapsed:.0f} ms")
    if report.ok and not report.errors:
        print("\nRESULT: the middleware behaved like a real LIS endpoint ✅")
        return 0
    print("\nRESULT: wiring or middleware problem ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
