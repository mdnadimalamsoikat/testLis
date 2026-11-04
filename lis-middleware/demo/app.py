"""
Browser demo for the LIS middleware.

Runs the exact same pipeline as `main.py` (parse → validate → dedupe → store)
but over HTTP, with an in-memory store, so you can watch an ASTM/HL7 message
travel from "analyzer" to "database" without a lab instrument or an Oracle box.

    pip install fastapi uvicorn jinja2
    python -m demo.app                 # http://localhost:8080
    python -m demo.app --port 9000 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI                     # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel                  # noqa: E402
import uvicorn                                  # noqa: E402

from core.ack_handler import ACKHandler          # noqa: E402
from core.e1381 import E1381Framer               # noqa: E402
from core.engine import IngestEngine             # noqa: E402
from core.parser_factory import sniff_protocol   # noqa: E402
from core.validator import ResultValidator       # noqa: E402
from database.memory_store import MemoryStore    # noqa: E402

CONFIG_PATH = ROOT / "config" / "config.yaml"
SAMPLES_PATH = ROOT / "samples" / "messages.yaml"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


CONFIG = load_yaml(CONFIG_PATH)
SAMPLES: Dict[str, str] = load_yaml(SAMPLES_PATH)

STORE = MemoryStore("DEMO")
VALIDATION = CONFIG.get("validation", {}) or {}
ENGINE = IngestEngine(
    store=STORE,
    validator=ResultValidator(ignore_sample_prefixes=VALIDATION.get("ignore_sample_prefixes")),
    ack_handler=ACKHandler(CONFIG.get("app", {}).get("ack_mode", "ASTM")),
    test_code_map=CONFIG.get("test_code_map", {}),
)

ANALYZERS: List[Dict[str, Any]] = CONFIG.get("analyzers", [])
# The demo always pretends the analyzers are connected; nothing binds a socket.
for analyzer in ANALYZERS:
    analyzer["enabled"] = True


class SendRequest(BaseModel):
    scenario: Optional[str] = None
    raw: Optional[str] = None
    analyzer: Optional[str] = None
    protocol: str = "AUTO"
    # how the payload is framed on the wire: AUTO (one shot) or E1381 (handshake)
    framing: str = "AUTO"
    # which answer path to simulate: the middleware (parse+store) or the device
    mode: str = "middleware"


app = FastAPI(title="LIS Middleware Demo", docs_url="/api/docs")


# ------------------------------------------------------------------ JSON API
@app.get("/api/state")
def state() -> Dict[str, Any]:
    return {
        "stats": ENGINE.stats(),
        "results": STORE.list_results(limit=300),
        "samples": STORE.samples()[:50],
        "messages": STORE.list_messages(limit=40),
        "analyzers": [
            {
                "name": a.get("name"),
                "brand": a.get("brand"),
                "model": a.get("model"),
                "protocol": a.get("protocol"),
                "connection_type": a.get("connection_type"),
                "port": a.get("port") or a.get("serial_port"),
                "test_codes": a.get("test_codes", []),
                "scenario": _default_scenario(a),
            }
            for a in ANALYZERS
        ],
        "scenarios": [
            {
                "key": key,
                "label": _scenario_label(key),
                "protocol": "HL7" if key.startswith("hl7") else "ASTM",
                "lines": len(value.strip().splitlines()),
                "raw": value,
            }
            for key, value in SAMPLES.items()
        ],
        "test_code_map": CONFIG.get("test_code_map", {}),
    }


@app.post("/api/send")
def send(request: SendRequest) -> JSONResponse:
    raw = request.raw
    if not raw and request.scenario:
        raw = SAMPLES.get(request.scenario, "")
    if not raw or not raw.strip():
        return JSONResponse({"ok": False, "note": "empty payload"}, status_code=400)

    analyzer = _pick_analyzer(request.analyzer, request.protocol)
    analyzer = dict(analyzer)
    analyzer["protocol"] = request.protocol or analyzer.get("protocol", "AUTO")

    response = ENGINE.process(raw, analyzer, peer=f"demo:{analyzer['name']}")
    response.update({
        "analyzer_name": analyzer["name"],
        "detected_protocol": sniff_protocol(raw),
        "raw": raw,
        "stats": ENGINE.stats(),
    })
    return JSONResponse(response)


@app.post("/api/wire")
def wire(request: SendRequest) -> JSONResponse:
    """Simulate the *device → middleware* conversation, step by step.

    Returns what the analyzer sends and what the middleware answers, including
    the ENQ/ACK handshake, per-frame checksums and the final ACK/NAK. Nothing is
    stored: this exists to show why a real machine feels different from a form.
    """
    raw = request.raw
    if not raw and request.scenario:
        raw = SAMPLES.get(request.scenario, "")
    if not raw or not raw.strip():
        return JSONResponse({"ok": False, "note": "empty payload"}, status_code=400)

    analyzer = dict(_pick_analyzer(request.analyzer, request.protocol))
    peer = f"wire:{analyzer['name']}"

    if str(request.framing).upper() == "E1381":
        return JSONResponse(_e1381_session(raw.strip(), analyzer, peer))

    # one-shot framing (what the mock tool sends for HL7 / basic ASTM)
    protocol = sniff_protocol(raw)
    response = ENGINE.process(raw, analyzer, peer=peer)
    return JSONResponse({
        "mode": "oneshot",
        "steps": [
            {"dir": "tx", "label": "STX … ETX", "text": raw.strip()[:400],
             "note": "single block, no handshake"},
            {"dir": "rx", "label": "ACK (0x06)" if response["ok"] else "NAK (0x15)",
             "text": repr(response["ack"]), "note": response["note"]},
        ],
        "result": response,
    })


def _e1381_session(raw: str, analyzer: Dict[str, Any], peer: str) -> Dict[str, Any]:
    """Run the real handshake against the engine — in memory, no sockets."""
    from core.e1381 import ENQ, STX, ETX, build_frame, checksum_chars, compute_checksum

    steps: List[Dict[str, Any]] = []
    framer = E1381Framer()
    accepted = False
    collected = ""

    def tx(label: str, text: str, note: str = "") -> None:
        steps.append({"dir": "tx", "label": label, "text": text, "note": note})

    def rx(label: str, text: str = "", note: str = "") -> None:
        steps.append({"dir": "rx", "label": label, "text": text, "note": note})

    # 1. ENQ → ACK
    tx("ENQ (0x05)", "", "analyzer: “I have results to send”")
    rx("ACK (0x06)", "", "middleware: “go ahead”")

    # 2. frames
    blocks = [line for line in raw.replace("\r\n", "\n").split("\n") if line.strip()]
    for index, line in enumerate(blocks, start=1):
        number = ((index - 1) % 8) + 1
        frame = build_frame(line, number)
        size = len(line.encode())
        tx(f"STX {number} … ETX ⟨cs⟩", line,
           f"frame {number}, {size} bytes, checksum {compute_checksum(0x30 + number, line.encode()):02X}")
        events = framer.feed(frame)
        ok = all(event.checksum_ok for event in events if event.kind == "frame")
        collected += line + "\r"
        rx("ACK (0x06)" if ok else "NAK (0x15)", "",
           "frame accepted" if ok else "checksum mismatch → analyzer repeats the frame")

    # 3. EOT → engine
    tx("EOT (0x04)", "", "end of transmission")
    response = ENGINE.process(collected, analyzer, peer=peer)
    accepted = response["ok"]
    rx("engine", "", response["note"])

    return {
        "mode": "e1381",
        "steps": steps,
        "result": response,
        "frames": len(blocks),
        "accepted": accepted,
        "analyzer_name": analyzer["name"],
    }


@app.post("/api/reset")
def reset() -> Dict[str, Any]:
    global STORE, ENGINE
    STORE = MemoryStore("DEMO")
    ENGINE = IngestEngine(
        store=STORE,
        validator=ResultValidator(ignore_sample_prefixes=VALIDATION.get("ignore_sample_prefixes")),
        ack_handler=ACKHandler(CONFIG.get("app", {}).get("ack_mode", "ASTM")),
        test_code_map=CONFIG.get("test_code_map", {}),
    )
    return {"ok": True}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "oracle_configured": bool(CONFIG.get("database", {}).get("password"))}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# -------------------------------------------------------------------- helpers
def _pick_analyzer(name: Optional[str], protocol: str) -> Dict[str, Any]:
    if name:
        for analyzer in ANALYZERS:
            if analyzer.get("name") == name:
                return analyzer
    wanted = "HL7" if str(protocol).upper() == "HL7" else None
    if wanted:
        for analyzer in ANALYZERS:
            if str(analyzer.get("protocol")).upper() == wanted:
                return analyzer
    return ANALYZERS[0] if ANALYZERS else {"name": "DEMO"}


def _default_scenario(analyzer: Dict[str, Any]) -> str:
    if str(analyzer.get("protocol")).upper() == "HL7":
        return "hl7_biochem"
    if str(analyzer.get("connection_type")).upper() == "SERIAL":
        return "astm_merged_unit"
    return "astm_cbc_normal"


def _scenario_label(key: str) -> str:
    return {
        "astm_cbc_normal": "CBC — normal (Sysmex XN)",
        "astm_cbc_abnormal": "CBC — abnormal + critical",
        "hl7_biochem": "Biochemistry — normal (Mindray)",
        "hl7_critical": "Biochemistry — critical glucose",
        "astm_qc_sample": "Quality control sample",
        "astm_merged_unit": "Value+unit merged (Roche)",
        "broken_message": "Broken message (error path)",
    }.get(key, key.replace("_", " "))


def main() -> None:
    parser = argparse.ArgumentParser(description="LIS middleware browser demo")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = parser.parse_args()
    print(f"LIS middleware demo → http://localhost:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
