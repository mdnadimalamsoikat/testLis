"""
Protocol sniffing + parser selection.

Analyzers are configured (see ``config/config.yaml``) with an explicit
protocol, but in the field people get it wrong. This module guesses from the
payload so a mislabelled channel still works, and logs what it picked.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from parsers.astm_parser import ASTMParser
from parsers.hl7_parser import HL7Parser

logger = logging.getLogger("LIS.PARSER")

HL7_SEGMENTS = ("MSH", "PID", "OBR", "OBX", "PV1", "ORC")


def sniff_protocol(raw: str) -> str:
    """Return 'HL7' or 'ASTM' for a raw message payload."""
    if not raw:
        return "ASTM"
    text = raw.replace("\x0b", "").replace("\x1c", "").lstrip()
    first = text.split("\r")[0].split("\n")[0].strip()
    if any(first.upper().startswith(seg + "|") for seg in HL7_SEGMENTS):
        return "HL7"
    if first[:1].upper() == "H":
        return "ASTM"
    if "|" in text and any(f"|{seg}|" in text for seg in HL7_SEGMENTS):
        return "HL7"
    return "ASTM"


def build_parser(protocol: str, test_code_map: Optional[Dict[str, str]] = None):
    if protocol.upper() == "HL7":
        return HL7Parser(test_code_map)
    return ASTMParser(test_code_map)


def parse_message(
    raw: str,
    protocol: str = "AUTO",
    analyzer_name: str = "UNKNOWN",
    test_code_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Parse with the configured protocol, falling back to sniffing."""
    chosen = protocol.upper()
    if chosen in ("AUTO", ""):
        chosen = sniff_protocol(raw)

    parser = build_parser(chosen, test_code_map)
    results = parser.parse(raw, analyzer_name)

    if not results and chosen != "AUTO":
        other = "HL7" if chosen == "ASTM" else "ASTM"
        retry = build_parser(other, test_code_map).parse(raw, analyzer_name)
        if retry:
            logger.warning(
                "%s sent %s but parsed as %s — check analyzer config",
                analyzer_name, chosen, other,
            )
            return retry

    return results
