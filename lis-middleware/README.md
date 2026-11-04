# LIS Middleware — Analyzer → Oracle → APEX

📄 বাংলা গাইড: [`../docs/PROJECT_GUIDE_BN.pdf`](../docs/PROJECT_GUIDE_BN.pdf) · code walkthrough: [`../docs/CODE_WALKTHROUGH.pdf`](../docs/CODE_WALKTHROUGH.pdf) · demo: `python -m demo.app`

Real lab analyzers (Sysmex, Mindray, Roche, Abbott…) do **not** speak SQL. They
send plain-text **ASTM E1394** or **HL7 v2.x** messages over TCP or a serial
cable. This middleware sits between them and your Oracle database:

```
Sysmex XN-1000 ──ASTM/TCP──┐
Mindray BS-240 ──HL7/TCP───┤   ┌──────────────────────┐   ┌──────────────┐   ┌───────────┐
Roche Cobas ────ASTM/RS232─┴──▶│  LIS Middleware      │──▶│  Oracle DB   │──▶│ APEX UI   │
                               │  parse → validate →  │   │ lab_results  │   │ reports   │
                               │  dedupe → insert     │   │ raw_messages │   │ dashboard │
                               └──────────┬───────────┘   └──────────────┘   └───────────┘
                                          │ ACK / MLLP ACK
                                          └──────────▶ back to the analyzer
```

What it guarantees:

| Guarantee | How |
|---|---|
| No data is ever lost | every raw payload is stored in `lis_raw_messages` before parsing |
| Re-transmissions never duplicate | `UNIQUE(sample_id, test_code, analyzer_name, result_value)` + pre-insert check |
| A bad message cannot kill the listener | each connection is its own task, all exceptions are caught and logged |
| A wrong protocol label still works | protocol sniffing + automatic retry as ASTM/HL7 |
| Critical values are visible | `is_critical` flag set for `C / HH / LL`, exposed by `v_critical_results` |

---

## 1. Quick start (no Oracle needed)

```bash
cd lis-middleware
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m unittest discover -s tests -v                # 36 tests, no DB required
python -m demo.app                                     # browser demo on :8080
```

The browser demo runs the **same** parser/validator/engine code over HTTP: send
a sample message, watch the ACK, inspect the raw payload, the parsed rows and
the stored results.

## 2. Quick start (real Oracle)

```bash
# 1. create the schema objects
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/schema.sql

# 2. put the connection details in config/config.yaml
#    database.host / port / service_name / username / password

# 3. check the connection only
python -m database.db_connection

# 4. run the middleware (dry run first if you like)
python main.py --dry-run
python main.py

# 5. in another terminal, pretend to be the Sysmex
python tools/mock_analyzer.py -s astm_cbc_normal -p 6661
```

Expected log:

```
2026-11-04 10:30:01 | INFO | LIS.DB      | Oracle DB connected: 192.168.1.100:1521/ORCL (schema LIS_USER)
2026-11-04 10:30:01 | INFO | LIS.TCP     | Sysmex_XN_1000 | TCP | listening on 0.0.0.0:6661
2026-11-04 10:30:01 | INFO | LIS.MAIN    | LIS Middleware is RUNNING (2 listener(s)). Press Ctrl+C to stop.
2026-11-04 10:30:12 | INFO | LIS.TCP     | Sysmex_XN_1000: received 412 bytes from 192.168.1.50:51244
2026-11-04 10:30:12 | INFO | LIS.ASTM    | ASTM: parsed 4 result(s) from Sysmex_XN_1000
2026-11-04 10:30:12 | INFO | LIS.DBW     | inserted SAMPLE-0001 / WBC = 7.5 10*3/uL
```

## 2b. Feel what a real machine does (wire level)

The browser demo is an HTTP form — a real analyzer is *not*. To see the actual
protocol, use the device simulator, which speaks ASTM E1381 (ENQ / ACK /
checksummed frames / NAK-retry / EOT) and HL7 MLLP:

```bash
python main.py --dry-run                                            # terminal 1
python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace       # terminal 2

# abuse the link like a real cable does
python tools/device_sim.py -s astm_cbc_normal --fault checksum --fault-rate 0.4
python tools/device_sim.py -s astm_cbc_normal --fault noise   --fault-rate 0.4
python tools/device_sim.py -s astm_cbc_normal --fault drop    --fault-rate 0.25
```

And to work with **your** machine's bytes instead of samples:

```bash
python tools/sniff_com.py --port COM4 --baud 9600 --parity EVEN \
    --tap 127.0.0.1:6661 --out logs/capture.jsonl    # capture the analyzer
python tools/replay_capture.py logs/capture.jsonl --port 6661 --timing
```

Full walkthrough (Bangla): [`../docs/REAL_DEVICE.md`](../docs/REAL_DEVICE.md)

## 3. Point a real analyzer at it

On the analyzer's *Host Communication* / *LIS Connection* page set:

| Field | Value |
|---|---|
| Host / IP | IP of the machine running this middleware |
| Port | `6661` (hematology) / `6662` (chemistry) — match `config.yaml` |
| Protocol | ASTM (Sysmex, Roche) or HL7 (Mindray) |
| Data bits / parity / stop | `7 / EVEN / 1` for RS-232 ASTM, `8/N/1` for TCP |
| Sample ID | must be the barcode you scan in APEX |

Then: open Windows Firewall for the port (`New-NetFirewallRule -DisplayName "LIS 6661" -Direction Inbound -LocalPort 6661 -Protocol TCP -Action Allow`).

## 4. Code map

```
lis-middleware/
├── main.py                     # entry point: starts every enabled listener
├── config/config.yaml          # DB credentials, analyzers, test-code map
├── core/
│   ├── engine.py               # the pipeline (raw → parse → validate → insert → ACK)
│   ├── parser_factory.py       # protocol sniffing + parser selection
│   ├── validator.py            # required fields, flags, critical detection
│   └── ack_handler.py          # ASTM 0x06 ACK / MLLP ACK message
├── parsers/
│   ├── astm_parser.py          # ASTM E1394 / LIS2-A2
│   └── hl7_parser.py           # HL7 v2 ORU^R01 (OBX segments)
├── core/e1381.py               # ENQ/ACK framing + checksums (the real ASTM link)
├── listeners/
│   ├── tcp_listener.py         # asyncio TCP/MLLP + E1381 handshake
│   └── serial_listener.py      # pyserial RS-232, threaded
├── database/
│   ├── db_connection.py        # oracledb thin pool (no Oracle Client)
│   ├── db_writer.py            # inserts, dedupe, audit, stats
│   ├── memory_store.py         # same API, in-memory (demo + tests)
│   └── schema.sql              # tables + APEX views
├── demo/app.py                 # browser demo (FastAPI, same engine)
├── tools/
│   ├── device_sim.py           # wire-level analyzer simulator (E1381/MLLP + faults)
│   ├── sniff_com.py            # capture real analyzer traffic (serial or TCP tap)
│   ├── replay_capture.py       # replay a captured conversation
│   ├── mock_analyzer.py        # simple one-shot sender
│   └── replay.py               # re-parse stored raw messages
├── samples/messages.yaml       # real-shaped ASTM/HL7 payloads
└── tests/test_pipeline.py      # 36 unit tests, no Oracle required
```

## 5. Extending it

* **New analyzer brand** — add an entry under `analyzers:` in `config.yaml`. No code change unless the vendor protocol is proprietary.
* **Map machine codes to report codes** — `test_code_map:` (`HGB → HB`, `GLU → FBS`).
* **New protocol** — subclass the parser and register it in `parser_factory.build_parser`.
* **Different result table** — change the INSERT in `db_writer.insert_results`; it is the only SQL that writes results.

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused` from the mock tool | middleware not running or wrong port | check `config.yaml`, check the log line "listening on" |
| `ORA-12541: TNS:no listener` | wrong host/port/service name | `tnsping` or `python -m database.db_connection` |
| `ORA-01017: invalid username/password` | credentials in `config.yaml` | fix `database.username/password` |
| `no results parsed` | wrong protocol label | set `protocol: AUTO`, then check the logged `detected_protocol` |
| Garbage characters on serial | baud/parity mismatch | try `9600 7E1`, then `9600 8N1` |
| Analyzer never sends | analyzer is waiting for an ACK | `app.ack_mode: MLLP` for HL7 analyzers |
| Same result twice | analyzer re-transmits (normal) | dedupe keeps one row; see `status = DUPLICATE` in `lis_raw_messages` |
| Analyzer sends ENQ, nothing after | the host is not answering ACK | our listener answers ACK on ENQ; check you are on the ASTM port |
| `checksum mismatch` in the log | cable/parity problem or real line noise | the listener NAKs and the analyzer repeats; fix parity (7E1) |
| Machine stops mid-transfer | it waits for a frame ACK | check the log for `bad block … -> NAK`; that is the retry path working |

## 7. Deploy as a service

Windows (NSSM or Task Scheduler):

```powershell
pip install pywin32
# then point the service at: python C:\lis\lis-middleware\main.py
```

Linux (systemd) — full unit file in `docs/DEPLOYMENT.md`:

```bash
sudo systemctl enable --now lis-middleware
journalctl -u lis-middleware -f
```

## 8. License / medical-device note

This is integration software, not a certified medical device. Validate every
result path against your analyzer's manual and your lab's SOP — and never let
the middleware *calculate* a result; it only transports and stores what the
instrument measured.
