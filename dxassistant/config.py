from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    callsign: str = ""
    grid: str = ""
    udp_host: str = "0.0.0.0"
    udp_port: int = 2237
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    distance_unit: str = "mi"
    adif_path: str = ""
    auto_import_adif: bool = True
    psk_reporter_enabled: bool = True
    psk_reporter_interval_minutes: int = 5
    psk_timeframe_minutes: int = 60
    reply_max_age_minutes: int = 20
    notification_score: int = 110
    voice_score: int = 120
    map_tile_url: str = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    ntp_server: str = "time.google.com"
    ntp_check_minutes: int = 10
    time_warning_seconds: float = 1.0
    audio_device: int = -1
    audio_device_name: str = ""
    audio_sample_rate: int = 48000
    audio_fft_size: int = 4096
    audio_fps: int = 15
    qrz_api_key: str = ""
    qrz_auto_log: bool = False
    lotw_auto_log: bool = False
    lotw_tqsl_path: str = r"C:\Program Files (x86)\TrustedQSL\tqsl.exe"
    lotw_station_location: str = ""
    lotw_password: str = ""
    audio_auto_start: bool = False
    database_path: str = "dxassistant.db"
    last_adif_mtime: float = 0.0

class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.settings = Settings()
        self.load()

    def load(self) -> Settings:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            valid = {k: v for k, v in data.items() if hasattr(self.settings, k)}
            self.settings = Settings(**valid)
        return self.settings

    def save(self) -> None:
        self.path.write_text(
            json.dumps(asdict(self.settings), indent=2),
            encoding="utf-8",
        )
