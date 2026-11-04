"""
Unit tests — no Oracle, no sockets. Run from lis-middleware/:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ack_handler import ACKHandler              # noqa: E402
from core.engine import IngestEngine                 # noqa: E402
from core.parser_factory import parse_message, sniff_protocol  # noqa: E402
from core.validator import ResultValidator           # noqa: E402
from database.memory_store import MemoryStore        # noqa: E402
from parsers.astm_parser import ASTMParser           # noqa: E402
from parsers.hl7_parser import HL7Parser             # noqa: E402


def load_samples() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "samples", "messages.yaml")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


SAMPLES = load_samples()
ANALYZER = {"name": "Sysmex_XN_1000", "protocol": "AUTO"}


class TestASTMParser(unittest.TestCase):
    def setUp(self):
        self.parser = ASTMParser()

    def test_parses_all_result_records(self):
        results = self.parser.parse(SAMPLES["astm_cbc_normal"], "Sysmex_XN_1000")
        self.assertEqual(len(results), 4)
        codes = [r["test_code"] for r in results]
        self.assertEqual(codes, ["WBC", "RBC", "HGB", "PLT"])

    def test_links_patient_and_sample(self):
        results = self.parser.parse(SAMPLES["astm_cbc_normal"], "Sysmex_XN_1000")
        first = results[0]
        self.assertEqual(first["sample_id"], "SAMPLE-0001")
        self.assertEqual(first["patient_id"], "2024001234")
        self.assertEqual(first["patient_name"], "Ahmed Karim")   # 'LAST, FIRST' flipped
        self.assertEqual(first["protocol"], "ASTM")

    def test_units_and_reference_range(self):
        results = self.parser.parse(SAMPLES["astm_cbc_normal"], "Sysmex_XN_1000")
        wbc = next(r for r in results if r["test_code"] == "WBC")
        self.assertEqual(wbc["result_value"], 7.5)
        self.assertEqual(wbc["result_unit"], "10*3/uL")
        self.assertEqual(wbc["ref_range"], "4.0-11.0")

    def test_abnormal_flags(self):
        results = self.parser.parse(SAMPLES["astm_cbc_abnormal"], "Sysmex_XN_1000")
        flags = {r["test_code"]: r["abnormal_flag"] for r in results}
        self.assertEqual(flags["WBC"], "H")
        self.assertEqual(flags["HGB"], "LL")
        self.assertEqual(flags["PLT"], "L")

    def test_timestamp_parsed(self):
        results = self.parser.parse(SAMPLES["astm_cbc_normal"], "Sysmex_XN_1000")
        self.assertEqual(results[0]["result_datetime"], "2024-01-15 10:30:00")

    def test_comma_decimal_and_merged_unit(self):
        results = ASTMParser().parse(SAMPLES["astm_merged_unit"], "Roche")
        values = {r["test_code"]: (r["result_value"], r["result_unit"]) for r in results}
        self.assertEqual(values["GLU"][0], 5.8)              # '5,8 mmol/L'
        self.assertIn("mmol/L", values["GLU"][1])
        self.assertEqual(values["ALT"][0], 150.0)            # '>150 U/L'

    def test_garbage_yields_nothing_and_does_not_raise(self):
        self.assertEqual(self.parser.parse(SAMPLES["broken_message"], "X"), [])
        self.assertEqual(self.parser.parse("", "X"), [])

    def test_control_characters_are_stripped(self):
        raw = "\x02" + SAMPLES["astm_cbc_normal"].replace("\n", "\r") + "\x03"
        self.assertEqual(len(self.parser.parse(raw, "Sysmex")), 4)


class TestHL7Parser(unittest.TestCase):
    def setUp(self):
        self.parser = HL7Parser()

    def test_parses_obx_segments(self):
        results = self.parser.parse(SAMPLES["hl7_biochem"], "Mindray_BS240")
        self.assertEqual(len(results), 4)
        self.assertEqual([r["test_code"] for r in results], ["GLU", "CREA", "ALT", "AST"])

    def test_patient_context(self):
        results = self.parser.parse(SAMPLES["hl7_biochem"], "Mindray_BS240")
        first = results[0]
        self.assertEqual(first["sample_id"], "SAMPLE-0003")
        self.assertEqual(first["patient_name"], "Fatema Akter")
        self.assertEqual(first["patient_id"], "2024009012")

    def test_value_unit_and_flag(self):
        results = self.parser.parse(SAMPLES["hl7_biochem"], "Mindray_BS240")
        alt = next(r for r in results if r["test_code"] == "ALT")
        self.assertEqual(alt["result_value"], 68.0)
        self.assertEqual(alt["result_unit"], "U/L")
        self.assertEqual(alt["abnormal_flag"], "H")

    def test_mllp_framing(self):
        raw = "\x0b" + SAMPLES["hl7_biochem"].replace("\n", "\r") + "\x1c\r"
        self.assertEqual(len(self.parser.parse(raw, "Mindray_BS240")), 4)

    def test_no_obx_returns_empty(self):
        self.assertEqual(self.parser.parse("MSH|^~\\&|A|B", "X"), [])


class TestProtocolSniffing(unittest.TestCase):
    def test_detect_hl7(self):
        self.assertEqual(sniff_protocol(SAMPLES["hl7_biochem"]), "HL7")

    def test_detect_astm(self):
        self.assertEqual(sniff_protocol(SAMPLES["astm_cbc_normal"]), "ASTM")

    def test_detect_hl7_with_mllp_wrapper(self):
        self.assertEqual(sniff_protocol("\x0b" + SAMPLES["hl7_biochem"]), "HL7")

    def test_mislabelled_channel_still_parses(self):
        # configured as ASTM but the analyzer speaks HL7 -> retry path
        results = parse_message(SAMPLES["hl7_biochem"], protocol="ASTM",
                                analyzer_name="Mindray")
        self.assertEqual(len(results), 4)


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ResultValidator()

    def test_normalises_flag_and_sets_critical(self):
        out = self.validator.validate([{
            "sample_id": "S1", "test_code": "hgb", "analyzer_name": "A",
            "result_value": "7.2", "abnormal_flag": "ll",
        }])
        self.assertEqual(out[0]["test_code"], "HGB")
        self.assertEqual(out[0]["abnormal_flag"], "LL")
        self.assertEqual(out[0]["is_critical"], "Y")
        self.assertEqual(out[0]["is_abnormal"], "Y")
        self.assertEqual(out[0]["result_value"], 7.2)

    def test_drops_missing_sample_id(self):
        out = self.validator.validate([{
            "sample_id": "", "test_code": "WBC", "analyzer_name": "A", "result_value": 5,
        }])
        self.assertEqual(out, [])

    def test_control_samples_are_ignored(self):
        validator = ResultValidator(ignore_sample_prefixes=["QC", "CTRL", "CAL"])
        out = validator.validate([{
            "sample_id": "QC-LEVEL2", "test_code": "WBC", "analyzer_name": "A", "result_value": 7,
        }])
        self.assertEqual(out, [])

    def test_normal_flag_is_not_critical(self):
        out = self.validator.validate([{
            "sample_id": "S1", "test_code": "WBC", "analyzer_name": "A",
            "result_value": 7, "abnormal_flag": "N",
        }])
        self.assertEqual(out[0]["is_critical"], "N")
        self.assertEqual(out[0]["is_abnormal"], "N")


class TestEnginePipeline(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.engine = IngestEngine(
            store=self.store,
            validator=ResultValidator(),
            ack_handler=ACKHandler("ASTM"),
            test_code_map={"HGB": "HB"},
        )

    def test_end_to_end_astm(self):
        response = self.engine.process(SAMPLES["astm_cbc_normal"], ANALYZER, peer="tcp:1")
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "PARSED")
        self.assertEqual(response["stored"], 4)
        self.assertEqual(response["ack"], "\x06")
        # HGB was remapped to HB by the config map
        stored_codes = {r["test_code"] for r in self.store.results}
        self.assertIn("HB", stored_codes)
        self.assertEqual(self.store.stats()["total"], 4)

    def test_end_to_end_hl7(self):
        response = self.engine.process(SAMPLES["hl7_biochem"], ANALYZER)
        self.assertEqual(response["protocol"], "HL7")
        self.assertEqual(response["stored"], 4)

    def test_duplicate_message_is_not_stored_twice(self):
        self.engine.process(SAMPLES["astm_cbc_normal"], ANALYZER)
        second = self.engine.process(SAMPLES["astm_cbc_normal"], ANALYZER)
        self.assertEqual(second["status"], "DUPLICATE")
        self.assertEqual(second["stored"], 0)
        self.assertEqual(self.store.stats()["total"], 4)

    def test_garbage_is_rejected_but_recorded(self):
        response = self.engine.process(SAMPLES["broken_message"], ANALYZER)
        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(len(self.store.messages), 1)      # raw payload kept
        self.assertEqual(self.store.messages[0]["status"], "ERROR")

    def test_raw_message_is_always_stored(self):
        self.engine.process(SAMPLES["hl7_biochem"], ANALYZER)
        self.assertEqual(len(self.store.messages), 1)
        self.assertIn("MSH|", self.store.messages[0]["raw_message"])

    def test_control_sample_is_ignored_but_acked(self):
        engine = IngestEngine(MemoryStore(),
                              ResultValidator(ignore_sample_prefixes=["QC"]),
                              ACKHandler("ASTM"))
        response = engine.process(SAMPLES["astm_qc_sample"], ANALYZER)
        self.assertTrue(response["ok"])              # analyzer must not get a NAK
        self.assertEqual(response["status"], "IGNORED")
        self.assertEqual(response["stored"], 0)

    def test_mllp_ack_mode(self):
        engine = IngestEngine(MemoryStore(), ResultValidator(), ACKHandler("MLLP"))
        response = engine.process(SAMPLES["hl7_biochem"], ANALYZER)
        self.assertIn("MSA|AA", response["ack"])
        self.assertTrue(response["ack"].startswith("\x0b"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
