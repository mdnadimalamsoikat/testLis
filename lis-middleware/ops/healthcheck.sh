#!/usr/bin/env bash
# healthcheck.sh — is the service alive and is the port answering?
#   ./ops/healthcheck.sh 6661
# Exit 0 = healthy, 1 = not. Use it from cron / Zabbix / APEX "check" button.
set -u
PORT="${1:-6661}"
LOG="${2:-logs/lis_service.log}"

fail=0
if pgrep -f "service.py" >/dev/null 2>&1; then
  echo "✅ process running"
else
  echo "❌ service.py not running"
  fail=1
fi

if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
  echo "✅ port ${PORT} accepting connections"
  exec 3<&- 2>/dev/null
elif python3 - "$PORT" <<'PROBE' 2>/dev/null
import socket, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2)
s.close()
PROBE
then
  echo "✅ port ${PORT} accepting connections"
else
  echo "❌ port ${PORT} closed"
  fail=1
fi

if [ -f "$LOG" ]; then
  ERRORS=$(grep -c "ERROR" "$LOG" 2>/dev/null | head -1)
  echo "ℹ️  ${ERRORS:-0} ERROR line(s) in ${LOG}"
  tail -n 3 "$LOG"
fi

exit $fail
