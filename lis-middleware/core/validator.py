"""Shared result model + validation/normalisation rules.

Every parser produces plain dicts that pass through :class:`ResultValidator`
before they reach the database. Normalisation lives in one place so the
business rules stay testable without an Oracle connection.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LIS.VALIDATOR")

FLAG_ALIASES = {
    "N": "N", "": "N", "NORMAL": "N",
    "H": "H", "HIGH": "H", "HH": "HH", "H+": "H",
    "L": "L", "LOW": "L", "LL": "LL", "L-": "L",
    "A": "A", "ABNORMAL": "A",
    "C": "C", "CRITICAL": "C",
}

REQUIRED_FIELDS = ("sample_id", "test_code", "analyzer_name")

#: A result whose abnormal flag is one of these should raise a critical alert.
CRITICAL_FLAGS = {"C", "HH", "LL"}


class ResultValidator:
    """Rejects incomplete results and normalises the rest."""

    def __init__(self, known_test_codes: Optional[set[str]] = None,
                 ignore_sample_prefixes: Optional[List[str]] = None):
        self.known_test_codes = {c.upper() for c in (known_test_codes or set())}
        # analyzer quality-control samples (QC / CTRL / CAL) are not patient data
        self.ignore_prefixes = tuple(p.upper() for p in (ignore_sample_prefixes or []))
        #: filled by validate() — results skipped because they are control samples
        self.last_ignored: List[str] = []

    def validate(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        valid: List[Dict[str, Any]] = []
        self.last_ignored = []
        for raw in results:
            result = self.normalise(raw)
            problem = self._problem(result)
            if problem:
                if problem.startswith("control sample"):
                    self.last_ignored.append(problem)
                    logger.info("ignored control sample: %s", result["sample_id"])
                else:
                    logger.warning("dropped result (%s): %r", problem, raw.get("test_code"))
                continue
            valid.append(result)
        return valid

    # ----------------------------------------------------------------- private
    def normalise(self, result: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(result)
        out["sample_id"] = str(out.get("sample_id") or "").strip()
        out["patient_id"] = str(out.get("patient_id") or "").strip()
        out["patient_name"] = str(out.get("patient_name") or "").strip()
        out["test_code"] = str(out.get("test_code") or "").strip().upper()
        out["analyzer_name"] = str(out.get("analyzer_name") or "").strip()
        out["result_unit"] = str(out.get("result_unit") or "").strip()
        out["ref_range"] = str(out.get("ref_range") or "").strip()
        out["result_text"] = str(out.get("result_text") or "").strip()

        flag = str(out.get("abnormal_flag") or "").strip().upper()
        out["abnormal_flag"] = FLAG_ALIASES.get(flag, flag or "N")

        out["is_critical"] = "Y" if out["abnormal_flag"] in CRITICAL_FLAGS else "N"
        out["is_abnormal"] = "Y" if out["abnormal_flag"] not in {"N", ""} else "N"

        if not out.get("result_datetime"):
            out["result_datetime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # numeric results may still arrive as strings from a custom parser
        if out.get("result_value") is not None:
            try:
                out["result_value"] = float(str(out["result_value"]).replace(",", "."))
            except (TypeError, ValueError):
                out["result_value"] = None

        out["protocol"] = str(out.get("protocol") or "").upper()
        return out

    def _problem(self, result: Dict[str, Any]) -> Optional[str]:
        for key in REQUIRED_FIELDS:
            if not result.get(key):
                return f"missing {key}"
        if result.get("result_value") is None and not result.get("result_text"):
            return "no value"
        if self.ignore_prefixes and result["sample_id"].upper().startswith(self.ignore_prefixes):
            return f"control sample ({result['sample_id']})"
        if self.known_test_codes and result["test_code"] not in self.known_test_codes:
            return f"unknown test code {result['test_code']}"
        return None
