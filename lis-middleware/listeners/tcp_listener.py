"""
TCP (MLLP) listener — the path used by almost every modern analyzer.

An analyzer opens a TCP connection to the middleware, pushes a message and
(minutes, sometimes seconds later) waits for an acknowledgement. Three
behaviours are handled here because real machines differ:

* **HL7 MLLP** — payload wrapped in ``\\x0b`` ... ``\\x1c\\r``. Reply ``\\x06``.
* **ASTM over TCP** — control characters ``ENQ``/``STX``/``ETX`` or a bare
  ``H|...`` block. Reply ``ACK`` (``\\x06``).
* **HL7 basic mode** — bare text with no framing, closed by the client. Reply
  ``ACK`` when an MSH segment is seen.

Each connection is handled in its own asyncio task, so many analyzers can be
connected at once without blocking each other.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger("LIS.TCP")

MLLP_START = b"\x0b"
MLLP_END = b"\x1c"
CR = b"\r"
ACK = b"\x06"
NAK = b"\x15"


class ResultSink(Protocol):
    """Anything that can accept a raw payload and return the parse result."""

    async def handle_payload(self, raw: str, analyzer: Dict[str, Any], peer: str) -> Dict[str, Any]:
        ...


class TCPListener:
    def __init__(self, analyzer: Dict[str, Any], sink: ResultSink,
                 host: str = "0.0.0.0", port: int = 6661):
        self.analyzer = analyzer
        self.sink = sink
        self.host = analyzer.get("host", host)
        self.port = int(analyzer.get("port", port))
        self.name = analyzer.get("name", f"TCP:{self.port}")
        self._server: Optional[asyncio.AbstractServer] = None
        self._connections = 0
        self.last_message_at: Optional[str] = None
        self.messages_received = 0

    # ------------------------------------------------------------------ public
    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_client, self.host, self.port, reuse_address=True
        )
        logger.info("%-22s | TCP | listening on %s:%s", self.name, self.host, self.port)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover
                pass
            self._server = None
            logger.info("%s listener stopped", self.name)

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "protocol": self.analyzer.get("protocol", "AUTO"),
            "connection_type": "TCP",
            "host": self.host,
            "port": self.port,
            "active_connections": self._connections,
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
            "running": self._server is not None,
        }

    # ----------------------------------------------------------------- private
    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer_info = writer.get_extra_info("peername")
        peer = f"{peer_info[0]}:{peer_info[1]}" if peer_info else "unknown"
        self._connections += 1
        logger.info("%s: connection from %s (%d active)", self.name, peer, self._connections)
        buffer = b""
        idle_since = time.monotonic()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning("%s: idle 300s, closing %s", self.name, peer)
                    break

                if not chunk:
                    # client closed: flush anything left (HL7 basic mode)
                    if buffer.strip():
                        await self._consume(buffer, peer)
                    break
                buffer += chunk

                # An analyzer can batch several messages per TCP write.
                while True:
                    payload, rest, needs_ack = self._extract(buffer)
                    if payload is None:
                        break
                    buffer = rest
                    idle_since = time.monotonic()
                    ok = await self._consume(payload, peer)
                    if needs_ack:
                        writer.write(ACK if ok else NAK)
                        await writer.drain()

                if len(buffer) > 1024 * 1024:  # 1 MB guard
                    logger.error("%s: message exceeded 1MB, dropping buffer", self.name)
                    buffer = b""

                if buffer and (time.monotonic() - idle_since) > 5:
                    # unfinished frame — hand over what we have instead of hanging
                    await self._consume(buffer, peer)
                    buffer = b""
        except (ConnectionResetError, BrokenPipeError):
            logger.warning("%s: connection reset by %s", self.name, peer)
        except Exception as exc:  # pragma: no cover
            logger.exception("%s: client handler error: %s", self.name, exc)
        finally:
            self._connections = max(0, self._connections - 1)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("%s: disconnected %s", self.name, peer)

    def _extract(self, buffer: bytes) -> tuple[Optional[bytes], bytes, bool]:
        """Pull one complete message out of the stream buffer.

        Returns ``(payload, remaining, needs_ack)``. ``payload`` is None when
        no complete message is available yet.
        """
        if not buffer:
            return None, buffer, False

        # drop stray bytes analyzers leave between frames (EOT / NUL / ENQ)
        buffer = buffer.lstrip(b"\x04\x00\x05")
        if not buffer:
            return None, buffer, False

        # --- HL7 MLLP: \x0b ... \x1c --------------------------------
        start = buffer.find(MLLP_START)
        if start != -1:
            end = buffer.find(MLLP_END, start)
            if end == -1:
                return None, buffer, False
            payload = buffer[start + 1:end]
            remaining = buffer[end + 1:]
            if remaining.startswith(CR):
                remaining = remaining[1:]
            return payload, remaining, True
        if buffer.startswith(b"\x0b"):
            return None, buffer, False

        # --- ASTM: \x05 "H|..." \x02 ... \x03 -----------------------
        if buffer.startswith(b"\x05"):  # ENQ — strip it, message follows
            buffer = buffer[1:]

        if MLLP_END in buffer:  # stray FS from a mis-framed client
            idx = buffer.find(MLLP_END)
            payload, remaining = buffer[:idx], buffer[idx + 1:]
            return payload, remaining, True

        if b"\x03" in buffer:  # ASTM ETX terminates the record set
            idx = buffer.find(b"\x03")
            payload, remaining = buffer[:idx], buffer[idx + 1:]
            return payload, remaining, True

        # --- bare text: HL7 basic mode / ASTM without control chars --
        text = buffer.decode("utf-8", errors="ignore")
        if "\r" in text or "\n" in text:
            complete = self._looks_complete(text)
            if complete:
                return buffer, b"", True
        return None, buffer, False

    @staticmethod
    def _looks_complete(text: str) -> bool:
        upper = text.upper()
        if "MSH|" in upper and text.rstrip().endswith(("F", "|F")) and "\n" in text:
            return True
        if re_astm_terminator(text):
            return True
        if "MSH|" in upper:
            return True
        return False

    async def _consume(self, payload: bytes, peer: str) -> bool:
        try:
            raw = payload.decode("utf-8", errors="ignore").strip("\x00")
        except Exception:
            logger.error("%s: undecodable payload from %s", self.name, peer)
            return False
        # a frame holding nothing but control characters is not a message
        if not raw.strip().strip("\x00\x02\x03\x04\x05\x06\x15\x1c\r\n"):
            return True

        self.messages_received += 1
        self.last_message_at = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("%s: received %d bytes from %s", self.name, len(raw), peer)

        result = await self.sink.handle_payload(raw, self.analyzer, peer)
        return bool(result.get("ok"))


def re_astm_terminator(text: str) -> bool:
    """True when the buffer holds a complete ASTM record set."""
    if "H|" not in text.upper():
        return False
    lines = [ln for ln in text.replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return False
    return any(ln.strip().upper().startswith("L|") for ln in lines) or len(lines) > 1
