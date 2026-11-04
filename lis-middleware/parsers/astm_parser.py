"""
ASTM E1394 / CLSI LIS2-A2 message parser.

Real lab analyzers (Sysmex XN, Roche Cobas, older Mindray) send plain-text
records separated by carriage returns, framed by <STX> ... <ETX>:

    H|\\^&|||Sysmex^XN-1000^12345|||||||P|E1394-97|20240115103000
    P|1||2024001234||Karim, Ahmed||19850615|M
    O|1|2024001234||^^^CBC|R
    R|1|^^^WBC^6690-2|7.5|10*3/uL|4.0-11.0|N||F||||20240115103000
    R|2|^^^RBC|5.2|10*6/uL|4.5-5.5|N||F
    L|1|N

Only the records that carry results (``R``) matter for a LIS, but the
patient (``P``) and order (``O``) records provide the keys needed to
store a result against a sample.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LIS.ASTM")

STX = "\x02"
ETX = "\x03"
CR = "\r"
LF = "\n"
ENQ = "\x05"
ACK = "\x06"
NAK = "\x15"
EOT = "\x04"

# ASTM uses these escape sequences inside a field
_REPEAT = "~"
_COMPONENT = "^"


class ASTMFields:
    """Field index constants for the record types we read."""

    # H (header)
    H_SENDER_NAME = 4
    H_TIMESTAMP = 13

    # P (patient)
    P_SEQ = 1
    P_PRACTICE_ID = 2         # practice-assigned patient id (often empty)
    P_PATIENT_ID = 3          # lab-assigned patient id — the one the LIS uses
    P_PATIENT_NAME = 5        # often "LAST, FIRST"
    P_DOB = 7
    P_SEX = 8

    # O (order)
    O_SEQ = 1
    O_SAMPLE_ID = 2
    O_INSTRUMENT = 3
    O_TEST_IDS = 4            # ^^^TEST1~^^^TEST2
    O_PRIORITY = 5
    O_ORDERED_AT = 6

    # R (result)
    R_SEQ = 1
    R_UNIVERSAL_TEST_ID = 2   # ^^^TEST_CODE^LOINC
    R_VALUE = 3
    R_UNITS = 4
    R_REF_RANGE = 5
    R_ABNORMAL_FLAG = 6
    R_STATUS = 7
    R_COMPLETED_AT = 12


def _clean(raw: str) -> str:
    """Strip framing/control characters and normalise line endings to CR.

    Real instruments terminate records with CR only, but sample files and
    copy-pasted payloads use LF — accept both.
    """
    if raw is None:
        return ""
    text = raw.replace(STX, "").replace(ETX, "").replace(ENQ, "")
    text = text.replace(ACK, "").replace(NAK, "").replace(EOT, "")
    text = text.replace("\r\n", CR).replace(LF, CR)
    return text


def _to_float(value: str) -> Optional[float]:
    """Analyzers like to send '7,5' or '>150' — normalise where possible."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    text = text.lstrip("<>=").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_astm_datetime(value: str) -> Optional[str]:
    """ASTM timestamps look like YYYYMMDDHHMMSS. Return ISO or None."""
    text = (value or "").strip()
    if len(text) < 8:
        return None
    for fmt, size in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        chunk = text[:size]
        try:
            return datetime.strptime(chunk, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _split_records(raw: str) -> List[List[str]]:
    """Split a message into records, each record into fields."""
    records: List[List[str]] = []
    for line in _clean(raw).split(CR):
        line = line.strip()
        if not line:
            continue
        records.append([field.strip() for field in line.split("|")])
    return records


def _field(fields: List[str], index: int, default: str = "") -> str:
    if 0 <= index < len(fields):
        return fields[index]
    return default


def _normalise_name(value: str) -> str:
    """ASTM names are 'LAST, FIRST' or 'LAST^FIRST'."""
    text = value.replace(_COMPONENT, ",").strip()
    if "," in text:
        last, _, first = text.partition(",")
        return f"{first.strip()} {last.strip()}".strip()
    return text


def _test_code_from_field(value: str) -> str:
    """'^^^WBC^6690-2' -> 'WBC'. Strips repeats and components."""
    if not value:
        return ""
    first = value.split(_REPEAT)[0]
    parts = [p.strip() for p in first.split(_COMPONENT)]
    # prefer the 4th component (test id), fall back to the last non-empty one
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    for part in reversed(parts):
        if part:
            return part
    return ""


class ASTMParser:
    """Parse an ASTM LIS2-A2 message into a list of result dicts."""

    name = "ASTM"

    def __init__(self, test_code_map: Optional[Dict[str, str]] = None):
        # e.g. {"WBC": "WBC", "HGB": "HB"} — machine code -> LIS test code
        self.test_code_map = test_code_map or {}

    # ------------------------------------------------------------------ public
    def parse(self, raw_message: str, analyzer_name: str = "UNKNOWN") -> List[Dict[str, Any]]:
        records = _split_records(raw_message)
        if not records:
            logger.warning("empty ASTM message from %s", analyzer_name)
            return []

        header = self._header(records)
        sample = self._order(records)
        patient = self._patient(records)

        results: List[Dict[str, Any]] = []
        for fields in records:
            if not fields or fields[0].upper() != "R":
                continue
            result = self._result_record(fields, analyzer_name, header, sample, patient, raw_message)
            if result:
                results.append(result)

        logger.info("%s: parsed %d result(s) from %s", self.name, len(results), analyzer_name)
        return results

    # ----------------------------------------------------------------- private
    def _header(self, records: List[List[str]]) -> Dict[str, str]:
        for fields in records:
            if fields and fields[0].upper() == "H":
                return {
                    "instrument": _field(fields, ASTMFields.H_SENDER_NAME),
                    "sent_at": _parse_astm_datetime(_field(fields, ASTMFields.H_TIMESTAMP)),
                }
        return {"instrument": "", "sent_at": None}

    def _patient(self, records: List[List[str]]) -> Dict[str, str]:
        for fields in records:
            if fields and fields[0].upper() == "P":
                patient_id = (_field(fields, ASTMFields.P_PATIENT_ID)
                              or _field(fields, ASTMFields.P_PRACTICE_ID))
                return {
                    "patient_id": patient_id,
                    "patient_name": _normalise_name(_field(fields, ASTMFields.P_PATIENT_NAME)),
                    "patient_dob": _field(fields, ASTMFields.P_DOB),
                    "patient_sex": _field(fields, ASTMFields.P_SEX),
                }
        return {"patient_id": "", "patient_name": "", "patient_dob": "", "patient_sex": ""}

    def _order(self, records: List[List[str]]) -> Dict[str, str]:
        for fields in records:
            if fields and fields[0].upper() == "O":
                return {
                    "sample_id": _field(fields, ASTMFields.O_SAMPLE_ID),
                    "ordered_at": _parse_astm_datetime(_field(fields, ASTMFields.O_ORDERED_AT)),
                }
        return {"sample_id": "", "ordered_at": None}

    def _result_record(
        self,
        fields: List[str],
        analyzer_name: str,
        header: Dict[str, str],
        sample: Dict[str, str],
        patient: Dict[str, str],
        raw_message: str,
    ) -> Optional[Dict[str, Any]]:
        raw_test = _field(fields, ASTMFields.R_UNIVERSAL_TEST_ID)
        test_code = _test_code_from_field(raw_test)
        if not test_code:
            return None
        test_code = self.test_code_map.get(test_code.upper(), test_code)

        raw_value = _field(fields, ASTMFields.R_VALUE)
        units = _field(fields, ASTMFields.R_UNITS)
        # some analyzers pack the unit inside the result field: "7.5 10*3/uL"
        if not units:
            match = re.match(r"^([<>]?[-\d.,]+)\s+(.+)$", raw_value)
            if match:
                raw_value, units = match.group(1), match.group(2)

        completed = _parse_astm_datetime(_field(fields, ASTMFields.R_COMPLETED_AT))

        return {
            "analyzer_name": analyzer_name,
            "sample_id": sample.get("sample_id", ""),
            "patient_id": patient.get("patient_id", ""),
            "patient_name": patient.get("patient_name", ""),
            "test_code": test_code,
            "result_value": _to_float(raw_value),
            "result_text": raw_value,
            "result_unit": units,
            "ref_range": _field(fields, ASTMFields.R_REF_RANGE),
            "abnormal_flag": (_field(fields, ASTMFields.R_ABNORMAL_FLAG) or "N").upper(),
            "result_status": _field(fields, ASTMFields.R_STATUS) or "F",
            "result_datetime": completed or header.get("sent_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_message": raw_message,
            "protocol": "ASTM",
        }
