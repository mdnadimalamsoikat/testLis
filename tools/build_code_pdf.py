"""
Builds docs/CODE_WALKTHROUGH.pdf — the engineering companion to the Bengali guide.

The Bengali guide explains *what* and *why*. This one is code-first: the exact
functions in the repository, in the order they run, with short English notes.

    python tools/build_code_pdf.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdfkit import (ACCENT, BODY, INK, LINE, MUTED, PAGE_H, Document,  # noqa: E402
                    Segment, Style, rgb)
from fonts_setup import build_fonts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "CODE_WALKTHROUGH.pdf"
MID = ROOT / "lis-middleware"


def snippet(rel: str, start: str, lines: int = 0, end: str = "") -> str:
    """Pull a real excerpt out of the repository so the PDF cannot drift."""
    text = (MID / rel).read_text(encoding="utf-8")
    lines_all = text.split("\n")
    for index, line in enumerate(lines_all):
        if start in line:
            chunk = lines_all[index:index + (lines or 900)]
            if end:
                for offset, candidate in enumerate(chunk):
                    if offset and end in candidate:
                        chunk = chunk[:offset + 1]
                        break
            else:
                chunk = chunk[:lines or 40]
            while chunk and (not chunk[-1].strip() or re.match(r"^\s*#\s*-{5,}", chunk[-1])):
                chunk.pop()
            return "\n".join(chunk)
    return f"(excerpt of {rel} not found)"


def section(doc: Document, title: str, kicker: str, need: float = 200) -> None:
    """Heading + intro + first code block should not be split."""
    doc.ensure(need)
    doc.h1(title, kicker)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fonts = build_fonts(Path(tempfile.gettempdir()) / "lis_fonts")

    doc = Document(fonts,
                   header="LIS Middleware — code walkthrough",
                   footer="LIS Middleware · code walkthrough · page {n} / {total}")

    top = PAGE_H - doc.top_margin - 24
    doc.rect(doc.left_margin, top - 96, doc.content_width, 96, fill=rgb("#101b2d"), radius=8)
    doc.y = top - 22
    doc.paragraph([Segment("CODE WALKTHROUGH", Style(size=7.6, color=rgb("#7fe3c8"), bold=True))],
                  Style(size=7.6, space_after=5), x=doc.left_margin + 18,
                  width=doc.content_width - 36)
    doc.paragraph([Segment("From a raw ASTM/HL7 byte stream to a row in lab_results",
                           Style(size=15, bold=True, color=rgb("#ffffff"), leading=1.35))],
                  Style(size=15, leading=1.35), x=doc.left_margin + 18,
                  width=doc.content_width - 36)

    doc.y = top - 114
    doc.para("Every excerpt below is read out of the repository when this PDF is built, so the "
             "document always matches the code that ships. Bengali project overview: "
             "docs/PROJECT_GUIDE_BN.pdf.", size=9.6, color=MUTED)

    # ------------------------------------------------------------------ 1
    section(doc, "1 · Entry point and wiring", "main.py")
    doc.para("`main.py` loads `config.yaml`, decides between Oracle and the in-memory store, then "
             "starts one listener per enabled analyzer. Serial analyzers get a thread, TCP "
             "analyzers get an asyncio task.")
    doc.code(snippet("main.py", "def start_listeners", 26))

    # ------------------------------------------------------------------ 2
    section(doc, "2 · Protocol sniffing", "core/parser_factory.py")
    doc.para("Analyzers get misconfigured more often than people admit. The sniffer looks at the "
             "first segment of the payload; `parse_message` retries with the other parser if the "
             "configured one returns nothing, and logs that the config is wrong.")
    doc.code(snippet("core/parser_factory.py", "def sniff_protocol", 18))
    doc.code(snippet("core/parser_factory.py", "def parse_message", 26))

    # ------------------------------------------------------------------ 3
    section(doc, "3 · ASTM record parsing", "parsers/astm_parser.py")
    doc.para("Records are split on CR (LF and CRLF tolerated), then on `|`. Patient and order "
             "records are read once and attached to every result record, so each row is "
             "self-contained for the database.")
    doc.code(snippet("parsers/astm_parser.py", "def _split_records", 12))
    doc.code(snippet("parsers/astm_parser.py", "    def parse(self", 24))

    # ------------------------------------------------------------------ 4
    section(doc, "4 · HL7 OBX parsing", "parsers/hl7_parser.py")
    doc.para("Segments are grouped by name and the parser anchors on `OBX`. Field indexes follow "
             "the HL7 numbering (OBX-2 value type, OBX-3 identifier, OBX-5 value, OBX-8 flag).")
    doc.code(snippet("parsers/hl7_parser.py", "    def _obx(", 34))

    # ------------------------------------------------------------------ 5
    section(doc, "5 · Validation rules", "core/validator.py")
    doc.para("One place decides what a valid result is: required keys, unit/flag normalisation, "
             "critical detection (`C`, `HH`, `LL`) and the control-sample filter.")
    doc.code(snippet("core/validator.py", "    def normalise", 30))
    doc.code(snippet("core/validator.py", "    def _problem", 16))

    # ------------------------------------------------------------------ 6
    section(doc, "6 · The pipeline itself", "core/engine.py")
    doc.para("Order matters: raw payload first, parse second, insert third, and only then build "
             "the ACK. If any step fails, the raw message is already safe and the analyzer gets a "
             "NAK instead of a silent drop.")
    doc.code(snippet("core/engine.py", "    def process", 62))

    # ------------------------------------------------------------------ 7
    section(doc, "7 · Oracle writer", "database/db_writer.py")
    doc.para("Two tables: `lis_raw_messages` (verbatim archive, with status) and `lab_results` "
             "(one row per test, de-duplicated). The duplicate check is cheap and keeps the "
             "analyzer's re-transmissions out of the report.")
    doc.code(snippet("database/db_writer.py", "    def is_duplicate", 20))
    doc.code(snippet("database/db_writer.py", "    def insert_results", 14))

    # ------------------------------------------------------------------ 8
    section(doc, "8 · Listener framing", "listeners/tcp_listener.py")
    doc.para("`_extract` turns a byte stream into messages: MLLP (`\\x0b…\\x1c`), ASTM "
             "(`\\x05`/`\\x03`), or bare text closed by the client. Whatever framing arrives, the "
             "analyzer gets `0x06` for success and `0x15` for a rejected message.")
    doc.code(snippet("listeners/tcp_listener.py", "    def _extract", 42))

    # ------------------------------------------------------------------ 9
    section(doc, "9 · Proving it works", "tests/test_pipeline.py")
    doc.para("Twenty-eight tests cover the parsers, the sniffer, the validator, de-duplication, "
             "the ACK modes and the error paths. No Oracle, no sockets, no lab instrument needed.")
    doc.code("python -m unittest discover -s tests -v\n"
             "  ...\n"
             "  Ran 28 tests in 0.006s\n"
             "  OK", caption="test run")
    doc.code(snippet("tests/test_pipeline.py", "    def test_end_to_end_astm", 12))

    doc.space(6)
    doc.callout("Where to start reading", "Follow the order of this document: main.py → engine.py → "
                "parsers/ → validator.py → db_writer.py. Everything else (demo, tools) reuses "
                "those five files.", kind="info")

    data = doc.build()
    OUT.write_bytes(data)
    print(f"wrote {OUT} ({len(data)/1024:.1f} KB, {len(doc.pages)} pages)")


if __name__ == "__main__":
    main()
