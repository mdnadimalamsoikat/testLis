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

from core.e1381 import ACK, EOT, E1381Framer, ENQ, NAK, compute_checksum

logger = logging.getLogger("LIS.TCP")

MLLP_START = b"\x0b"
MLLP_END = b"\x1c"
CR = b"\r"
ACK_B = bytes([ACK])
NAK_B = bytes([NAK])
EOT_B = bytes([EOT])


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
        self._ready = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._connections = 0
        self.last_message_at: Optional[str] = None
        self.messages_received = 0

    # ------------------------------------------------------------------ public
    async def serve(self) -> None:
        """Bind the port and keep serving until stop() is called."""
        self._server = await asyncio.start_server(
            self._on_client, self.host, self.port, reuse_address=True
        )
        logger.info("%-22s | TCP | listening on %s:%s", self.name, self.host, self.port)
        self._ready.set()
        async with self._server:
            await self._server.serve_forever()

    async def start(self) -> None:
        """Backwards-compatible alias used by main.py."""
        await self.serve()

    async def start_background(self) -> asyncio.Task:
        """Run serve() as a task and wait until the port is actually bound."""
        task = asyncio.create_task(self.serve(), name=self.name)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            logger.error("%s: port %s did not open", self.name, self.port)
        return task

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

        framer = E1381Framer()
        # a frame is buffered until its ACK is known, so a NAK can trigger a retry
        pending_frame = ""
        mllp_buffer = b""
        e1381_mode = False        # becomes True after the first ENQ

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning("%s: idle 300s, closing %s", self.name, peer)
                    break

                if not chunk:
                    leftover = framer.flush()
                    if leftover and leftover.text.strip():
                        pending_frame += leftover.text
                    if pending_frame.strip():
                        await self._consume(pending_frame, peer)
                    if mllp_buffer.strip():
                        await self._consume(mllp_buffer, peer)
                    break

                # HL7 analyzers frame with VT ... FS; ASTM never does
                mllp_buffer += chunk
                if MLLP_START in mllp_buffer:
                    e1381_mode = False
                    while True:
                        payload, rest, needs_ack = self._extract(mllp_buffer)
                        if payload is None:
                            break
                        mllp_buffer = rest
                        ok = await self._consume(payload, peer)
                        if needs_ack:
                            writer.write(ACK_B if ok else NAK_B)
                            await writer.drain()
                    if not mllp_buffer or mllp_buffer.strip()[:1] != MLLP_START:
                        mllp_buffer = mllp_buffer if mllp_buffer.strip() else b""
                    continue

                events = framer.feed(chunk)
                for event in events:
                    if event.kind == "enq":
                        e1381_mode = True
                        logger.debug("%s: ENQ from %s", self.name, peer)
                        writer.write(ACK_B)
                        await writer.drain()
                        continue

                    if event.kind == "eot":
                        # end of transmission: process whatever we collected
                        if pending_frame.strip():
                            ok = await self._consume(pending_frame, peer)
                            pending_frame = ""
                        mllp_buffer = b""
                        continue

                    if event.kind == "frame":
                        if not event.checksum_ok:
                            # bad block -> NAK, the sender repeats the frame
                            writer.write(NAK_B)
                            await writer.drain()
                            continue
                        pending_frame += event.text + "\r"
                        writer.write(ACK_B)
                        await writer.drain()
                        continue

                    if event.kind == "text":
                        # inside a E1381 session a bare text block is a bad frame:
                        # NAK so the analyzer repeats it
                        if e1381_mode and event.detail != "unterminated":
                            logger.warning("%s: bad block from %s -> NAK", self.name, peer)
                            writer.write(NAK_B)
                            await writer.drain()
                            continue
                        # unframed / loose ASTM or HL7 basic mode
                        if event.detail == "unterminated":
                            pending_frame += event.text
                            continue
                        pending_frame += event.text + "\r"
                        if not e1381_mode:
                            ok = await self._consume(pending_frame, peer)
                            pending_frame = ""
                            writer.write(ACK_B if ok else NAK_B)
                            await writer.drain()

                # unframed text that never sees an ETX (basic mode): send it on
                if pending_frame and not e1381_mode and not chunk.endswith(bytes([ETX])):
                    if len(pending_frame) > 60 and "\r" in pending_frame.strip():
                        ok = await self._consume(pending_frame, peer)
                        pending_frame = ""
                        writer.write(ACK_B if ok else NAK_B)
                        await writer.drain()

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

    async def _consume(self, payload, peer: str) -> bool:
        """Hand one message to the sink — accepts str (ASTM framer) or bytes (MLLP)."""
        if isinstance(payload, (bytes, bytearray)):
            try:
                raw = payload.decode("utf-8", errors="ignore").strip("\x00")
            except Exception:
                logger.error("%s: undecodable payload from %s", self.name, peer)
                return False
        else:
            raw = str(payload)

        # a frame holding nothing but control characters is not a message
        if not raw.strip().strip("\x00\x02\x03\x04\x05\x06\x15\x1c\r\n"):
            return True

        self.messages_received += 1
        self.last_message_at = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("%s: received %d bytes from %s", self.name, len(raw), peer)

        result = await self.sink.handle_payload(raw, self.analyzer, peer)
        return bool(result.get("ok"))

    # ------------------------------------------------------------------ MLLP
    def _extract(self, buffer: bytes):
        """Pull one MLLP message (VT ... FS CR) out of the stream buffer."""
        if not buffer:
            return None, buffer, False
        start = buffer.find(MLLP_START)
        if start == -1:
            return None, buffer[-1:], False        # keep only the tail
        end = buffer.find(MLLP_END, start)
        if end == -1:
            return None, buffer[start:], False
        payload = buffer[start + 1:end]
        remaining = buffer[end + 1:]
        if remaining.startswith(CR):
            remaining = remaining[1:]
        return payload, remaining, True


