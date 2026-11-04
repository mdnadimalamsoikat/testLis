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
| [`docs/REAL_DEVICE.md`](docs/REAL_DEVICE.md) | Honest guide: demo vs real analyzer, wire-level simulation, capturing the real machine's bytes (Bangla) |
| [`docs/LAB_DAY.md`](docs/LAB_DAY.md) | **Start here for the real thing** — 3-day plan (simulate → rehearse → lab), every machine setting you must change, other simulators, rollback (Bangla) |

## 60-second start (no Oracle needed)

```bash
cd lis-middleware
pip install -r requirements.txt

python -m unittest discover -s tests -v     # 36 tests, no DB
python -m demo.app                          # http://localhost:8080
```

Then send a message in the browser, or drive the real wire protocol:

```bash
python main.py --dry-run                    # listeners on 6661 (ASTM) / 6662 (HL7)
python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace
#  → <ENQ>            ← <ACK>
#  → <STX>1H|\^&|…<ETX>?2   ← <ACK>   (×8 frames)
#  → <EOT>
#  RESULT: the middleware behaved like a real LIS endpoint ✅
```

`device_sim` can also inject the faults a real cable produces
(`--fault checksum|noise|drop`), and `tools/sniff_com.py` captures **your**
analyzer's own bytes so you test against the machine, not against samples.

## For the real lab

Three steps: simulate today, rehearse tomorrow, drive to the lab on day 3.
Full checklist: [`docs/LAB_DAY.md`](docs/LAB_DAY.md).

```bash
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @lis-middleware/database/schema.sql
export LIS_DB_PASSWORD='...'      # keep passwords out of config.yaml
python tools/preflight.py         # every check at once — no ❌ before you travel
python main.py                    # real Oracle writes
```

Full instructions — analyzer host settings, firewall rules, Windows service /
systemd, and a troubleshooting table — are in the PDF guide.

## Notes

* **Nothing is ever lost:** every raw payload is stored before parsing, so a
  parser bug can be fixed and old messages replayed (`tools/replay.py`).
* **Re-transmissions are safe:** unique key + pre-insert check; the analyzer
  still gets its ACK.
* **Wire level:** ASTM E1381 handshake, checksums, NAK-retry and line-noise
  resync are implemented and tested — that is the layer a demo HTTP form hides.
* **Quality-control samples** (`QC…`, `CTRL…`, `CAL…`) are acknowledged but not
  stored as patient data.
* This is integration software, not a certified medical device. It transports
  and stores what the instrument measured; it never calculates a result.
