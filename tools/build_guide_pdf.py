"""
Builds docs/PROJECT_GUIDE_BN.pdf — the Bengali project guide.

    pip install uharfbuzz fonttools
    python tools/build_guide_pdf.py

Content is written as code (headings/paragraphs/blocks) so the PDF and the
repository can never drift apart: rerun the script after changing the code.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdfkit import (ACCENT, ACCENT_SOFT, BODY, DANGER, INK, LINE, MUTED, OK,  # noqa: E402
                    PAGE_H, PANEL, WARN, Document, Segment, Style, rgb)
from fonts_setup import build_fonts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "PROJECT_GUIDE_BN.pdf"

TITLE = "ল্যাব ইনফরমেশন সিস্টেম (LIS)"
SUBTITLE = "Analyzer → Middleware → Oracle Database → Oracle APEX"


def cover(doc: Document) -> None:
    top = PAGE_H - doc.top_margin - 30

    # accent band
    doc.rect(doc.left_margin, top - 118, doc.content_width, 118, fill=rgb("#0b2d3a"), radius=8)
    doc.y = top - 26
    doc.paragraph([Segment("PROJECT GUIDE  ·  v1.0", Style(size=8, color=rgb("#7fe3c8"), bold=True))],
                  Style(size=8, space_after=6), x=doc.left_margin + 20, width=doc.content_width - 40)
    doc.paragraph([Segment(TITLE, Style(size=21, bold=True, color=rgb("#ffffff"), leading=1.35))],
                  Style(size=21, leading=1.35, space_after=6),
                  x=doc.left_margin + 20, width=doc.content_width - 40)
    doc.paragraph([Segment(SUBTITLE, Style(size=9.5, color=rgb("#a8c6d4")))],
                  Style(size=9.5), x=doc.left_margin + 20, width=doc.content_width - 40)

    doc.y = top - 136
    doc.para("আপনার ল্যাবের মেশিন (Sysmex, Mindray, Roche, Abbott) থেকে রিপোর্ট সরাসরি Oracle ডেটাবেসে "
             "আনার সম্পূর্ণ সমাধান — কোড, আর্কিটেকচার, সেটআপ, টেস্ট আর troubleshooting সহ।", size=10.5)

    doc.space(2)
    rows = [
        ["স্তর", "বিস্তারিত"],
        ["Frontend", "Oracle APEX — রিপোর্ট, ড্যাশবোর্ড, critical alert"],
        ["Database", "Oracle 19c / 21c / 23ai — thin mode, কোনো Oracle Client দরকার নেই"],
        ["নিয়ন্ত্রণ", "সব কনফিগ APEX পেজ থেকে — Python সার্ভারে কেউ ঢুকবে না"],
        ["Middleware", "Python 3.10+ — ASTM ও HL7 parser, TCP ও Serial listener"],
        ["Analyzer", "Sysmex XN (ASTM/TCP), Mindray BS-240 (HL7/TCP), Roche Cobas (ASTM/Serial)"],
        ["ডেমো", "ব্রাউজারে চলা mock analyzer — real machine ছাড়াই পুরো flow টেস্ট করা যায়"],
    ]
    doc.table(rows[0], rows[1:], widths=[95, 380], size=9)

    doc.space(2)
    doc.callout("এই ডকুমেন্টে যা পাবেন",
                "1) কেন middleware লাগে  2) পুরো আর্কিটেকচার  3) ফাইল ও কোড  4) ডেটাবেস স্কিমা  "
                "5) APEX রিপোর্ট ও APEX-চালিত কন্ট্রোল  6) সেটআপ ধাপে ধাপে  7) টেস্ট ও মক অ্যানালাইজার  "
                "8) ডিপ্লয়মেন্ট  9) সমস্যা ও সমাধান  10) চেকলিস্ট", kind="info")

    doc.space(10)
    doc.h2("সূচিপত্র", "")
    toc = [
        ["১", "কেন মিডলওয়্যার দরকার", "Mirth নাকি নিজের কোড — সিদ্ধান্তের ভিত্তি"],
        ["২", "আর্কিটেকচার", "পাঁচ স্তর, প্রতিটির দায়িত্ব"],
        ["৩", "কোড", "ASTM/HL7 ফরম্যাট, pipeline, ACK, ডুপ্লিকেট"],
        ["৪", "ডেটাবেস স্কিমা", "টেবিল, ইনডেক্স, APEX ভিউ"],
        ["৫", "Oracle APEX", "রিপোর্ট, ফর্ম, critical alert + APEX থেকে সব নিয়ন্ত্রণ"],
        ["৬", "সেটআপ ও ল্যাব ডে", "স্কিমা → config → preflight → মেশিন সেটিং → ল্যাবের ক্রম"],
        ["৭", "টেস্ট", "৫০টি ইউনিট টেস্ট, মক অ্যানালাইজার, ব্রাউজার ডেমো"],
        ["৮", "ডিপ্লয়মেন্ট", "Windows সার্ভিস, Linux systemd"],
        ["৯", "সমস্যা ও সমাধান", "লক্ষণ → কারণ → সমাধান"],
        ["১০", "চেকলিস্ট", "প্রোডাকশনে যাওয়ার আগে"],
    ]
    doc.table(["#", "বিভাগ", "কী আছে"], toc, widths=[30, 130, 300], size=8.6)

    doc.space(6)
    doc.para("**গুরুত্বপূর্ণ:** এই সফটওয়্যার রেজাল্ট শুধু পরিবহন ও সংরক্ষণ করে — কোনো ভ্যালু নিজে হিসাব করে না। "
             "প্রতিটি analyzer-এর manual আর ল্যাবের SOP অনুযায়ী validation করুন, তারপর প্রোডাকশনে দিন।",
             size=9, color=MUTED)


def section_why(doc: Document) -> None:
    doc.page_break()
    doc.h1("কেন মিডলওয়্যার দরকার", "বিভাগ ১")

    doc.para("ল্যাব অ্যানালাইজার Oracle ডেটাবেস চেনে না। মেশিন একটা সিরিয়াল কেবল বা LAN কেবল দিয়ে "
             "শুধু **টেক্সট** পাঠায় — ASTM (E1394) অথবা HL7 v2। Oracle চায় **SQL INSERT**। "
             "দুই ভাষার মাঝখানে যে অনুবাদক বসে, সেটাই মিডলওয়্যার।")

    doc.code(
        "ASTM (মেশিন যা পাঠায়)                       SQL (ডেটাবেস যা বোঝে)\n"
        "---------------------------------------       ---------------------------------\n"
        "H|\\^&|||Sysmex^XN-1000|||||||...             INSERT INTO lab_results (\n"
        "P|1||2024001234||Karim, Ahmed||19850615|M        sample_id, test_code,\n"
        "O|1|SAMPLE-0001||^^^CBC|R                        result_value, result_unit\n"
        "R|1|^^^WBC^6690-2|7.5|10*3/uL|4.0-11.0|N|F    ) VALUES (\n"
        "L|1|N                                            'SAMPLE-0001', 'WBC', 7.5, '10*3/uL'\n"
        "                                             );",
        caption="একই রেজাল্ট, দুই রকম ফরম্যাট"
    )

    doc.h2("তিনটি পথ — কোনটা আপনার জন্য", "১.১")
    rows = [
        ["বিকল্প", "সুবিধা", "অসুবিধা", "কখন ভালো"],
        ["Mirth Connect", "ফ্রি কোর, HL7+MLLP নেটিভ, GUI", "ASTM E1394/E1381 আলাদা কমার্শিয়াল এক্সটেনশন; Java দরকার", "HL7-ভাষী analyzer, একাধিক মেশিন"],
        ["নিজের কোড (এই প্রজেক্ট)", "পূর্ণ নিয়ন্ত্রণ, কোনো লাইসেন্স নেই, Oracle-native", "নিজে মেইনটেইন করতে হবে", "১-৩টি analyzer, কোড শেখার ইচ্ছা"],
        ["Vendor middleware", "ভেন্ডর সাপোর্ট", "ব্যয়বহুল, একই ব্র্যান্ডে সীমাবদ্ধ", "এন্টারপ্রাইজ, বাজেট আছে"],
    ]
    doc.table(rows[0], rows[1:], widths=[110, 150, 130, 130], size=8.2)

    doc.callout("সিদ্ধান্ত", "আপনি নিজের কোড চেয়েছেন — তাই এই প্রজেক্টে **সম্পূর্ণ Python middleware** "
                "বানানো হয়েছে। Mirth-এর মতোই কাজ করে, কিন্তু কোনো তৃতীয় পক্ষের উপর নির্ভর নেই।",
                kind="ok")


def section_architecture(doc: Document) -> None:
    doc.h1("আর্কিটেকচার", "বিভাগ ২")

    doc.para("পাঁচটি স্তর। প্রতিটি স্তরের কাজ আলাদা ফাইলে, তাই একটা বদলালে বাকিগুলো ভাঙে না।")
    doc.code(
        "MACHINES                          PROTOCOL / PORT\n"
        "  Sysmex XN-1000                  ASTM over TCP  :6661\n"
        "  Mindray BS-240                  HL7 over TCP   :6662\n"
        "  Roche Cobas c311                ASTM / RS-232  COM3\n"
        "      |  raw text payload (ASTM or HL7)\n"
        "      v\n"
        "LIS MIDDLEWARE (this project)\n"
        "  1 listener   accept, strip MLLP/ASTM framing, one task per analyzer\n"
        "  2 parser     text -> structured result dicts (ASTM / HL7)\n"
        "  3 validator  required fields, flags, test-code mapping\n"
        "  4 db writer  duplicate check, INSERT, raw archive\n"
        "  5 ack        reply so the machine keeps sending\n"
        "      |  SQL (python-oracledb, thin mode)\n"
        "      v\n"
        "ORACLE DATABASE   lis_raw_messages  +  lab_results\n"
        "      |  SELECT / views\n"
        "      v\n"
        "ORACLE APEX       reports, dashboard, critical alerts",
        size=7.6,
        caption="ডেটা প্রবাহ (এক দিক) + ACK (ফেরত পথ)"
    )

    doc.h2("প্রতিটি স্তর কী করে", "২.১")
    rows = [
        ["স্তর", "ফাইল", "দায়িত্ব"],
        ["Listener", "listeners/tcp_listener.py\nlisteners/serial_listener.py", "পোর্ট খোলা রাখা, একাধিক মেশিন একসাথে সামলানো, MLLP/ASTM ফ্রেমিং ভাঙা, ACK পাঠানো"],
        ["Parser", "parsers/astm_parser.py\nparsers/hl7_parser.py", "টেক্সট → স্ট্রাকচার্ড dict: sample, patient, test code, value, unit, range, flag"],
        ["Validator", "core/validator.py", "বাধ্যতামূলক ফিল্ড, flag ঠিক করা (H/L/HH/LL), critical শনাক্ত, test code mapping"],
        ["Writer", "database/db_writer.py", "ডুপ্লিকেট চেক করে INSERT, raw message সংরক্ষণ, audit log"],
        ["Engine", "core/engine.py", "উপরের সব ধাপ সঠিক ক্রমে সাজানো — একমাত্র জায়গা যেখানে order বোঝা যায়"],
    ]
    doc.table(rows[0], rows[1:], widths=[70, 150, 260], size=8.2)

    doc.callout("নকশার মূল সিদ্ধান্ত", "**raw message আগে সংরক্ষণ, তারপর parse।** কোনো কারণে parser ব্যর্থ হলে "
                "ডেটা হারায় না — পরে tools/replay.py দিয়ে একই মেসেজ আবার প্রসেস করা যায়।", kind="ok")


def section_code(doc: Document) -> None:
    doc.h1("কোড — ফাইল ধরে ধরে", "বিভাগ ৩")

    doc.h2("প্রজেক্ট স্ট্রাকচার", "৩.১")
    doc.code(
        "lis-middleware/\n"
        "├── service.py               # production: APEX-driven + hot reload\n"
        "├── main.py                  # dev: start every listener once\n"
        "├── config/config.yaml       # DB host/user, log level, fallback list\n"
        "├── core/\n"
        "│   ├── engine.py            # raw → parse → validate → insert → ACK\n"
        "│   ├── parser_factory.py    # protocol sniff + parser selection\n"
        "│   ├── validator.py         # rules + normalisation\n"
        "│   └── ack_handler.py       # ASTM 0x06 ACK / MLLP ACK builder\n"
        "├── parsers/{astm_parser,hl7_parser}.py\n"
        "├── listeners/{tcp_listener,serial_listener}.py\n"
        "├── database/{db_connection,db_writer,control}.py\n"
        "│   + schema.sql            # results tables\n"
        "│   + apex_install.sql      # APEX control plane (lis_config ...)\n"
        "├── demo/app.py             # browser demo (same engine)\n"
        "├── ops/                    # systemd / NSSM service installers\n"
        "├── tools/{device_sim,preflight,sniff_com,replay_capture}.py\n"
        "├── samples/messages.yaml   # real-shaped ASTM / HL7 payloads\n"
        "└── tests/test_pipeline.py  # 50 tests, no Oracle needed"
    )

    doc.h2("ASTM মানে কী — এক নজরে", "৩.২")
    doc.para("ASTM E1394 মেসেজের প্রতিটি লাইন একেকটা **record**। প্রথম অক্ষরই বলে দেয় এটা কী:")
    rows = [
        ["Record", "অর্থ", "আমাদের দরকার?"],
        ["H", "Header — মেশিনের নাম, সময়", "হ্যাঁ (instrument, timestamp)"],
        ["P", "Patient — রোগীর আইডি, নাম, বয়স", "হ্যাঁ"],
        ["O", "Order — স্যাম্পল আইডি, কোন টেস্ট", "হ্যাঁ"],
        ["R", "Result — টেস্ট কোড, ভ্যালু, ইউনিট, রেঞ্জ, flag", "হ্যাঁ — মূল ডেটা"],
        ["C / L", "Comment / Terminator", "না"],
    ]
    doc.table(rows[0], rows[1:], widths=[60, 300, 130], size=8.4)

    doc.para("একটা R লাইন ভেঙে দেখলে:")
    doc.code(
        "R|1|^^^WBC^6690-2|7.5|10*3/uL|4.0-11.0|N||F\n"
        "│ │ │            │   │        │        │ │  └─ F = final\n"
        "│ │ │            │   │        │        │ └──── abnormal flag (N/H/L/HH/LL)\n"
        "│ │ │            │   │        │        └────── reference range\n"
        "│ │ │            │   │        └─────────────── unit\n"
        "│ │ │            │   └──────────────────────── value\n"
        "│ │ └────────────┴──────────────────────────── ^^^TEST^LOINC  (কোড ৪র্থ অংশে)\n"
        "│ └────────────────────────────────────────── record serial\n"
        "└──────────────────────────────────────────── record type",
        caption="ASTM R record-এর ফিল্ড ম্যাপিং"
    )

    doc.h2("HL7 মানে কী", "৩.৩")
    doc.para("HL7 v2 হলো segment-ভিত্তিক: প্রতিটি লাইন `SEG|field1|field2...`। রেজাল্ট থাকে **OBX**-এ, "
             "রোগীর তথ্য **PID**-এ, স্যাম্পল **OBR**-এ। MSH দিয়ে শুরু, তাই MSH-1 ফিল্ডটাই separator — "
             "এখানেই বেশিরভাগ মানুষ এক ঘর করে ভুল করে।")
    doc.code(
        "MSH|^~\\&|MINDRAY|BS240|LISMW|HOSPITAL|20240115110000||ORU^R01|MSG00042|P|2.5.1\n"
        "PID|1||2024009012||Akter^Fatema||19780311|F          ← রোগী\n"
        "OBR|1||SAMPLE-0003|CHEM7                              ← স্যাম্পল\n"
        "OBX|1|NM|GLU^Glucose^2345-7||5.4|mmol/L|3.9-5.5|N|||F\n"
        "               │  │     │       │   │      │       └─ flag\n"
        "               │  │     │       │   │      └───────── range\n"
        "               │  │     │       │   └──────────────── unit\n"
        "               │  │     │       └──────────────────── value\n"
        "               │  └─────┴─────────────────────────── CODE^TEXT^LOINC\n"
        "               └──────────────────────────────────── value type (NM/ST/TX)"
    )

    doc.h2("Pipeline — core/engine.py", "৩.৪")
    doc.para("পুরো সিস্টেমের হৃদয়। ছয়টি ধাপ, প্রতিটির আগে-পরে লগ থাকে:")
    doc.code(
        "def process(self, raw, analyzer, peer=\"n/a\"):\n"
        "    message_id = self.store.log_raw_message(...)   # 1. keep raw payload\n"
        "    protocol   = sniff_protocol(raw)               # 2. ASTM or HL7?\n"
        "    parsed     = parse_message(raw, ...)           # 3. text -> dicts\n"
        "    validated  = self.validator.validate(parsed)   # 4. apply rules\n"
        "    stored     = self.store.insert_results(...)    # 5. dedupe + INSERT\n"
        "    ack        = self.ack.build(ok)                # 6. reply to analyzer\n"
        "    return {...}",
        caption="৬ ধাপের পাইপলাইন — ব্যর্থ হলেও raw ডেটা থাকে"
    )

    doc.h2("কেন ACK এত গুরুত্বপূর্ণ", "৩.৫")
    doc.para("বেশিরভাগ অ্যানালাইজার ACK না পেলে **পরের স্যাম্পল পাঠায় না** — মেশিন আটকে যায়। "
             "দুই রকম ACK আছে, analyzer-এর manual দেখে বেছে নিতে হয়:")
    rows = [
        ["Mode", "কী পাঠাতে হয়", "config"],
        ["ASTM (E1381)", "এক বাইট: 0x06 (ACK) বা 0x15 (NAK)", "app.ack_mode: ASTM"],
        ["HL7 MLLP", "পূর্ণ MSA সেগমেন্ট সহ একটা মেসেজ, \\x0b...\\x1c\\r", "app.ack_mode: MLLP"],
    ]
    doc.table(rows[0], rows[1:], widths=[110, 260, 130], size=8.4)

    doc.h2("ডুপ্লিকেট কেন হয়, কীভাবে থামে", "৩.৬")
    doc.para("মেশিন একই রেজাল্ট দুইবার পাঠায় — নেটওয়ার্ক টাইমআউট হলে, বা অপারেটর আবার স্যাম্পল চালালে। "
             "তিন স্তরে সুরক্ষা: **INSERT-এর আগে COUNT চেক**, **UNIQUE constraint** "
             "(sample_id, test_code, analyzer_name, result_value), আর **raw status = DUPLICATE**।")
    doc.code(
        "SELECT COUNT(*) FROM lab_results\n"
        " WHERE sample_id = :sid AND test_code = :tc\n"
        "   AND analyzer_name = :an AND result_value = :val;\n"
        "-- when count > 0: log a warning, skip the INSERT",
        caption="database/db_writer.py — is_duplicate()"
    )


def section_database(doc: Document) -> None:
    doc.h1("ডেটাবেস স্কিমা", "বিভাগ ৪")

    doc.para("তিনটি টেবিল + একটি status টেবিল। সবকিছু `database/schema.sql`-এ আছে, "
             "একবার চালালেই তৈরি হয়ে যায়।")

    doc.h2("lis_raw_messages — কাঁচা মেসেজ", "৪.১")
    doc.code(
        "CREATE TABLE lis_raw_messages (\n"
        "  message_id    NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,\n"
        "  analyzer_name VARCHAR2(100) NOT NULL,\n"
        "  protocol      VARCHAR2(10),          -- ASTM / HL7\n"
        "  peer          VARCHAR2(100),         -- 192.168.1.50:51244\n"
        "  raw_message   CLOB,\n"
        "  received_at   TIMESTAMP DEFAULT SYSTIMESTAMP,\n"
        "  status        VARCHAR2(20),          -- RECEIVED/PARSED/DUPLICATE/ERROR\n"
        "  note          VARCHAR2(500)\n"
        ");"
    )
    doc.para("এই টেবিল কখনো খালি করবেন না — parser-এ বাগ ঠিক করার পর পুরনো মেসেজ এখান থেকেই আবার প্রসেস হয়।", size=9.4, color=MUTED)

    doc.h2("lab_results — রিপোর্টের মূল টেবিল", "৪.২")
    doc.code(
        "CREATE TABLE lab_results (\n"
        "  result_id       NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,\n"
        "  sample_id       VARCHAR2(50)  NOT NULL,\n"
        "  patient_id      VARCHAR2(50),\n"
        "  patient_name    VARCHAR2(150),\n"
        "  test_code       VARCHAR2(30)  NOT NULL,   -- WBC, HGB, GLU ...\n"
        "  result_value    NUMBER,                   -- numeric হিসাবের জন্য\n"
        "  result_text     VARCHAR2(100),            -- '<150', 'Positive' ইত্যাদি\n"
        "  result_unit     VARCHAR2(30),\n"
        "  ref_range       VARCHAR2(50),\n"
        "  abnormal_flag   VARCHAR2(5),              -- N/H/L/HH/LL/C\n"
        "  is_abnormal     CHAR(1),                  -- APEX-এ ফিল্টার সহজ করতে\n"
        "  is_critical     CHAR(1),\n"
        "  analyzer_name   VARCHAR2(100) NOT NULL,\n"
        "  result_datetime TIMESTAMP,\n"
        "  received_at     TIMESTAMP DEFAULT SYSTIMESTAMP,\n"
        "  CONSTRAINT uq_result UNIQUE (sample_id, test_code, analyzer_name, result_value)\n"
        ");"
    )

    doc.h2("APEX-এর জন্য ভিউ", "৪.৩")
    rows = [
        ["ভিউ", "কী দেয়", "APEX-এ কোথায়"],
        ["v_sample_results", "প্রতি স্যাম্পলের সারসংক্ষেপ + abnormal সংখ্যা", "Result Entry পেজের রিপোর্ট"],
        ["v_critical_results", "শুধু critical রেজাল্ট, নতুন আগে", "ড্যাশবোর্ড, auto-refresh"],
        ["v_analyzer_throughput", "ঘণ্টাভিত্তিক মেশিনভেদে রেজাল্ট সংখ্যা", "Monitoring পেজ"],
    ]
    doc.table(rows[0], rows[1:], widths=[135, 240, 125], size=8.4)


def section_apex(doc: Document) -> None:
    doc.h1("Oracle APEX রিপোর্ট", "বিভাগ ৫")

    doc.para("Middleware ডেটা ঢোকানোর পর APEX-এ মাত্র তিনটি জিনিস বানালেই পুরো সিস্টেম ব্যবহারযোগ্য:")
    doc.numbered([
        "**Interactive Report** — source হিসেবে `SELECT * FROM v_sample_results` (বা lab_results)। "
        "samp_id-এ লিংক দিয়ে ডিটেইল পেজে যাবে।",
        "**Form পেজ** — রোগী/স্যাম্পল রেজিস্ট্রেশন, বিক্রয় বা বারকোড স্ক্যান। এখানে যা সেভ হবে সেই "
        "sample_id-ই মেশিন থেকে ফিরে আসবে।",
        "**Classic Report + Alert** — `v_critical_results` উপর ভিত্তি করে; রিফ্রেশ ৩০ সেকেন্ড, "
        "নতুন critical এলে লাল ব্যাজ।",
    ])

    doc.code(
        "-- one patient: every result, sorted by test code\n"
        "SELECT test_code, result_value, result_unit, ref_range, abnormal_flag\n"
        "  FROM lab_results\n"
        " WHERE sample_id = :P10_SAMPLE_ID\n"
        " ORDER BY test_code;\n\n"
        "-- is data still arriving? (quick health check per analyzer)\n"
        "SELECT analyzer_name, MAX(received_at) AS last_seen,\n"
        "       COUNT(*) AS results_today\n"
        "  FROM lab_results\n"
        " WHERE received_at >= TRUNC(SYSDATE)\n"
        " GROUP BY analyzer_name;"
    )

    doc.callout("ছোট টিপস", "APEX-এ রিপোর্ট পেজে **Pagination → 50** আর "
                "**Column → result_value → Number format** দিয়ে দিলে ল্যাবের লোকজন সহজে পড়তে পারবে। "
                "abnormal_flag-এ Highlight রঙ দিলে H/L চোখে পড়ে।", kind="info")


    doc.h2("সব নিয়ন্ত্রণ APEX থেকে — ল্যাব কখনো সার্ভারে ঢুকবে না", "৫.৬")

    doc.para("উপরের রিপোর্টগুলো শুধু **দেখার** অংশ। কিন্তু আসল প্রশ্নটা হলো: অ্যানালাইজার যোগ করা, "
             "পোর্ট বদলানো, একটা মেশিন সাময়িক বন্ধ করা — এসব **কে** করবে? প্রচলিত উত্তর: সার্ভারে গিয়ে "
             "`config.yaml` এডিট, তারপর সার্ভিস রিস্টার্ট। এতে তিনটি সমস্যা: (ক) ল্যাবের লোক সার্ভারে "
             "ঢুকতে চায় না, ঢোকা উচিতও নয়; (খ) কে কখন কী বদলেছে তার হিসাব থাকে না; (গ) একটা অক্ষর "
             "ভুল হলে পুরো সার্ভিস বন্ধ, অথচ অ্যানালাইজার তখনো নমুনা পাঠাচ্ছে।")

    doc.para("তাই এই ডিজাইনে **Oracle টেবিলই একমাত্র সত্য**। APEX পেজ শুধু ওই টেবিলের ফর্ম; Python "
             "সার্ভিস প্রতি ৫ সেকেন্ডে টেবিল পড়ে বদলটা নিজে থেকে প্রয়োগ করে, আর সফল/ব্যর্থতা টেবিলেই "
             "ফিরিয়ে লেখে।")

    doc.code(
        "APEX form (lab staff)\n"
        "      |  INSERT / UPDATE\n"
        "      v\n"
        "  lis_config / lis_commands          <-- the only interface\n"
        "      |  poll every 5 s\n"
        "      v\n"
        "  service.py  ->  apply diff  ->  restart ONLY the changed listener\n"
        "      |\n"
        "      +-->  lis_analyzer_status (ONLINE, last message)\n"
        "      +-->  lis_config.last_applied_at / last_error  --> green tick in APEX"
    )

    doc.h2("একবারের ইনস্টল", "৫.৬.১")
    doc.numbered([
        "`sqlplus lis_user/pass@//host:1521/ORCL @database/schema.sql` — রেজাল্ট টেবিল ও ভিউ।",
        "`sqlplus lis_user/pass@//host:1521/ORCL @database/apex_install.sql` — কন্ট্রোল টেবিল "
        "(`lis_config`, `lis_test_code_map`, `lis_commands`, `lis_analyzer_status`), APEX-এর ভিউ "
        "(`v_lis_analyzers`, `v_lis_settings`, `v_lis_health`, `v_lis_command_log`) এবং তিনটি "
        "ডেমো অ্যানালাইজার + পাঁচটি সেটিং সিড।",
        "APEX-এ পেজ বানান (`docs/APEX_CONTROL.md` §২-এ প্রতিটি ফিল্ড, LOV আর নিয়ম লেখা আছে)।",
        "সার্ভারে সার্ভিস ইনস্টল করুন — `ops/` ফোল্ডারের স্ক্রিপ্ট, নিচে দেখুন।",
    ])

    doc.h2("APEX পেজ ম্যাপ", "৫.৬.২")
    doc.table(["#", "পেজ", "Region source", "কাজ"],
              [["১", "অ্যানালাইজার", "v_lis_analyzers", "তালিকা: অনলাইন/অফলাইন, শেষ মেসেজ"],
               ["২", "অ্যানালাইজার ফর্ম", "lis_config", "পোর্ট, প্রোটোকল, সিরিয়াল সেটিং"],
               ["৩", "সেটিংস", "v_lis_settings", "ACK mode, log level, QC prefix"],
               ["৪", "টেস্ট কোড ম্যাপ", "lis_test_code_map", "মেশিনের কোড → রিপোর্ট কোড"],
               ["৫", "কন্ট্রোল বাটন", "lis_commands", "Reload / Start / Stop / Restart / Selftest"],
               ["৬", "হেলথ ড্যাশবোর্ড", "v_lis_health", "কত অনলাইন, আজ কত রেজাল্ট, কত ক্রিটিক্যাল"]],
              widths=[26, 105, 118, 226], size=8.4)

    doc.h2("ফর্মে যে নিয়মগুলো বসাবেন", "৫.৬.৩")
    doc.table(["ফিল্ড", "নিয়ম", "কেন"],
              [["analyzer_name", "Required, Unique, এডিটে Read Only", "নাম বদলালে হিস্টোরি ভেঙে যায়"],
               ["protocol", "LOV: ASTM / HL7 / AUTO", "ভুল প্রোটোকল = কোনো রেজাল্ট না"],
               ["connection_type", "LOV: TCP / SERIAL", "সিরিয়ালে পোর্ট নম্বর লাগে না"],
               ["port", "1024–65535, TCP হলে Required", "ভুল পোর্টে অ্যানালাইজার connect করতে পারবে না"],
               ["serial_port", "SERIAL হলে Required (COM3)", "নাহলে পোর্ট খোলা যায় না"],
               ["enabled", "Switch Y/N", "বন্ধ = শুধু লিসেনার উঠে যায়, ডেটা থাকে"],
               ["status", "ডিফল্ট DRAFT, শেষে ACTIVE", "অর্ধেক লেখা পোর্ট কখনো লাইভ হবে না"]],
              widths=[92, 175, 208], size=8.4)

    doc.callout("DRAFT কেন এত জরুরি",
                "নতুন অ্যানালাইজার যোগ করার সময় সার্ভিস শুধু `status = 'ACTIVE'` রো পড়ে। তাই আগে "
                "DRAFT-এ সব লিখে ফেলুন, মেশিনের Host সেটিং মিলিয়ে নিন, তারপর ACTIVE করুন — তখনই "
                "পোর্ট খুলবে। এডিটের সময়ও একই নিয়ম: DRAFT → বদলান → ACTIVE।",
                kind="warn")

    doc.h2("বাটনগুলো (lis_commands)", "৫.৬.৪")
    doc.table(["বাটন", "কাজ", "কখন"],
              [["RELOAD", "সব রো নতুন করে পড়ে, শুধু বদলানোটা রিস্টার্ট", "কিছু apply না হলে"],
               ["START / STOP", "একটা অ্যানালাইজার চালু / বন্ধ", "সার্ভিসিং বা ভুল করে বন্ধ"],
               ["RESTART", "বন্ধ করে চালু", "পোর্ট আটকে গেলে"],
               ["SELFTEST", "ভেতরের নমুনা দিয়ে টেস্ট, ডেটাবেসে লেখে না", "নতুন মেশিন যোগ করার পর"],
               ["PING", "সার্ভিস জীবিত কি না", "সন্দেহ হলে"]],
              widths=[78, 205, 192], size=8.4)

    doc.para("`SELFTEST`-এর ফলাফল ওই কমান্ড রো-এর `result_message`-এ ফিরে আসে, যেমন — "
             "`astm_cbc_normal: HALF_ACCEPTED, 4 stored, ack=ASTM (E1381 checksum ok)`। "
             "ফলে আসল মেশিনে হাত দেওয়ার আগেই বোঝা যায় পার্সিং আর স্টোরেজ ঠিক আছে।", size=9)

    doc.h2("সার্ভিস ইনস্টল (একবার)", "৫.৬.৫")
    doc.code(
        "# Windows (Administrator PowerShell) — NSSM, auto-restart, password in machine env\n"
        ".\\ops\\install_service_windows.ps1 -ProjectDir \"C:\\lis\\lis-middleware\"\n"
        "nssm status LISMiddleware\n\n"
        "# Linux — systemd unit already in ops/\n"
        "sudo cp ops/lis-middleware.service /etc/systemd/system/\n"
        "sudo install -m 600 ops/lis-middleware.env.example /etc/lis-middleware.env   # DB password\n"
        "sudo systemctl enable --now lis-middleware\n"
        "journalctl -u lis-middleware -f"
    )

    doc.para("`config/config.yaml`-এ এখন শুধু ডেটাবেসের ঠিকানা (host / service_name / username), "
             "লগ-লেভেল আর ফলব্যাক তালিকা থাকে; পাসওয়ার্ড থাকে সার্ভিসের environment-এ। অ্যানালাইজার, "
             "পোর্ট, প্রোটোকল, টেস্ট কোড ম্যাপ — সব ডেটাবেসে।", size=9)

    doc.h2("ভুল হলে কী হয়", "৫.৬.৬")
    doc.table(["পরিস্থিতি", "সার্ভিসের প্রতিক্রিয়া", "APEX-এ যা দেখবেন"],
              [["পোর্ট ৮০ লিখেছেন", "প্রয়োগ করে না, আগের কনফিগ চালু থাকে", "last_error = port outside 1024-65535"],
               ["দুই মেশিন একই পোর্টে", "প্রয়োগ করে না", "last_error = port 6661 used by both A and B"],
               ["পোর্ট অন্য প্রোগ্রাম ধরে আছে", "শুধু ওই লিসেনার উঠবে না", "state = STOPPED + cannot open port"],
               ["সব ঠিক", "শুধু বদলানো লিসেনার রিস্টার্ট", "সবুজ applied tick, state = ONLINE"],
               ["সার্ভিস বন্ধ", "—", "heartbeat পুরোনো, preflight ⚠️ দেখাবে"]],
              widths=[120, 175, 180], size=8.4)

    doc.para("নিয়মটা সহজ: **ভুল কনফিগ কখনো চলতি কনফিগকে ভাঙে না।** মেসেজ আসতে থাকে, রেজাল্ট জমতে "
             "থাকে, শুধু লাল লেখাটা অপারেটরকে জানায় কী ঠিক করতে হবে।", size=9)

def section_setup(doc: Document) -> None:
    doc.h1("সেটআপ — ধাপে ধাপে", "বিভাগ ৬")

    doc.h2("ধাপ ১ — Oracle স্কিমা তৈরি", "৬.১")
    doc.code(
        "# Oracle schema (use your own service name)\n"
        "sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/schema.sql\n\n"
        "# or run schema.sql from SQL Developer / SQLcl"
    )

    doc.h2("ধাপ ২ — config.yaml পূরণ", "৬.২")
    doc.code(
        "database:\n"
        "  host: \"192.168.1.100\"      # DB server IP\n"
        "  port: 1521\n"
        "  service_name: \"ORCL\"       # XEPDB1 / FREEPDB1 / ORCL\n"
        "  username: \"lis_user\"\n"
        "  password: \"CHANGE_ME\"\n"
        "analyzers:\n"
        "  - name: \"Sysmex_XN_1000\"\n"
        "    protocol: \"ASTM\"\n"
        "    connection_type: \"TCP\"\n"
        "    port: 6661\n"
        "    enabled: true",
        caption="config/config.yaml — সংযোগ ও মেশিন"
    )

    doc.h2("ধাপ ৩ — ইনস্টল ও প্রথম রান", "৬.৩")
    doc.code(
        "cd lis-middleware\n"
        "python -m venv .venv\n"
        "source .venv/bin/activate          # Windows: .venv\\Scripts\\activate\n"
        "pip install -r requirements.txt\n\n"
        "# test the Oracle connection only\n"
        "python -m database.db_connection\n\n"
        "# run without analyzers (results kept in memory)\n"
        "python main.py --dry-run\n\n"
        "# the real thing\n"
        "python main.py"
    )

    doc.h2("ধাপ ৩.৫ — preflight (ল্যাবে যাওয়ার আগে বাধ্যতামূলক)", "৬.৩.৫")
    doc.para("এক কমান্ডে সব যাচাই: Python ও প্যাকেজ, config-এর ভুল (ডুপ্লিকেট পোর্ট, ভুল protocol), "
             "পোর্ট খোলা যায় কি না, Oracle-এ পৌঁছায় কি না, টেবিল/ভিউ আছে কি না, লগ ফোল্ডার লেখা যায় কি না।")
    doc.code(
        "python tools/preflight.py            # পুরো চেক (Oracle সহ)\n"
        "python tools/preflight.py --offline  # DB ছাড়া\n\n"
        "# ❌ থাকলে ল্যাবে যাবেন না — প্রতিটি ❌-এর নিচে সমাধান লেখা থাকে\n"
        "# সব ✅ হলে: python main.py  → device_sim দিয়ে নকল মেশিন → APEX-এ রো দেখা"
    )

    doc.h2("ধাপ ৪ — মেশিনে সেটিং", "৬.৪")
    doc.para("analyzer-এর **Host Communication / LIS Connection** পেজে গিয়ে বসান:")
    rows = [
        ["ফিল্ড", "মান", "ভুল হলে"],
        ["Host / LIS IP", "middleware চলা পিসির স্থির IP", "মেশিন কিছুই পাঠাবে না"],
        ["Host Port", "6661 (ASTM) / 6662 (HL7) — config-এর সাথে হুবহু", "connection refused"],
        ["Protocol", "ASTM (Sysmex, Roche) / HL7 (Mindray)", "no results parsed"],
        ["Transmission", "ASTM হলে E1381 (কিছু মেশিনে Basic/RAW)", "হ্যান্ডশেক মিলবে না"],
        ["Checksum", "চালু (আমরা যাচাই করি)", "প্রতি ব্লকে NAK → রিট্রাই"],
        ["Baud/Data/Parity/Stop", "9600 · 7 · EVEN · 1 (RS-232)", "আজেবাজে অক্ষর"],
        ["Sample ID", "APEX-এ স্ক্যান করা বারকোডই", "ভুল স্যাম্পলে রেজাল্ট"],
        ["Auto-send results", "চালু", "অপারেটরকে Send চাপতে হবে"],
        ["Host query", "প্রথম দিনে বন্ধ", "ওয়ার্কলিস্ট লাগবে"],
    ]
    doc.table(rows[0], rows[1:], widths=[150, 300], size=8.4)

    doc.callout("Firewall", "Windows-এ পোর্ট খুলতে ভুলবেন না:\n"
                "`New-NetFirewallRule -DisplayName \"LIS 6661\" -Direction Inbound -LocalPort 6661 -Protocol TCP -Action Allow`",
                kind="warn")


def section_labday(doc: Document) -> None:
    doc.h1("ল্যাব ডে — ক্রম মেনে", "বিভাগ ৬ক")
    doc.para("ঘরে সব প্রস্তুত হওয়ার পর ল্যাবে মাত্র এই ধাপগুলো:")
    doc.numbered([
        "**পিং ও পোর্ট:** `ping <মেশিনের IP>` এবং `netstat -ano | findstr 6661` — LISTENING আছে কি?",
        "**কন্ট্রোল স্যাম্পল:** মেশিন থেকে QC বা ফাঁকা স্যাম্পল পাঠান; লগে `parsed N result(s)` খুঁজুন।",
        "**APEX:** `v_sample_results`-এ স্যাম্পল আইডি ও ভ্যালু মিলছে কি?",
        "**একটা আসল স্যাম্পল**, তারপর ল্যাবের নিজের রিপোর্টের সাথে মিলিয়ে দেখা।",
        "**ক্যাপচার:** `sniff_com` দিয়ে সেশন রেকর্ড করে রাখুন — ভবিষ্যতের টেস্টের ভিত্তি।",
    ])
    doc.callout("আটকে গেলে রোলব্যাক", "মেশিনের Host communication **Off** করে দিন — মেশিন আবার নিজের "
                "প্রিন্টারে রিপোর্ট দেবে, ল্যাব আটকে থাকবে না। লগ + `lis_raw_messages` নিয়ে ঘরে ফিরে "
                "`device_sim`/`replay_capture` দিয়ে সমস্যা রিপ্রোডিউস করে ঠিক করুন।", kind="warn")
    doc.para("বিস্তারিত ধাপ, মেশিনের সেটিং টেবিল আর সমস্যার তালিকা: `docs/LAB_DAY.md`।",
             size=9, color=MUTED)


def section_test(doc: Document) -> None:
    doc.h1("টেস্ট ও মক অ্যানালাইজার", "বিভাগ ৭")

    doc.para("আসল মেশিন ছাড়া টেস্ট করা যায় দুইভাবে — দুটোই প্রজেক্টে আছে।")

    doc.h2("ইউনিট টেস্ট — Oracle ছাড়াই", "৭.১")
    doc.code("python -m unittest discover -s tests -v\n"
             "# 50 tests — ASTM, HL7, E1381 framing, validator, dedupe, ACK, hot reload")
    doc.para("টেস্টগুলো আসল ফরম্যাটের নমুনা দিয়ে চলে (samples/messages.yaml), তাই parser "
             "বদলালে সাথে সাথেই বোঝা যায় কিছু ভেঙেছে কি না।")

    doc.h2("মক অ্যানালাইজার — মেশিনের অভিনয়", "৭.২")
    doc.code(
        "# list scenarios\n"
        "python tools/mock_analyzer.py --list\n\n"
        "# pretend to be a Sysmex: one CBC message\n"
        "python tools/mock_analyzer.py -s astm_cbc_normal -p 6661\n\n"
        "# pretend to be a Mindray: HL7, 5x every 10s (load test)\n"
        "python tools/mock_analyzer.py -s hl7_biochem -p 6662 --repeat 5 --interval 10\n\n"
        "# serial port (use a com0com virtual pair to test)\n"
        "python tools/mock_analyzer.py -s astm_cbc_normal --serial COM4"
    )

    doc.h2("ব্রাউজার ডেমো", "৭.৩")
    doc.code("python -m demo.app        # http://localhost:8080")
    doc.para("একই pipeline HTTP দিয়ে চালায় — মেসেজ পাঠানো, ACK দেখা, raw মেসেজ, parsed রেজাল্ট, "
             "ডুপ্লিকেট টেস্ট — সব ব্রাউজারে। মেসেজ এডিট করে ইচ্ছাকৃত ভাঙলে error path-ও দেখা যায়।")

    doc.h2("আসল মেশিনের অনুভূতি — E1381 ওয়্যার লেভেল", "৭.৪")
    doc.para("ব্রাউজার ডেমো একটা HTTP ফর্ম — আসল অ্যানালাইজার তা নয়। আসল মেশিন "
             "ASTM E1381 হ্যান্ডশেক করে: **ENQ → ACK → STX⟨নম্বর⟩ডেটা⟨ETX⟩⟨চেকসাম⟩ → ACK … → EOT**। "
             "চেকসাম ভুল হলে মেশিন NAK পায় এবং একই ব্লক আবার পাঠায়। এই লেয়ারটা "
             "`core/e1381.py`-তে আছে এবং ৮টি টেস্টে যাচাই করা।")
    doc.code(
        "analyzer                        middleware\n"
        "  ENQ  (0x05)  -------------->  \"আমার পাঠানোর আছে\"\n"
        "               <--------------  ACK  (0x06)\n"
        "  STX 1 <records> ETX <cs> -->  ফ্রেম নম্বর + ডেটা + চেকসাম\n"
        "               <--------------  ACK / NAK  (NAK হলে মেশিন রিপিট করে)\n"
        "  EOT  (0x04)  -------------->  শেষ; এখন পুরো মেসেজ প্রসেস হয়",
        caption="একই কথোপকথন যেটা কেবলে ঘটে — ডেমো ফর্মে দেখা যায় না"
    )
    doc.code(
        "python main.py --dry-run                                     # টার্মিনাল ১\n"
        "python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace  # টার্মিনাল ২\n\n"
        "# কেবল-এর সমস্যা বানিয়ে দেখা\n"
        "python tools/device_sim.py -s astm_cbc_normal --fault checksum --fault-rate 0.4\n"
        "python tools/device_sim.py -s astm_cbc_normal --fault noise    --fault-rate 0.4\n"
        "python tools/device_sim.py -s astm_cbc_normal --fault drop     --fault-rate 0.25\n"
        "python tools/device_sim.py -s hl7_critical   -p 6662 --trace",
        caption="tools/device_sim.py — ওয়্যার-লেভেল সিমুলেটর"
    )

    doc.h2("Mirth ছাড়া অন্য সিমুলেটর", "৭.৬")
    doc.para("আসল মেশিনের মতো টেস্ট করার জন্য আরও অপশন আছে — কমার্শিয়াল, ওপেন সোর্স আর ভার্চুয়াল পোর্ট:")
    rows = [
        ["টুল", "কী ভালো", "সীমা"],
        ["tools/device_sim.py (এই প্রজেক্ট)", "E1381 হ্যান্ডশেক, চেকসাম, NAK-রিট্রাই, নয়েজ, MLLP, ফল্ট ইনজেকশন", "সিনারিও নিজে বানাতে হবে"],
        ["Lab Analyzer Simulator (ওপেন সোর্স)", "ব্রাউজার প্যানেল, HL7+ASTM, Docker, ফিক্সচার", "থার্ড-পার্টি, নমুনা ফরম্যাট"],
        ["Sand Lake Simulator", "লো-লেভেল ASTM/HL7 ডিবাগিং, ট্রান্সমিশন ফাইল", "কমার্শিয়াল লাইসেন্স"],
        ["HEDONICS ASTM Simulator", "ইনস্ট্রুমেন্ট ↔ LIS দুই দিকেই", "কমার্শিয়াল"],
        ["HL7 Inspector / 7Edit", "HL7 মেসেজ দেখা/এডিট", "ASTM বোঝে না"],
        ["com0com / HHD Virtual Serial Ports", "ভার্চুয়াল COM পেয়ার (COM3↔COM4)", "ড্রাইভার ইনস্টল"],
    ]
    doc.table(rows[0], rows[1:], widths=[150, 210, 110], size=8.0)
    doc.para("কমার্শিয়াল টুলের দাম/লাইসেন্স বদলাতে পারে — কেনার আগে যাচাই করুন। নিজের কাজের "
             "৯০% `device_sim` দিয়েই হবে।", size=9, color=MUTED)

    doc.h2("আসল মেশিনের ডেটা ধরা ও রিপ্লে", "৭.৫")
    doc.para("সিমুলেটর যতই ভালো হোক, আপনার Sysmex-এর ফিল্ড ম্যাপিং সে জানে না। একবার আসল "
             "ক্যাপচার নিলে সেটাই চিরকালের রেফারেন্স:")
    doc.code(
        "# সিরিয়াল ট্যাপ (RS-232): মেশিন → sniff_com → middleware\n"
        "python tools/sniff_com.py --port COM4 --baud 9600 --parity EVEN \\\n"
        "    --tap 127.0.0.1:6661 --out logs/capture.jsonl\n\n"
        "# TCP ট্যাপ: মেশিনের Host সেটিং-এ পোর্ট 6663 দিন\n"
        "python tools/sniff_com.py --listen 0.0.0.0:6663 --tap 127.0.0.1:6661\n\n"
        "# পরে যতবার চান রিপ্লে করুন (parser বদলালে এটাই রিগ্রেশন টেস্ট)\n"
        "python tools/replay_capture.py logs/capture.jsonl --list\n"
        "python tools/replay_capture.py logs/capture.jsonl --port 6661 --timing"
    )

    doc.callout("সৎ কথা", "ডেমো ডেটা দিয়ে শুধু পাইপলাইন প্রমাণ হয়, আপনার মেশিনের ফরম্যাট নয়। "
                "তাই আসল মেশিনে যাওয়ার আগে অন্তত একবার `sniff_com` দিয়ে ক্যাপচার নিন এবং সেই সেশন "
                "`replay_capture` দিয়ে চালান। বিস্তারিত: `docs/REAL_DEVICE.md`।", kind="warn")

    rows = [
        ["সিনারিও", "কী পরীক্ষা হয়"],
        ["astm_cbc_normal", "সাধারণ ৪-টেস্ট CBC, patient/sample লিংক"],
        ["astm_cbc_abnormal", "H / L / LL flag, critical শনাক্তকরণ"],
        ["hl7_biochem", "HL7 ORU^R01, OBX পার্সিং"],
        ["hl7_critical", "HH flag → is_critical = Y"],
        ["astm_merged_unit", "ভ্যালু ও ইউনিট একই ফিল্ডে ('5,8 mmol/L')"],
        ["broken_message", "ভাঙা মেসেজ — NAK, কিন্তু raw সংরক্ষিত"],
    ]
    doc.table(rows[0], rows[1:], widths=[150, 300], size=8.4)


def section_deploy(doc: Document) -> None:
    doc.h1("ডিপ্লয়মেন্ট", "বিভাগ ৮")

    doc.callout("পাসওয়ার্ড ও সিকিউরিটি", "`config.yaml`-এ প্লেইন পাসওয়ার্ড রাখবেন না — প্রোডাকশনে "
                "`LIS_DB_PASSWORD` এনভায়রনমেন্ট ভ্যারিয়েবল ব্যবহার করুন (db_connection.py এটি আগে দেখে)। "
                "ফাইল পারমিশন সীমিত রাখুন, এবং ডেটাবেস ইউজারকে শুধু প্রয়োজনীয় টেবিলের ইনসার্ট/সিলেক্ট দিন।",
                kind="danger")

    doc.para("ল্যাবে সাধারণত একটা ছোট পিসি বা সার্ভার ২৪ ঘণ্টা চলে। তাই middleware-কে "
             "**সার্ভিস** হিসেবে বসাতে হবে — কেউ লগআউট করলেও চলবে।")

    doc.h2("Windows — সার্ভিস হিসেবে", "৮.১")
    doc.code(
        "# with NSSM (easiest)\n"
        "nssm install LISMiddleware \"C:\\Python311\\python.exe\" \"C:\\lis\\lis-middleware\\main.py\"\n"
        "nssm set LISMiddleware AppDirectory \"C:\\lis\\lis-middleware\"\n"
        "nssm set LISMiddleware AppStdout \"C:\\lis\\logs\\stdout.log\"\n"
        "nssm start LISMiddleware\n\n"
        "# or with Task Scheduler:\n"
        "#   Trigger: At startup\n"
        "#   Action:  python.exe main.py  (Start in: C:\\lis\\lis-middleware)"
    )

    doc.h2("Linux — systemd", "৮.২")
    doc.code(
        "# /etc/systemd/system/lis-middleware.service\n"
        "[Unit]\n"
        "Description=LIS Middleware (ASTM/HL7 to Oracle)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "User=lis\n"
        "WorkingDirectory=/opt/lis-middleware\n"
        "ExecStart=/opt/lis-middleware/.venv/bin/python main.py\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target"
    )
    doc.code("sudo systemctl daemon-reload\n"
             "sudo systemctl enable --now lis-middleware\n"
             "journalctl -u lis-middleware -f")

    doc.callout("প্রোডাকশনের আগে", "১) `--dry-run` দিয়ে অন্তত একদিন চালান।  ২) লগ ফাইল ঘুরছে কি না দেখুন "
                "(RotatingFileHandler, ৫ MB × ৫)।  ৩) ডেটাবেস ব্যাকআপ রাখুন।  ৪) "
                "`tools/replay.py --status ERROR` দিয়ে ব্যর্থ মেসেজ দেখুন।", kind="warn")


def section_troubleshoot(doc: Document) -> None:
    doc.h1("সমস্যা ও সমাধান", "বিভাগ ৯")

    rows = [
        ["লক্ষণ", "কারণ", "সমাধান"],
        ["connection refused", "middleware চলছে না বা পোর্ট আলাদা", "লগে 'listening on' লাইন দেখুন, config মেলান"],
        ["ORA-12541 (no listener)", "host/port/service_name ভুল", "python -m database.db_connection দিয়ে টেস্ট"],
        ["ORA-01017 (invalid login)", "ইউজারনেম/পাসওয়ার্ড ভুল", "config.yaml ঠিক করুন"],
        ["no results parsed", "protocol ভুল লেবেল", "protocol: AUTO দিন, লগে detected_protocol দেখুন"],
        ["সিরিয়ালে আজেবাজে অক্ষর", "baud/parity মেলেনি", "9600 7E1, তারপর 9600 8N1 চেষ্টা করুন"],
        ["মেশিন কিছু পাঠাচ্ছেই না", "ACK-এর অপেক্ষায় আছে", "HL7 হলে app.ack_mode: MLLP দিন"],
        ["একই রেজাল্ট দুইবার", "মেশিন পুনরায় পাঠাচ্ছে (স্বাভাবিক)", "ডুপ্লিকেট আটকায়; lis_raw_messages-এ DUPLICATE"],
        ["QC নমুনা রিপোর্টে ঢুকছে", "quality-control স্যাম্পল আসল রোগীর মতোই আসে", "validation.ignore_sample_prefixes-এ QC/CTRL/CAL দিন → status IGNORED"],
        ["পোর্ট ইতিমধ্যে ব্যবহৃত", "অন্য প্রোগ্রাম", "netstat -ano | findstr 6661 → PID বন্ধ করুন"],
        ["লগে checksum mismatch", "কেবলের নয়েজ বা parity ভুল", "লিসেনার NAK দেয়, মেশিন আবার পাঠায় — 7E1 মিলিয়ে নিন"],
        ["মেশিন মাঝপথে থেমে যায়", "ব্লকের ACK-এর অপেক্ষায়", "লগে bad block → NAK দেখুন; এটাই রিট্রাই পথ"],
    ]
    doc.table(rows[0], rows[1:], widths=[130, 150, 190], size=8.2)

    doc.h2("লগ পড়ার নিয়ম", "৯.১")
    doc.code(
        "2026-11-04 10:30:01 | INFO  | LIS.DB   | Oracle DB connected: 192.168.1.100:1521/ORCL\n"
        "2026-11-04 10:30:01 | INFO  | LIS.TCP  | Sysmex_XN_1000 | TCP | listening on 0.0.0.0:6661\n"
        "2026-11-04 10:30:12 | INFO  | LIS.TCP  | Sysmex_XN_1000: received 412 bytes from 192.168.1.50\n"
        "2026-11-04 10:30:12 | INFO  | LIS.ASTM | ASTM: parsed 4 result(s) from Sysmex_XN_1000\n"
        "2026-11-04 10:30:12 | INFO  | LIS.DBW  | inserted SAMPLE-0001 / WBC = 7.5 10*3/uL\n"
        "2026-11-04 10:30:12 | WARN | LIS.DBW  | duplicate skipped: SAMPLE-0001 / RBC",
        caption="সুস্থ সিস্টেমের লগ — প্রথম লাইন থেকেই বোঝা যায় কোথায় আটকেছে"
    )


def section_checklist(doc: Document) -> None:
    doc.h1("শেষ চেকলিস্ট", "বিভাগ ১০")

    doc.h2("ডেটাবেস", "১০.১")
    doc.bullets([
        "schema.sql চালানো হয়েছে, TEAM লিস্টে `lis_user` আছে",
        "`python -m database.db_connection` → OK",
        "ইনডেক্স আছে (sample_id, received_at, is_critical)",
        "ব্যাকআপ শিডিউল করা আছে",
    ])
    doc.h2("Middleware", "১০.২")
    doc.bullets([
        "`python -m unittest discover -s tests` → সব পাস",
        "`--dry-run` দিয়ে একদিন চলেছে",
        "সার্ভিস হিসেবে ইনস্টল করা (Windows NSSM / Linux systemd)",
        "লগ রোটেট হচ্ছে, ডিস্ক ভরছে না",
        "সার্ভার রিস্টার্ট হলে নিজে থেকে চালু হয়",
    ])
    doc.h2("মেশিন", "১০.৩")
    doc.bullets([
        "প্রতিটি analyzer-এর IP, পোর্ট, প্রোটোকল কাগজে লিখে রাখা",
        "ACK mode (ASTM / MLLP) মিলিয়ে দেখা",
        "একটা আসল স্যাম্পল দিয়ে end-to-end টেস্ট",
        "বারকোড স্ক্যান → মেশিন → APEX — পুরো পথে ভ্যালু মিলেছে",
        "আসল মেশিনের এক ক্যাপচার নেওয়া হয়েছে এবং সেটি রিপ্লে করে দেখা গেছে",
        "`python tools/preflight.py` → কোনো ❌ নেই (ল্যাবে যাওয়ার আগের শেষ গেট)",
        "সার্ভারে স্থির IP + ফায়ারওয়ালে পোর্ট খোলা",
        "মেশিনের Host সেটিং-এর নাম আর মান খাতায় লেখা (ম্যানুয়ালের Host Interface অধ্যায়)",
    ])
    doc.h2("APEX", "১০.৪")
    doc.bullets([
        "Interactive Report + Form পেজ তৈরি",
        "critical রেজাল্টের জন্য আলাদা রিপোর্ট/অ্যালার্ট",
        "অ্যাবনর্মাল ফ্ল্যাগে রঙ হাইলাইট",
        "ইউজার পারমিশন (ল্যাব টেকনিশিয়ান vs ডক্টর)",
    ])

    doc.space(10)
    doc.callout("শেষ কথা", "মিডলওয়্যার শুধু অনুবাদক — মেশিন যা মাপে, তাই ডেটাবেসে পৌঁছায়, কিছু বাড়ে না কমে না। "
                "তাই প্রতিটি নতুন মেশিন যোগ করার সময় একটা ম্যানুয়াল রেজাল্টের সাথে মিলিয়ে নিন।",
                kind="ok")

    doc.space(14)
    doc.hline(doc.left_margin, doc.left_margin + doc.content_width, doc.y, LINE, 0.6)
    doc.space(8)
    doc.para("LIS Middleware · Python 3.10+ · Oracle 12c+ · ASTM E1394 · HL7 v2.5.1", size=8.5, color=MUTED, align="center")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.gettempdir()) / "lis_fonts"
    fonts = build_fonts(workdir)

    doc = Document(fonts,
                   header=f"{TITLE} — {SUBTITLE}",
                   footer="LIS Middleware · Project Guide · পৃষ্ঠা {n} / {total}")
    cover(doc)
    section_why(doc)
    section_architecture(doc)
    section_code(doc)
    section_database(doc)
    section_apex(doc)
    section_setup(doc)
    section_labday(doc)
    section_test(doc)
    section_deploy(doc)
    section_troubleshoot(doc)
    section_checklist(doc)

    data = doc.build()
    OUT.write_bytes(data)
    print(f"wrote {OUT}  ({len(data)/1024:.1f} KB, {len(doc.pages)} pages)")


if __name__ == "__main__":
    main()
