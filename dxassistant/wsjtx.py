from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

MAGIC = 0xADBCCBDA


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def bool(self) -> bool:
        return bool(self.u8())

    def u32(self) -> int:
        value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def i32(self) -> int:
        value = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        value = struct.unpack_from(">Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def f64(self) -> float:
        value = struct.unpack_from(">d", self.data, self.pos)[0]
        self.pos += 8
        return value

    def qstring(self) -> str:
        length = self.u32()
        if length == 0xFFFFFFFF:
            return ""
        value = self.data[self.pos : self.pos + length]
        self.pos += length
        return value.decode("utf-8", errors="replace")


class Writer:
    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def u8(self, value: int) -> None:
        self.parts.append(struct.pack(">B", value))

    def u32(self, value: int) -> None:
        self.parts.append(struct.pack(">I", value & 0xFFFFFFFF))

    def i32(self, value: int) -> None:
        self.parts.append(struct.pack(">i", value))

    def f64(self, value: float) -> None:
        self.parts.append(struct.pack(">d", value))

    def qstring(self, value: str) -> None:
        raw = value.encode("utf-8")
        self.u32(len(raw))
        self.parts.append(raw)

    def bytes(self) -> bytes:
        return b"".join(self.parts)


@dataclass(slots=True)
class Decode:
    time_ms: int
    snr: int
    delta_time: float
    delta_frequency: int
    mode: str
    message: str
    low_confidence: bool
    off_air: bool


class WsjtxProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        on_message: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.on_message = on_message
        self.transport: asyncio.DatagramTransport | None = None
        self.remote: tuple[str, int] | None = None
        self.schema = 3
        self.wsjtx_id = "WSJT-X"

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            reader = Reader(data)
            magic = reader.u32()
            schema = reader.u32()
            msg_type = reader.u32()
            wsjtx_id = reader.qstring()
            if magic != MAGIC:
                return

            self.remote = addr
            self.schema = schema
            self.wsjtx_id = wsjtx_id or "WSJT-X"

            payload: dict = {
                "type": msg_type,
                "schema": schema,
                "id": self.wsjtx_id,
                "remote": addr,
            }

            if msg_type == 0:
                payload.update({
                    "max_schema": reader.u32(),
                    "version": reader.qstring(),
                    "revision": reader.qstring(),
                })
            elif msg_type == 1:
                payload.update(self._read_status(reader))
            elif msg_type == 2:
                payload["new"] = reader.bool()
                payload["decode"] = Decode(
                    time_ms=reader.u32(),
                    snr=reader.i32(),
                    delta_time=reader.f64(),
                    delta_frequency=reader.u32(),
                    mode=reader.qstring(),
                    message=reader.qstring(),
                    low_confidence=reader.bool(),
                    off_air=reader.bool(),
                )
            elif msg_type == 3:
                payload["window"] = reader.u8() if reader.pos < len(data) else 0
            elif msg_type == 5:
                payload["logged"] = True
            elif msg_type == 6:
                payload["closed"] = True
            elif msg_type in (10, 12):
                try:
                    payload["logged_adif"] = reader.qstring()
                except Exception:
                    pass

            asyncio.create_task(self.on_message(payload))
        except Exception as exc:
            asyncio.create_task(self.on_message({"type": -1, "error": str(exc)}))

    def _read_status(self, r: Reader) -> dict:
        status = {
            "dial_frequency": r.u64(),
            "mode": r.qstring(),
            "dx_call": r.qstring(),
            "report": r.qstring(),
            "tx_mode": r.qstring(),
            "tx_enabled": r.bool(),
            "transmitting": r.bool(),
            "decoding": r.bool(),
            "rx_df": r.u32(),
            "tx_df": r.u32(),
            "de_call": r.qstring(),
            "de_grid": r.qstring(),
            "dx_grid": r.qstring(),
            "tx_watchdog": r.bool(),
            "sub_mode": r.qstring(),
            "fast_mode": r.bool(),
        }
        if r.pos < len(r.data):
            status["special_operation_mode"] = r.u8()
        if r.pos < len(r.data):
            status["frequency_tolerance"] = r.u32()
        if r.pos < len(r.data):
            status["tr_period"] = r.u32()
        if r.pos < len(r.data):
            status["configuration_name"] = r.qstring()
        if r.pos < len(r.data):
            status["tx_message"] = r.qstring()
        return status

    def _header(self, msg_type: int) -> Writer:
        w = Writer()
        w.u32(MAGIC)
        w.u32(self.schema)
        w.u32(msg_type)
        w.qstring(self.wsjtx_id)
        return w

    def send_halt_tx(self, auto_only: bool = False) -> bool:
        if not self.transport or not self.remote:
            return False
        w = self._header(8)
        w.u8(1 if auto_only else 0)
        self.transport.sendto(w.bytes(), self.remote)
        return True

    def send_reply(self, decode: Decode) -> bool:
        if not self.transport or not self.remote:
            return False
        w = self._header(4)
        w.u32(decode.time_ms)
        w.i32(decode.snr)
        w.f64(decode.delta_time)
        w.u32(decode.delta_frequency)
        w.qstring(decode.mode)
        w.qstring(decode.message)
        w.u8(1 if decode.low_confidence else 0)
        w.u8(0)
        self.transport.sendto(w.bytes(), self.remote)
        return True


GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$", re.IGNORECASE)
CALL_RE = re.compile(
    r"^(?=.{3,15}$)(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]+(?:/[A-Z0-9]+)?$",
    re.IGNORECASE,
)

CQ_MODIFIERS = {
    "DX", "TEST", "CONTEST", "POTA", "SOTA", "QRP", "NA", "EU", "AS", "AF",
    "SA", "OC", "JA", "CQDX", "FD", "WW", "ARRL", "WAE", "IOTA",
}


def _looks_like_call(token: str) -> bool:
    token = token.strip().upper()
    if not token or GRID_RE.fullmatch(token):
        return False
    return bool(CALL_RE.fullmatch(token))


def parse_cq(message: str) -> tuple[str, str]:
    """Parse common WSJT-X CQ/QRZ message layouts.

    Handles examples such as:
      CQ K1ABC FN42
      CQ DX K1ABC FN42
      CQ POTA K1ABC FN42
      CQ 123 K1ABC FN42
      QRZ K1ABC FN42
    """
    tokens = (message or "").strip().upper().split()
    if len(tokens) < 2 or tokens[0] not in {"CQ", "QRZ"}:
        return "", ""

    # Grid is normally the final token, but don't treat it as the callsign.
    grid = tokens[-1] if GRID_RE.fullmatch(tokens[-1]) else ""

    candidates = tokens[1:-1] if grid else tokens[1:]
    call = ""

    # Search from right to left because optional CQ modifiers precede the call.
    for token in reversed(candidates):
        if token in CQ_MODIFIERS:
            continue
        if token.isdigit():
            continue
        if _looks_like_call(token):
            call = token
            break

    return call, grid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
