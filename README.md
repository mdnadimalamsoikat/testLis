# LIS — Lab Analyzer → Oracle Database → Oracle APEX

Real lab analyzers (Sysmex, Mindray, Roche, Abbott…) send plain-text **ASTM
E1394** or **HL7 v2.x** messages over TCP or RS-232. Oracle does not understand
either. This repository contains the middleware that closes that gap, plus a
browser demo and a full written guide.

```
Sysmex XN ──ASTM/TCP──┐
Mindray BS-240 ─HL7───┤   ┌────────────────────┐   ┌───────────────┐   ┌──────────┐
Roche Cobas ──Serial──┴──▶│  LIS Middleware    │──▶│  Oracle DB    │──▶│  APEX    │
   (python, 28 tests)     │  parse → validate  │   │ lab_results   │   │ reports  │
                          │  dedupe → insert   │   │ raw_messages  │   │ alerts   │
                          └────────┬───────────┘   └───────────────┘   └──────────┘
                                   └── ACK / MLLP ACK ──▶ analyzer
```

## What is here

| Path | What it is |
|---|---|
| [`lis-middleware/`](lis-middleware/) | The working Python middleware — listeners, ASTM/HL7 parsers, validator, Oracle writer, mock analyzer, tests |
| [`lis-middleware/demo/`](lis-middleware/demo/) | Browser demo: send a message, watch parse → validate → store → ACK |
| [`docs/PROJECT_GUIDE_BN.pdf`](docs/PROJECT_GUIDE_BN.pdf) | 10-page Bengali guide: architecture, schema, APEX, setup, deploy, troubleshooting |
| [`docs/CODE_WALKTHROUGH.pdf`](docs/CODE_WALKTHROUGH.pdf) | The same pipeline explained code-first, with the real functions |

## 60-second start (no Oracle needed)

```bash
cd lis-middleware
pip install -r requirements.txt

python -m unittest discover -s tests -v     # 28 tests, no DB
python -m demo.app                          # http://localhost:8080
```

Then send a message in the browser, or replay a real one over the wire:

```bash
python main.py --dry-run                    # listeners on 6661 (ASTM) / 6662 (HL7)
python tools/mock_analyzer.py -s astm_cbc_normal -p 6661
#  → 127.0.0.1:6661  397 bytes (ASTM)
#  ← ACK (0x06)
```

## For the real lab

```bash
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @lis-middleware/database/schema.sql
export LIS_DB_PASSWORD='...'      # keep passwords out of config.yaml
python main.py                    # real Oracle writes
```

Full instructions — analyzer host settings, firewall rules, Windows service /
systemd, and a troubleshooting table — are in the PDF guide.

## Notes

* **Nothing is ever lost:** every raw payload is stored before parsing, so a
  parser bug can be fixed and old messages replayed (`tools/replay.py`).
* **Re-transmissions are safe:** unique key + pre-insert check; the analyzer
  still gets its ACK.
* **Quality-control samples** (`QC…`, `CTRL…`, `CAL…`) are acknowledged but not
  stored as patient data.
* This is integration software, not a certified medical device. It transports
  and stores what the instrument measured; it never calculates a result.
