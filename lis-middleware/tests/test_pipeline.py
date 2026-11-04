"""
Unit tests — no Oracle, no sockets. Run from lis-middleware/:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

import yaml
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ack_handler import ACKHandler              # noqa: E402
from core.e1381 import (ACK, EOT, ENQ, ETX, NAK, STX,  # noqa: E402
                        E1381Framer, build_frame, compute_checksum)
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


class TestE1381Framing(unittest.TestCase):
    """The layer that makes a real analyzer talk: ENQ/frame/ACK/EOT."""

    def test_checksum_matches_the_wire_rule(self):
        # frame number + text + ETX, low byte
        self.assertEqual(compute_checksum(0x31, b"H|\^&|"), (0x31 + sum(b"H|\^&|") + ETX) & 0xFF)

    def test_frame_is_stx_number_text_etx_checksum(self):
        frame = build_frame("R|1|^^^WBC|7.5", 3)
        self.assertEqual(frame[0], STX)
        self.assertEqual(frame[1], ord("3"))
        self.assertEqual(frame[-3], ETX)
        self.assertEqual(len(frame[-2:]), 2)

    def test_round_trip_through_the_framer(self):
        framer = E1381Framer()
        events = framer.feed(bytes([ENQ]) + build_frame("R|1|^^^WBC|7.5", 1) + bytes([EOT]))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["enq", "frame", "eot"])
        self.assertEqual(events[1].text.strip(), "R|1|^^^WBC|7.5")
        self.assertTrue(events[1].checksum_ok)

    def test_bad_checksum_is_reported(self):
        frame = build_frame("L|1|N", 1)
        broken = frame[:-1] + bytes([0x40])
        events = E1381Framer().feed(broken)
        self.assertEqual(events[0].kind, "frame")
        self.assertFalse(events[0].checksum_ok)

    def test_split_frame_across_tcp_segments(self):
        frame = build_frame("R|1|^^^HGB|14.5", 2)
        framer = E1381Framer()
        self.assertEqual(framer.feed(frame[:5]), [])          # nothing yet
        events = framer.feed(frame[5:])
        self.assertEqual(events[0].text.strip(), "R|1|^^^HGB|14.5")
        self.assertTrue(events[0].checksum_ok)

    def test_noise_before_stx_is_resynced(self):
        events = E1381Framer().feed(bytes([0x00, 0x7F]) + build_frame("L|1|N", 1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "frame")
        self.assertTrue(events[0].checksum_ok)

    def test_frame_number_wraps_after_8(self):
        event = E1381Framer().feed(build_frame("L|1|N", 0))[0]   # sent as 0x30
        self.assertEqual(event.frame_number, 0)

    def test_unterminated_text_is_flushed(self):
        framer = E1381Framer()
        framer.feed(b"MSH|^~\\&|A|B|C")
        leftover = framer.flush()
        self.assertIsNotNone(leftover)
        self.assertIn("MSH", leftover.text)


class _FakeCursor:
    """Minimal cursor stand-in so DatabaseControl can be tested without Oracle."""

    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = " ".join(sql.split())
        self.db.executed.append((self._last_sql, params))
        lowered = self._last_sql.lower()
        key = next((k for k in self.db.canned if k in lowered), None)
        self.rows = self.db.canned.get(key, [])
        self.rowcount = 1 if key else 0

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else (0,)

    def close(self):
        pass


class _FakeDB:
    schema = "LIS_USER"

    def __init__(self, canned):
        self.canned = canned
        self.executed = []

    @contextmanager
    def cursor(self):
        cur = _FakeCursor(self)
        try:
            yield cur
        finally:
            cur.close()


class TestApexControl(unittest.TestCase):
    """The APEX-driven configuration layer."""

    def _db(self):
        return _FakeDB({
            "config_type = 'analyzer'": [
                # name, enabled, protocol, conn, host, port, serial, baud, bits,
                # parity, stop, brand, model, test_codes, code_map, version
                ("Sysmex_XN_1000", "Y", "ASTM", "TCP", "0.0.0.0", 6661, None, 9600, 8,
                 "NONE", 1, "Sysmex", "XN-1000", "WBC,RBC,HGB", '{"HGB":"HB"}', 3),
            ],
            "config_type = 'setting'": [
                ("ACK_MODE", "MLLP"),
                ("LOG_LEVEL", "DEBUG"),
                ("IGNORE_SAMPLE_PREFIXES", "QC,CTRL"),
            ],
            "lis_test_code_map": [("GLU", "FBS")],
            "sum(config_version)": [(42, 7, 9)],
        })

    def test_snapshot_is_built_from_control_tables(self):
        from database.control import DatabaseControl
        control = DatabaseControl(self._db())
        snapshot = control.load_config()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.source, "database")
        self.assertEqual(len(snapshot.analyzers), 1)
        analyzer = snapshot.analyzers[0]
        self.assertEqual(analyzer.name, "Sysmex_XN_1000")
        self.assertEqual(analyzer.port, 6661)
        self.assertEqual(analyzer.test_codes, ["WBC", "RBC", "HGB"])
        self.assertEqual(analyzer.test_code_map, {"HGB": "HB"})
        self.assertEqual(snapshot.settings.ack_mode, "MLLP")
        self.assertEqual(snapshot.settings.ignore_sample_prefixes, ["QC", "CTRL"])
        self.assertEqual(snapshot.test_code_map, {"GLU": "FBS"})
        self.assertEqual(snapshot.problems(), [])

    def test_disabled_analyzer_is_not_started(self):
        from database.control import AnalyzerConfig, ConfigSnapshot
        snapshot = ConfigSnapshot(analyzers=[
            AnalyzerConfig(name="A", enabled=True, port=6661),
            AnalyzerConfig(name="B", enabled=False, port=6662),
        ])
        self.assertEqual([a["name"] for a in snapshot.analyzer_dicts()], ["A"])

    def test_duplicate_ports_are_refused(self):
        from database.control import AnalyzerConfig, ConfigSnapshot
        snapshot = ConfigSnapshot(analyzers=[
            AnalyzerConfig(name="A", port=6661),
            AnalyzerConfig(name="B", port=6661),
        ])
        problems = snapshot.problems()
        self.assertTrue(any("used by both" in p for p in problems))

    def test_bad_values_are_refused_with_a_readable_message(self):
        from database.control import AnalyzerConfig
        problems = AnalyzerConfig(name="A", protocol="XYZ", port=80).validate()
        self.assertTrue(any("protocol" in p for p in problems))
        self.assertTrue(any("port" in p for p in problems))

    def test_serial_analyzer_needs_a_port(self):
        from database.control import AnalyzerConfig
        problems = AnalyzerConfig(name="A", transport="SERIAL", port=0).validate()
        self.assertTrue(any("serial_port" in p for p in problems))

    def test_fingerprint_ignores_bookkeeping_columns(self):
        """applied_at is written by the service; it must not look like an edit."""
        from database.control import DatabaseControl
        db = _FakeDB({})
        DatabaseControl(db).config_version()
        sql = db.executed[0][0]
        self.assertNotIn("applied_at", sql.lower())
        self.assertIn("config_version", sql.lower())

    def test_service_does_not_reload_on_its_own_ticks(self):
        """Two consecutive fingerprints must be equal when nothing was edited."""
        from database.control import DatabaseControl
        db = _FakeDB({"sum(config_version)": [(42, 7, 9)]})
        control = DatabaseControl(db)
        self.assertEqual(control.config_version(), control.config_version())

    def test_commands_are_claimed_once(self):
        from database.control import DatabaseControl
        db = _FakeDB({"status = 'pending'": [(11, "reload", None, None)]})
        control = DatabaseControl(db)
        claimed = control.claim_commands()
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["command_type"], "RELOAD")
        # it must have been marked RUNNING inside the same transaction
        self.assertTrue(any(sql.lower().startswith("update lis_user.lis_commands") or
                            "status = 'running'" in sql.lower() for sql, _ in db.executed))

    def test_file_control_still_works(self):
        from database.control import FileControl
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "config", "config.yaml")
        control = FileControl(config_path)
        snapshot = control.load_config()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.source, "file")
        self.assertGreaterEqual(len(snapshot.analyzers), 2)


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _SupervisorControl:
    """Stands in for the Oracle control tables: gives service.py a snapshot."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.applied = []
        self.errors = []
        self.statuses = []

    def load_config(self):
        return self.snapshot

    def config_version(self):
        return self.snapshot.version if self.snapshot else ""

    def mark_applied(self, version, by):
        self.applied.append(version)

    def set_last_error(self, message, analyzer=None):
        self.errors.append(message)

    def write_status(self, statuses):
        self.statuses = statuses

    def claim_commands(self, limit=5):
        return []

    def finish_command(self, command_id, ok, message=""):
        pass

    def insert_audit(self, event, analyzer, detail):
        pass


class TestSupervisorHotReload(unittest.IsolatedAsyncioTestCase):
    """The promise: an APEX edit applies without a restart, and only to what changed."""

    def _snapshot(self, analyzers, protocol="ASTM", version="1"):
        from database.control import AnalyzerConfig, ConfigSnapshot, RuntimeSettings
        return ConfigSnapshot(analyzers=analyzers, settings=RuntimeSettings(),
                              version=version, source="database")

    async def asyncSetUp(self):
        import service
        from database.memory_store import MemoryStore
        self.service = service
        self.store = MemoryStore()

    async def test_only_the_changed_listener_is_restarted(self):
        from database.control import AnalyzerConfig
        port_a, port_b = _free_port(), _free_port()
        first = self._snapshot([AnalyzerConfig(name="A", port=port_a)], version="1")
        control = _SupervisorControl(first)
        sup = self.service.Supervisor(control, lambda: self.store, dry_run=True)
        await sup.start()
        listener_a = sup.listeners["A"]
        self.assertTrue(listener_a.status()["running"])

        second = self._snapshot([AnalyzerConfig(name="A", port=port_a),
                                 AnalyzerConfig(name="B", port=port_b)], version="2")
        control.snapshot = second
        result = await sup.reload(triggered_by="test")

        self.assertTrue(result["ok"], result)
        self.assertIs(sup.listeners["A"], listener_a)      # A untouched
        self.assertIn("B", sup.listeners)
        self.assertEqual(control.applied[-1], "2")
        await sup.shutdown()

    async def test_changing_a_port_moves_the_listener(self):
        from database.control import AnalyzerConfig
        old_port, new_port = _free_port(), _free_port()
        control = _SupervisorControl(self._snapshot([AnalyzerConfig(name="A", port=old_port)]))
        sup = self.service.Supervisor(control, lambda: self.store, dry_run=True)
        await sup.start()

        control.snapshot = self._snapshot([AnalyzerConfig(name="A", port=new_port)], version="2")
        await sup.reload(triggered_by="test")

        self.assertEqual(sup.listeners["A"].port, new_port)
        self.assertTrue(sup.listeners["A"].status()["running"])
        await sup.shutdown()

    async def test_an_invalid_row_is_refused_and_the_old_config_keeps_running(self):
        from database.control import AnalyzerConfig
        port = _free_port()
        control = _SupervisorControl(self._snapshot([AnalyzerConfig(name="A", port=port)]))
        sup = self.service.Supervisor(control, lambda: self.store, dry_run=True)
        await sup.start()

        control.snapshot = self._snapshot([AnalyzerConfig(name="A", port=port, protocol="XYZ")],
                                          version="2")
        result = await sup.reload(triggered_by="test")

        self.assertFalse(result["ok"])
        self.assertIn("protocol", result["message"])
        self.assertTrue(control.errors)                      # written back to APEX
        self.assertEqual(sup.listeners["A"].port, port)       # still the working one
        self.assertTrue(sup.listeners["A"].status()["running"])
        await sup.shutdown()

    async def test_selftest_runs_through_the_live_engine(self):
        from database.control import AnalyzerConfig
        control = _SupervisorControl(self._snapshot([AnalyzerConfig(name="A", port=_free_port())]))
        sup = self.service.Supervisor(control, lambda: self.store, dry_run=True)
        await sup.start()
        ok, message = await sup._selftest("A", "astm_cbc_normal")
        self.assertTrue(ok, message)
        self.assertIn("stored", message)
        await sup.shutdown()

    async def test_heartbeat_reports_every_listener(self):
        from database.control import AnalyzerConfig
        control = _SupervisorControl(self._snapshot([AnalyzerConfig(name="A", port=_free_port())]))
        sup = self.service.Supervisor(control, lambda: self.store, dry_run=True)
        await sup.start()
        await sup.tick()
        self.assertEqual([s["name"] for s in control.statuses], ["A"])
        self.assertTrue(control.statuses[0]["running"])
        await sup.shutdown()


class TestInstallerTools(unittest.TestCase):
    """tools/apex_setup.py + tools/install_sql.py must never mangle the SQL."""

    def _sql(self, name: str) -> str:
        path = Path(__file__).resolve().parents[1] / "database" / name
        return path.read_text(encoding="utf-8")

    def test_apex_setup_splits_every_statement(self):
        from tools.apex_setup import split_statements
        statements = split_statements(self._sql("apex_install.sql"))
        tables = [s for s in statements if s.upper().startswith("CREATE TABLE")]
        triggers = [s for s in statements if "TRIGGER" in s.upper()[:40]]
        inserts = [s for s in statements if s.upper().startswith("INSERT")]
        self.assertEqual(len(tables), 3)          # lis_config, code map, commands
        self.assertEqual(len(triggers), 2)
        self.assertEqual(len(inserts), 12)        # 3 analyzers + 5 settings + 4 code maps
        # no statement may swallow the next one
        for statement in statements:
            self.assertEqual(statement.upper().count("CREATE "), 1 if statement.upper().startswith("CREATE") else 0,
                             statement[:80])
        # PL/SQL blocks keep their END;
        for trigger in triggers:
            self.assertTrue(trigger.rstrip().endswith("END;"), trigger[-60:])

    def test_install_sql_blocks_are_terminated_for_sqlplus(self):
        from tools.install_sql import split_blocks
        blocks = split_blocks(self._sql("apex_install.sql"))
        plsql = [text for text, is_plsql in blocks if is_plsql]
        self.assertEqual(len(plsql), 2)
        for text in plsql:
            self.assertTrue(text.rstrip().endswith("END;"))
        for text, is_plsql in blocks:
            if not is_plsql:
                self.assertTrue(text.rstrip().endswith(";") or text.strip().upper() == "COMMIT;")
                self.assertNotEqual(text.strip(), "/")

    def test_schema_sql_has_the_result_tables(self):
        from tools.apex_setup import split_statements
        statements = split_statements(self._sql("schema.sql"))
        creates = [s for s in statements if s.upper().startswith("CREATE TABLE")]
        names = {s.split("(", 1)[0].split()[-1].lower() for s in creates}
        self.assertEqual(names, {"lis_raw_messages", "lab_results", "lis_audit_log",
                                 "lis_analyzer_status"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
