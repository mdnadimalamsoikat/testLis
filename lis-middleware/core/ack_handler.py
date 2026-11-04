"""
ACK framing for analyzers that speak ASTM E1381 / HL7 MLLP.

Some instruments will not release the next sample until the previous one is
acknowledged. Two modes are supported:

* ``ASTM``  — ``ACK``/``NAK`` single control bytes (E1381 low-level ACK).
* ``MLLP``  — a full ``MSH|...|ACK`` message, which some HL7 analyzers need.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("LIS.ACK")

ACK = "\x06"
NAK = "\x15"
ENQ = "\x05"
EOT = "\x04"
VT = "\x0b"
FS = "\x1c"
CR = "\r"


class ACKHandler:
    def __init__(self, mode: str = "ASTM", sending_app: str = "LIS_MIDDLEWARE",
                 sending_facility: str = "HOSPITAL"):
        self.mode = mode.upper()
        self.sending_app = sending_app
        self.sending_facility = sending_facility

    # ------------------------------------------------------------------ public
    def build(self, ok: bool = True, message_control_id: str = "", error: str = "") -> str:
        """Return the bytes (as str) an analyzer expects after a message."""
        if self.mode == "MLLP":
            return self.build_mllp_ack(ok, message_control_id, error)
        return ACK if ok else NAK

    def build_mllp_ack(self, ok: bool = True, message_control_id: str = "", error: str = "") -> str:
        now = datetime.now().strftime("%Y%m%d%H%M%S")
        ack_code = "AA" if ok else "AE"
        segments = [
            f"MSH|^~\\&|{self.sending_app}|{self.sending_facility}|ANALYZER|LAB|{now}||ACK^R01|ACK{now}|P|2.5.1",
            f"MSA|{ack_code}|{message_control_id or 'UNKNOWN'}" + (f"|{error}" if error else ""),
        ]
        return VT + CR.join(segments) + CR + FS + CR

    def build_astm_ack(self, ok: bool = True, sequence: int = 1) -> str:
        """A minimal ASTM L-record style reply (some hosts only want 0x06)."""
        return ACK if ok else NAK

    def describe(self, ok: bool, raw_len: int, stored: int) -> Dict[str, Any]:
        return {
            "ok": ok,
            "ack": "ACK" if ok else "NAK",
            "ack_mode": self.mode,
            "stored_results": stored,
            "payload_bytes": raw_len,
            "replied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


class NullACKHandler(ACKHandler):
    """Used by the web demo: nothing is written back to a real socket."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__("NONE")

    def build(self, ok: bool = True, message_control_id: str = "", error: str = "") -> str:  # noqa: D102
        return ""
