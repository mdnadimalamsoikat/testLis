"""
Serial (RS-232) listener for analyzers wired straight into the LIS host.

Requires ``pyserial``. Configuration in ``config.yaml``::

    - name: "Roche_Cobas_c311"
      connection_type: "SERIAL"
      serial_port: "COM3"      # /dev/ttyS0 on Linux
      baud_rate: 9600
      data_bits: 7
      parity: "EVEN"           # NONE / EVEN / ODD
      stop_bits: 1

Why 7-E-1? That is what ASTM E1381 specifies, and older Roche/Beckman
instruments honour it. If you get garbage, check the analyzer's
"Host communication" page first — a wrong baud rate is the #1 cause.

pyserial is blocking, so the port is read in a worker thread and completed
messages are handed to the async sink with ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger("LIS.SERIAL")

ACK = "\x06"
NAK = "\x15"
ENQ = "\x05"
STX = "\x02"
ETX = "\x03"
EOT = "\x04"


class ResultSink(Protocol):
    async def handle_payload(self, raw: str, analyzer: Dict[str, Any], peer: str) -> Dict[str, Any]:
        ...


class SerialListener:
    def __init__(self, analyzer: Dict[str, Any], sink: ResultSink, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.analyzer = analyzer
        self.sink = sink
        self.loop = loop or asyncio.get_event_loop()
        self.name = analyzer.get("name", "SERIAL")
        self.port = analyzer.get("serial_port", "COM3")
        self.baud_rate = int(analyzer.get("baud_rate", 9600))
        self.data_bits = int(analyzer.get("data_bits", 8))
        self.parity = str(analyzer.get("parity", "NONE")).upper()
        self.stop_bits = int(analyzer.get("stop_bits", 1))
        self.timeout = float(analyzer.get("read_timeout", 1.0))

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self.messages_received = 0
        self.last_message_at: Optional[str] = None

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"serial-{self.port}", daemon=True)
        self._thread.start()
        logger.info(
            "%-22s | Serial | %s @ %s baud %s-%s-%s",
            self.name, self.port, self.baud_rate, self.data_bits, self.parity[0], self.stop_bits,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._running = False
        logger.info("%s serial listener stopped", self.name)

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "protocol": self.analyzer.get("protocol", "AUTO"),
            "connection_type": "SERIAL",
            "host": self.port,
            "port": self.baud_rate,
            "active_connections": 1 if self._running else 0,
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
            "running": self._running,
        }

    # ----------------------------------------------------------------- private
    def _run(self) -> None:
        try:
            import serial  # pyserial
        except ImportError:
            logger.error("pyserial missing — run: pip install pyserial")
            return

        parity_map = {"NONE": serial.PARITY_NONE, "EVEN": serial.PARITY_EVEN, "ODD": serial.PARITY_ODD}
        stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}

        try:
            with serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=self.data_bits,
                parity=parity_map.get(self.parity, serial.PARITY_NONE),
                stopbits=stop_map.get(self.stop_bits, serial.STOPBITS_ONE),
                timeout=self.timeout,
            ) as port:
                self._running = True
                logger.info("%s: serial port %s open", self.name, self.port)
                buffer = ""
                while not self._stop.is_set():
                    chunk = port.read(port.in_waiting or 1).decode("ascii", errors="ignore")
                    if not chunk:
                        if buffer.strip():
                            self._dispatch(buffer, port)
                            buffer = ""
                        continue
                    buffer += chunk
                    if self._is_complete(buffer):
                        self._dispatch(buffer, port)
                        buffer = ""
        except Exception as exc:
            logger.error("%s: serial failure on %s — %s", self.name, self.port, exc)
            logger.error("check cable, COM port number and baud rate in config.yaml")
        finally:
            self._running = False

    def _is_complete(self, buffer: str) -> bool:
        if ETX in buffer or EOT in buffer:
            return True
        upper = buffer.upper()
        return "\n" in buffer and "MSH|" in upper and buffer.rstrip().endswith(("F", "\x1c"))

    def _dispatch(self, buffer: str, port: Any = None) -> None:
        raw = buffer.replace(ENQ, "").replace(STX, "").replace(ETX, "").replace(EOT, "")
        raw = raw.strip()
        if not raw:
            return
        self.messages_received += 1
        self.last_message_at = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("%s: received %d chars on %s", self.name, len(raw), self.port)
        future = asyncio.run_coroutine_threadsafe(
            self.sink.handle_payload(raw, self.analyzer, f"serial:{self.port}"), self.loop
        )
        try:
            ok = bool(future.result(timeout=30).get("ok"))
        except Exception as exc:
            logger.error("%s: sink error: %s", self.name, exc)
            ok = False
        if port is not None:
            try:
                port.write((ACK if ok else NAK).encode("ascii"))
                port.flush()
            except Exception as exc:
                logger.warning("%s: could not send ACK: %s", self.name, exc)
