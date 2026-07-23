from __future__ import annotations

import asyncio
import os
import socket
import struct
import subprocess
import sys
import json
import ctypes
from pathlib import Path
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


NTP_DELTA = 2_208_988_800


@dataclass(slots=True)
class TimeStatus:
    server: str = "time.google.com"
    drift_seconds: float | None = None
    round_trip_ms: float | None = None
    checked_at: str | None = None
    error: str = ""
    syncing: bool = False
    sync_message: str = "Not synchronized by this app yet"

    def public(self) -> dict:
        return asdict(self)


def _query_ntp_blocking(server: str, timeout: float = 3.0) -> tuple[float, float]:
    packet = b"\x1b" + 47 * b"\0"
    address = socket.gethostbyname(server)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(timeout)

    t1 = time.time()
    try:
        client.sendto(packet, (address, 123))
        data, _ = client.recvfrom(512)
        t4 = time.time()
    finally:
        client.close()

    if len(data) < 48:
        raise RuntimeError("Short NTP response")

    seconds, fraction = struct.unpack("!II", data[40:48])
    server_time = seconds - NTP_DELTA + fraction / 2**32
    # Half the round trip is a practical approximation of arrival delay.
    corrected_server_time = server_time + (t4 - t1) / 2
    drift = corrected_server_time - t4
    return drift, (t4 - t1) * 1000


async def query_ntp(server: str) -> tuple[float, float]:
    return await asyncio.to_thread(_query_ntp_blocking, server)


async def sync_windows_time() -> tuple[bool, str]:
    """Run a separate one-shot elevated helper; the main app remains normal."""
    if os.name != "nt":
        return False, "Windows time synchronization is only available on Windows"

    root = Path(__file__).resolve().parent.parent
    helper = root / "sync_clock_admin.py"
    result_file = root / "time_sync_result.json"

    if not helper.exists():
        return False, f"Clock helper is missing: {helper}"

    try:
        result_file.unlink(missing_ok=True)
    except OSError:
        pass

    # ShellExecute with the runas verb creates a UAC prompt for only the helper.
    params = subprocess.list2cmdline([str(helper), "time.google.com", str(result_file)])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, str(root), 0
    )
    if rc <= 32:
        if rc == 5:
            return False, "Administrator approval was canceled or denied"
        return False, f"Could not launch the clock helper (ShellExecute error {rc})"

    # Wait for the helper result without blocking WSJT-X UDP processing.
    for _ in range(150):
        await asyncio.sleep(0.2)
        if result_file.exists():
            try:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
                return bool(payload.get("ok")), str(payload.get("message", "Clock helper finished"))
            except Exception as exc:
                return False, f"Clock helper returned an unreadable result: {exc}"

    return False, "Clock helper timed out; the UAC prompt may still be open"
