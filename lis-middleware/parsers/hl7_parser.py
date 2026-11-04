"""
HL7 v2.x (2.3.1 / 2.5.1) message parser — the ORU^R01 result message.

Every segment is a line, fields are separated by ``|``, components by ``^``::

    MSH|^~\\&|MINDRAY|BS240|LIS|HOSPITAL|20240115103000||ORU^R01|MSG00001|P|2.5.1
    PID|1||2024001234||Karim^Ahmed||19850615|M
    OBR|1||2024001234|CBC
    OBX|1|NM|WBC^Leukocytes^6690-2||7.5|10*3/uL|4.0-11.0|N|||F
    OBX|2|NM|RBC^Erythrocytes^789-8||5.2|10*6/uL|4.5-5.5|N|||F

Unlike ASTM, HL7 messages are terminated by ``\\r`` and the parser anchors on
segment names, so it is tolerant of their order.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LIS.HL7")

VT = "\x0b"   # vertical tab — MLP block start
FS = "\x1c"   # file separator — MLP block end
CR = "\r"
LF = "\n"


def _clean(raw: str) -> str:
    if raw is None:
        return ""
    text = raw.replace(VT, "").replace(FS, "")
    return text.replace(CR, LF)


def _to_float(value: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".").lstrip("<>=").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_hl7_datetime(value: str) -> Optional[str]:
    """HL7 timestamps: YYYYMMDDHHMMSS[+ZZZZ]."""
    text = (value or "").strip()
    if not text:
        return None
    text = text.split("+")[0].split("-")[0] if len(text) > 14 else text
    for fmt, size in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:size], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


class HL7Parser:
    """Parse an HL7 ORU^R01 (or any OBX-bearing) message into result dicts."""

    name = "HL7"

    def __init__(self, test_code_map: Optional[Dict[str, str]] = None):
        self.test_code_map = test_code_map or {}

    # ------------------------------------------------------------------ public
    def parse(self, raw_message: str, analyzer_name: str = "UNKNOWN") -> List[Dict[str, Any]]:
        segments: Dict[str, List[List[str]]] = {}
        for line in _clean(raw_message).split(LF):
            line = line.strip()
            if not line or "|" not in line:
                continue
            fields = line.split("|")
            segments.setdefault(fields[0].upper(), []).append(fields)

        if "OBX" not in segments:
            logger.warning("%s: no OBX segment in message from %s", self.name, analyzer_name)
            return []

        context = self._patient_context(segments)
        results: List[Dict[str, Any]] = []

        for fields in segments["OBX"]:
            result = self._obx(fields, analyzer_name, context, raw_message)
            if result:
                results.append(result)

        logger.info("%s: parsed %d result(s) from %s", self.name, len(results), analyzer_name)
        return results

    # ----------------------------------------------------------------- private
    def _patient_context(self, segments: Dict[str, List[List[str]]]) -> Dict[str, str]:
        ctx = {
            "sample_id": "",
            "patient_id": "",
            "patient_name": "",
            "patient_dob": "",
            "patient_sex": "",
            "sent_at": None,
            "control_id": "",
        }

        pid = segments.get("PID", [])
        if pid:
            fields = pid[0]
            ctx["patient_id"] = self._at(fields, 3).split("~")[0]
            name = self._at(fields, 5)
            if "^" in name:
                last, first = (name.split("^") + ["", ""])[:2]
                ctx["patient_name"] = f"{first} {last}".strip()
            else:
                ctx["patient_name"] = name
            ctx["patient_dob"] = _parse_hl7_datetime(self._at(fields, 7)) or ""
            ctx["patient_sex"] = self._at(fields, 8)

        obr = segments.get("OBR", [])
        if obr:
            ctx["sample_id"] = self._at(obr[0], 3).split("^")[0] or self._at(obr[0], 2)

        msh = segments.get("MSH", [])
        if msh:
            ctx["sent_at"] = _parse_hl7_datetime(self._at(msh[0], 6))
            ctx["control_id"] = self._at(msh[0], 9)

        return ctx

    def _obx(
        self,
        fields: List[str],
        analyzer_name: str,
        context: Dict[str, Any],
        raw_message: str,
    ) -> Optional[Dict[str, Any]]:
        identifier = self._at(fields, 3)          # CODE^TEXT^LOINC
        value_type = self._at(fields, 2) or "NM"
        raw_value = self._at(fields, 5)

        code = identifier.split("^")[0].strip()
        if not code:
            return None
        code = self.test_code_map.get(code.upper(), code)

        abnormal = (self._at(fields, 8) or "N").upper()
        status = self._at(fields, 11) or "F"
        observed_at = _parse_hl7_datetime(self._at(fields, 14))

        return {
            "analyzer_name": analyzer_name,
            "sample_id": context.get("sample_id") or context.get("patient_id", ""),
            "patient_id": context.get("patient_id", ""),
            "patient_name": context.get("patient_name", ""),
            "test_code": code,
            "result_value": _to_float(raw_value) if value_type.upper() != "ST" else None,
            "result_text": raw_value,
            "result_unit": self._at(fields, 6),
            "ref_range": self._at(fields, 7),
            "abnormal_flag": abnormal,
            "result_status": status,
            "result_datetime": observed_at or context.get("sent_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_message": raw_message,
            "protocol": "HL7",
            "control_id": context.get("control_id", ""),
        }

    @staticmethod
    def _at(fields: List[str], index: int) -> str:
        """Field accessor. MSH-1 is the separator itself, so indexes shift by 1."""
        if 0 <= index < len(fields):
            return fields[index].strip()
        return ""
