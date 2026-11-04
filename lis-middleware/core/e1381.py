"""
ASTM E1381 (LIS-1A) low-level transmission framing.

This is the layer *below* the E1394 message format: the handshake a real
analyzer uses before it will release a result. Getting this wrong is the most
common reason "the middleware starts but the machine sends nothing".

The real sequence (analyzer = sender, middleware = receiver):

    analyzer            middleware
       |  ENQ (0x05)        |
       |------------------->|   "I have a message"
       |  ACK (0x06)        |
       |<-------------------|   "go ahead"      (NAK 0x15 = busy, wait)
       |  STX 1 <text> ETX cs|
       |------------------->|   frame number, text, checksum
       |  ACK / NAK          |
       |<-------------------|   NAK -> the analyzer repeats the frame
       |  EOT (0x04)         |
       |------------------->|   "done"

The checksum is the low byte of the sum of every byte from the frame number
through ETX inclusive, sent as two ASCII digits (high nibble then low nibble).

Some instruments omit the handshake and just push text — the framer below
accepts both, which is why it can sit in front of a channel that is configured
strictly or loosely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger("LIS.E1381")

ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
EOT = 0x04
LF = 0x0A
CR = 0x0D


def compute_checksum(frame_number: int, text: bytes) -> int:
    """Sum of frame number + text + ETX, low byte."""
    total = frame_number + sum(text) + ETX
    return total & 0xFF


def checksum_chars(value: int) -> bytes:
    """Checksum as the two ASCII nibbles the wire format uses."""
    return bytes(((value >> 4) + 0x30, (value & 0x0F) + 0x30))


def build_frame(text: str, frame_number: int = 1, with_checksum: bool = True) -> bytes:
    """Encode a message block the way an E1381 sender does."""
    payload = text.replace("\r\n", "\r").replace("\n", "\r").encode("ascii", "replace")
    body = bytes([0x30 + (frame_number % 8)]) + payload
    frame = bytes([STX]) + body + bytes([ETX])
    if with_checksum:
        frame += checksum_chars(compute_checksum(0x30 + (frame_number % 8), payload))
    return frame


@dataclass
class Event:
    """One thing that happened on the wire."""
    kind: str                     # enq | frame | eot | text | error
    text: str = ""
    checksum_ok: bool = True
    frame_number: int = 0
    detail: str = ""


class E1381Framer:
    """Incremental parser: feed bytes, get events back.

    Handles three kinds of stream, because real labs contain all three:

    * full E1381 — ENQ, checksummed frames, EOT
    * "loose ASTM" — STX/ETX or plain text, no handshake
    * partial writes — a frame split across TCP segments
    """

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> List[Event]:
        self.buffer.extend(chunk)
        events: List[Event] = []

        while self.buffer:
            head = self.buffer[0]

            # --- link control bytes ------------------------------------
            if head == ENQ:
                del self.buffer[0]
                events.append(Event("enq"))
                continue
            if head == EOT:
                del self.buffer[0]
                events.append(Event("eot"))
                continue
            if head in (ACK, NAK):
                del self.buffer[0]      # we are the receiver; ignore stray ACKs
                continue
            if head in (CR, LF, 0x00):
                del self.buffer[0]      # filler between blocks
                continue

            # --- a framed block -----------------------------------------
            if head == STX:
                end = self.buffer.find(bytes([ETX]))
                if end == -1:
                    break               # wait for the rest
                if len(self.buffer) < end + 3:
                    break               # checksum bytes not here yet
                frame_number = (self.buffer[1] - 0x30) % 8   # 1..8 then 1 again
                payload = bytes(self.buffer[2:end])
                high, low = self.buffer[end + 1], self.buffer[end + 2]
                received = ((high - 0x30) << 4) | (low - 0x30)
                expected = compute_checksum(self.buffer[1], payload)
                ok = received == expected
                if not ok:
                    logger.warning(
                        "checksum mismatch on frame %d (got 0x%02X, expected 0x%02X)",
                        frame_number, received, expected,
                    )
                events.append(Event(
                    "frame",
                    text=payload.decode("ascii", "replace"),
                    checksum_ok=ok,
                    frame_number=frame_number,
                    detail="" if ok else "checksum",
                ))
                del self.buffer[:end + 3]
                continue

            # --- resync: noise in front of a frame -----------------------
            # (a serial cable with the wrong parity produces exactly this)
            stx = self.buffer.find(bytes([STX]), 1)
            if stx > 0:
                logger.debug("resyncing to STX, dropping %d noise byte(s)", stx)
                del self.buffer[:stx]
                continue

            # --- unframed text: accumulate until ETX, or give up waiting --
            etx = self.buffer.find(bytes([ETX]))
            if etx != -1:
                text = bytes(self.buffer[:etx]).decode("ascii", "replace")
                del self.buffer[:etx + 1]
                events.append(Event("text", text=text))
                continue
            break

        return events

    def flush(self) -> Optional[Event]:
        """End of stream: hand back any unterminated text (HL7 basic mode)."""
        if not self.buffer:
            return None
        text = bytes(self.buffer).decode("ascii", "replace")
        self.buffer.clear()
        if not text.strip("\x00\r\n"):
            return None
        return Event("text", text=text, detail="unterminated")
