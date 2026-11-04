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
| The lab never edits a file | analyzers/ports/protocols live in `lis_config` and are changed **in APEX**; the service picks changes up within ~5 s |
| A bad edit never stops the lab | invalid rows are refused, `last_error` is shown in APEX, the previous working config keeps running |

---

## 1. Quick start (no Oracle needed)

```bash
cd lis-middleware
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m unittest discover -s tests -v                # 53 tests, no DB required
python -m demo.app                                     # browser demo on :8080
```

The browser demo runs the **same** parser/validator/engine code over HTTP: send
a sample message, watch the ACK, inspect the raw payload, the parsed rows and
the stored results.

## 2. Starting for real

`docs/LAB_DAY.md` (Bangla) is the three-day plan. Short version:

```bash
# today, at home: simulate the machine
python main.py --dry-run
python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace

# tomorrow: rehears with your own config (copy the example, fill 4 values)
cp config/config.yaml.example config/config.yaml
export LIS_DB_PASSWORD='...'
python tools/preflight.py            # ← run this before every trip to the lab

# lab day: only the machine's Host setting changes (IP + port 6661)
```

## 3. Everything is controlled from APEX (the point of this project)

**The lab never touches the server.** Analyzers, ports, protocols, serial
settings, test-code mapping and start/stop all live in Oracle tables and are
edited on APEX pages. Python is only a worker: it reads the tables, applies the
change, and writes back "applied" or "error".

```
APEX form (lab staff)  ──►  lis_config / lis_commands  ──►  service.py (5 s poll)
        ▲                                                        │
        └──────────  lis_analyzer_status  ◄──────────────────────┘
                     ONLINE · last message · last_error
```

One-time setup — two options, same result:

```bash
# A. one command, no Oracle Client needed (recommended)
pip install oracledb
python tools/apex_setup.py --user lis_user --service XEPDB1 --write-config
python tools/apex_setup.py --verify-only          # re-check any time

# B. through SQL*Plus, statement by statement (shows the exact failing SQL)
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/schema.sql
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/apex_install.sql
python tools/install_sql.py --sqlplus "sqlplus -S lis_user/password@//localhost:1521/XEPDB1"
```

Already running APEX + 19c on your own machine? [`../docs/LOCAL_SETUP.md`](../docs/LOCAL_SETUP.md)
is the 20-minute walkthrough for exactly that case.

Then build the pages (`docs/APEX_CONTROL.md`, Bangla, §2 shows every field and
LOV) and start the service — **no analyzer settings in any file**:

```bash
export LIS_DB_PASSWORD='...'
python service.py                       # APEX-driven mode (production)
python service.py --mode file           # config.yaml mode (legacy / demo)
```

| Operator action in APEX | What the service does | Time |
|---|---|---|
| change a port, Save | restarts **only that** listener | ~5 s |
| `enabled = N` | closes that port, keeps the DB connection | ~5 s |
| new analyzer, `status = DRAFT → ACTIVE` | binds the new port | ~5 s |
| press `RELOAD` | re-reads every row, restarts only what changed | next tick |
| press `SELFTEST` | pushes a sample through the live engine, stores nothing | next tick |
| mistype a port | **refuses** it, writes `last_error`, old config keeps running | — |

`config/config.yaml` now holds only the DB host/service/user, the log level and
a fallback analyzer list; the password comes from `LIS_DB_PASSWORD`. Everything
a lab technician would want to change is a row in Oracle.

## 4. Quick start (real Oracle)

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

## 5. Feel what a real machine does (wire level)

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

## 6. Point a real analyzer at it

On the analyzer's *Host Communication* / *LIS Connection* page set:

| Field | Value |
|---|---|
| Host / IP | IP of the machine running this middleware |
| Port | `6661` (hematology) / `6662` (chemistry) — match `config.yaml` |
| Protocol | ASTM (Sysmex, Roche) or HL7 (Mindray) |
| Data bits / parity / stop | `7 / EVEN / 1` for RS-232 ASTM, `8/N/1` for TCP |
| Sample ID | must be the barcode you scan in APEX |

Then: open Windows Firewall for the port (`New-NetFirewallRule -DisplayName "LIS 6661" -Direction Inbound -LocalPort 6661 -Protocol TCP -Action Allow`).

## 7. Code map

```
lis-middleware/
├── service.py                  # PRODUCTION entry point: APEX-driven, hot reload
├── main.py                     # dev entry point: config.yaml, start once
├── config/config.yaml          # DB credentials, analyzers, test-code map
├── config/config.yaml.example  # commented template to copy (4 values to change)
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
│   ├── control.py              # reads lis_config, claims lis_commands, writes heartbeat
│   ├── schema.sql              # results tables + APEX views
│   └── apex_install.sql        # APEX control plane: lis_config, commands, status, v_* views
├── demo/app.py                 # browser demo (FastAPI, same engine)
├── ops/
│   ├── lis-middleware.service       # systemd unit (APEX mode)
│   ├── install_service_windows.ps1  # NSSM installer (Windows service, auto-restart)
│   └── healthcheck.sh               # is the service alive, is the port open?
├── tools/
│   ├── apex_setup.py           # one-command install + verify (python-oracledb)
│   ├── install_sql.py          # same install through SQL*Plus, per-statement reporting
│   ├── preflight.py            # go/no-go checks before the lab trip (incl. APEX tables)
│   ├── device_sim.py           # wire-level analyzer simulator (E1381/MLLP + faults)
│   ├── sniff_com.py            # capture real analyzer traffic (serial or TCP tap)
│   ├── replay_capture.py       # replay a captured conversation
│   ├── mock_analyzer.py        # simple one-shot sender
│   └── replay.py               # re-parse stored raw messages
├── samples/messages.yaml       # real-shaped ASTM/HL7 payloads
└── tests/test_pipeline.py      # 53 unit tests, no Oracle required
```

## 8. Extending it

* **New analyzer brand** — create a row in **APEX → Analyzers** (no file, no code) unless the vendor protocol is proprietary.
* **Map machine codes to report codes** — **APEX → Test code map** (`HGB → HB`, `GLU → FBS`), per analyzer or global.
* **New operator action** — add a `command_type` and one `elif` in `service.py::_run_command`.
* **New protocol** — subclass the parser and register it in `parser_factory.build_parser`.
* **Different result table** — change the INSERT in `db_writer.insert_results`; it is the only SQL that writes results.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused` from the mock tool | middleware not running or wrong port | check the log line "listening on"
| `last_error` = `cannot open port 6661: address in use` | another program holds the port | `netstat -ano | findstr 6661` |
| APEX shows `STOPPED` although enabled | the analyzer is `DRAFT`, or the port could not be opened | view `v_lis_analyzers.last_error` |
| Nothing changes after editing in APEX | service not running, or `service.py` started with `--mode file` | `bash ops/healthcheck.sh`, restart the service |
| `ORA-12541: TNS:no listener` | wrong host/port/service name | `tnsping` or `python -m database.db_connection` |
| `ORA-01017: invalid username/password` | credentials in `config.yaml` | fix `database.username/password` |
| `no results parsed` | wrong protocol label | set `protocol: AUTO`, then check the logged `detected_protocol` |
| Garbage characters on serial | baud/parity mismatch | try `9600 7E1`, then `9600 8N1` |
| Analyzer never sends | analyzer is waiting for an ACK | `app.ack_mode: MLLP` for HL7 analyzers |
| Same result twice | analyzer re-transmits (normal) | dedupe keeps one row; see `status = DUPLICATE` in `lis_raw_messages` |
| Analyzer sends ENQ, nothing after | the host is not answering ACK | our listener answers ACK on ENQ; check you are on the ASTM port |
| `checksum mismatch` in the log | cable/parity problem or real line noise | the listener NAKs and the analyzer repeats; fix parity (7E1) |
| Machine stops mid-transfer | it waits for a frame ACK | check the log for `bad block … -> NAK`; that is the retry path working |

## 10. Deploy as a service

**Windows** (runs as a real Windows service, restarts itself, password in the
machine environment — never in a file):

```powershell
.\ops\install_service_windows.ps1 -ProjectDir "C:\lis\lis-middleware"
nssm status LISMiddleware
Get-Content "C:\lis\lis-middleware\logs\lis_service.log" -Wait
```

**Linux** (systemd unit in `ops/`):

```bash
sudo cp ops/lis-middleware.service /etc/systemd/system/
sudo cp ops/lis-middleware.env.example /etc/lis-middleware.env   # chmod 600, set the password
sudo systemctl enable --now lis-middleware
journalctl -u lis-middleware -f
```

Both start `service.py` (APEX mode, auto-reload). `ops/healthcheck.sh` answers
"is it alive and is the port open?" for cron/Zabbix.

## 11. License / medical-device note

This is integration software, not a certified medical device. Validate every
result path against your analyzer's manual and your lab's SOP — and never let
the middleware *calculate* a result; it only transports and stores what the
instrument measured.
