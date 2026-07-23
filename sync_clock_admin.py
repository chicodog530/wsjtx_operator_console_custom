from __future__ import annotations

import ctypes
import json
import socket
import struct
import sys
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path

NTP_DELTA = 2_208_988_800

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", ctypes.c_ushort), ("wMonth", ctypes.c_ushort),
        ("wDayOfWeek", ctypes.c_ushort), ("wDay", ctypes.c_ushort),
        ("wHour", ctypes.c_ushort), ("wMinute", ctypes.c_ushort),
        ("wSecond", ctypes.c_ushort), ("wMilliseconds", ctypes.c_ushort),
    ]

def ntp_time(server: str, timeout: float = 5.0) -> tuple[float, float]:
    packet = b"\x1b" + 47 * b"\0"
    address = socket.gethostbyname(server)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    t1 = time.time()
    try:
        sock.sendto(packet, (address, 123))
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()
    if len(data) < 48:
        raise RuntimeError("Short NTP response")
    seconds, fraction = struct.unpack("!II", data[40:48])
    server_tx = seconds - NTP_DELTA + fraction / 2**32
    return server_tx + (t4 - t1) / 2, (t4 - t1) * 1000

def set_clock(timestamp: float) -> None:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    st = SYSTEMTIME(dt.year, dt.month, dt.weekday(), dt.day,
                    dt.hour, dt.minute, dt.second, dt.microsecond // 1000)
    ctypes.set_last_error(0)
    if not ctypes.windll.kernel32.SetSystemTime(ctypes.byref(st)):
        raise ctypes.WinError(ctypes.get_last_error())

def main() -> int:
    if len(sys.argv) != 3:
        return 2
    server, result_name = sys.argv[1], sys.argv[2]

    # Register the scheduled task to enable silent execution later
    try:
        task_name = "WSJTX_Console_TimeSync"
        python_exe = sys.executable
        if python_exe.lower().endswith("python.exe"):
            python_exe = python_exe[:-10] + "pythonw.exe"
        script_path = Path(__file__).resolve()
        # Need to properly escape the command string for schtasks
        command = f'"{python_exe}" "{script_path}" "{server}" "{result_name}"'
        subprocess.run(["schtasks", "/create", "/f", "/tn", task_name, "/tr", command, "/sc", "ONCE", "/st", "00:00", "/rl", "HIGHEST"], capture_output=True, creationflags=0x08000000)
    except Exception:
        pass

    result = Path(result_name)
    try:
        target, rtt = ntp_time(server)
        before = time.time()
        set_clock(target)
        payload = {"ok": True,
                   "message": f"Clock synchronized directly from {server}; adjustment {target-before:+.3f} s; RTT {rtt:.0f} ms"}
        code = 0
    except Exception as exc:
        payload = {"ok": False, "message": f"Clock synchronization failed: {exc}"}
        code = 1
    try:
        result.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return code

if __name__ == '__main__':
    raise SystemExit(main())
