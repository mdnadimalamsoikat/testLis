# Running the browser demo

```bash
cd lis-middleware
pip install -r requirements.txt
python -m demo.app                 # http://localhost:8080
python -m demo.app --port 9000     # custom port
```

The demo runs the **same pipeline** as `main.py` (`core/engine.py`), only the
storage differs: an in-memory store instead of Oracle. Everything you see is
produced by the production code path.

| Panel | What it shows |
|---|---|
| Send a message | Pick an analyzer, a protocol label (AUTO sniffs it) and one of the sample payloads; edit the raw text freely |
| Last message | ACK / NAK, message id, latency, the 5 pipeline stages, and the exact bytes that would go back to the analyzer |
| Database | Result / today / abnormal / critical counters |
| Tables | `lab_results` rows, per-sample grouping, and the raw message archive with its status (PARSED / DUPLICATE / IGNORED / ERROR) |
| Test code mapping | `HGB → HB`, `GLU → FBS` … applied before insert |

Useful things to try:

* press **×3 re-transmit** — the second and third messages come back `DUPLICATE`
  with `stored = 0`, and the analyzer still receives an ACK;
* pick **Broken message** — you get `ERROR` + NAK, but the raw payload is kept
  (visible in *raw messages*);
* pick **Quality control sample** — `IGNORED`, acknowledged, not stored;
* pick **Value+unit merged (Roche)** — `5,8 mmol/L` and `>150 U/L` are split into
  a number and a unit;
* delete an `R|` line from the payload and send — fewer results, no crash.

## Live preview

Start the demo (`python -m demo.app`) and open the forwarded port — the UI is
served by the same process, no separate frontend build step.
