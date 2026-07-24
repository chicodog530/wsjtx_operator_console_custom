from __future__ import annotations

import asyncio
import math
from collections import Counter, deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adif import read_adif
from .config import SettingsStore
from .database import Database
from .dxcc import DxccDatabase
from .geo import distance_bearing, maidenhead_to_latlon
from .wsjtx import Decode, WsjtxProtocol, parse_cq, utc_now
from .psk_reporter import PskReporterClient
from .time_sync import TimeStatus, query_ntp, sync_windows_time


CONTINENT_NAMES = {
    "NA": "North America",
    "SA": "South America",
    "EU": "Europe",
    "AF": "Africa",
    "AS": "Asia",
    "OC": "Oceania",
    "AN": "Antarctica",
}


class DxAssistant:
    def __init__(self, root: Path, user_data_dir: Path | None = None) -> None:
        self.root = root
        self.user_data_dir = user_data_dir or root
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_store = SettingsStore(self.user_data_dir / "settings.json")
        self.settings = self.settings_store.settings
        self.db = Database(self.user_data_dir / self.settings.database_path)
        self.dxcc = DxccDatabase(root / "data" / "dxcc_prefixes.json")
        self.wsjtx: WsjtxProtocol | None = None
        self.wsjtx_transport: asyncio.DatagramTransport | None = None
        self.clients: set[Any] = set()
        self.qrz_session_count = 0
        self.lotw_session_count = 0
        self.status: dict[str, Any] = {
            "connected": False,
            "last_packet": None,
            "id": "WSJT-X",
            "version": "",
            "dial_frequency": 0,
            "mode": "",
            "band": "",
            "dx_call": "",
            "dx_grid": "",
            "de_call": self.settings.callsign,
            "de_grid": self.settings.grid,
            "report": "",
            "tx_enabled": False,
            "transmitting": False,
            "decoding": False,
            "rx_df": 0,
            "tx_df": 0,
            "tr_period": 15,
            "tx_message": "",
            "command_status": "Waiting for WSJT-X",
        }
        self.recent: deque[dict[str, Any]] = deque(maxlen=200)
        self.best_target: dict[str, Any] | None = None
        self.last_adif_mtime: float = 0
        self.propagation: deque[tuple[float, str]] = deque(maxlen=1000)
        self.psk_client = PskReporterClient()
        self.psk = {"reports": [], "last_refresh": None, "error": "", "refreshing": False}
        self.time_status = TimeStatus(server=self.settings.ntp_server)
        self._pending_broadcast = False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: WsjtxProtocol(self.handle_wsjtx),
            local_addr=(self.settings.udp_host, self.settings.udp_port),
            reuse_address=True
        )
        self.wsjtx_transport = transport
        self.wsjtx = protocol
        self.db.log_event("INFO", f"Listening for WSJT-X UDP on {self.settings.udp_port}")
        asyncio.create_task(self.monitor_adif())
        asyncio.create_task(self.monitor_time())
        if self.settings.psk_reporter_enabled:
            asyncio.create_task(self.monitor_psk_reporter())
        asyncio.create_task(self.broadcast_loop())
        asyncio.create_task(self.monitor_qrz_sync())
        asyncio.create_task(self.monitor_lotw_sync())

    async def stop(self) -> None:
        if self.wsjtx_transport:
            self.wsjtx_transport.close()

    async def handle_wsjtx(self, message: dict) -> None:
        msg_type = message.get("type")
        self.status["last_packet"] = utc_now()

        if msg_type == -1:
            self.db.log_event("ERROR", f"WSJT-X packet error: {message.get('error')}")
            return

        self.status["connected"] = True
        self.status["id"] = message.get("id", "WSJT-X")

        if msg_type == 0:
            self.status["version"] = message.get("version", "")
        elif msg_type == 1:
            self.update_status(message)
        elif msg_type == 2 and message.get("new"):
            await self.process_decode(message["decode"])
        elif msg_type == 3:
            self.recent.clear()
            self.best_target = None
            await self.broadcast()
        elif msg_type == 6:
            self.status["connected"] = False
            await self.broadcast()
        elif msg_type in (10, 12) and message.get("logged_adif"):
            from .adif import parse_adif
            records = list(parse_adif(message["logged_adif"]))
            if records:
                for record in records:
                    call = record.get("CALL", "").upper()
                    if not call: continue
                    entity = self.dxcc.lookup(call)
                    confirmed = any(record.get(f, "").upper() == "Y" for f in ("QSL_RCVD", "LOTW_QSL_RCVD", "EQSL_QSL_RCVD"))
                    self.db.execute(
                        "INSERT INTO qso(call, band, mode, grid, entity_id, confirmed, qso_date, time_on) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(call, band, mode, qso_date, time_on) DO UPDATE SET grid=excluded.grid, entity_id=excluded.entity_id, confirmed=MAX(qso.confirmed, excluded.confirmed)",
                        (call, record.get("BAND", ""), record.get("SUBMODE") or record.get("MODE", ""), record.get("GRIDSQUARE", ""), entity.id, int(confirmed), record.get("QSO_DATE", ""), record.get("TIME_ON", ""))
                    )
                    new_recent = [r for r in self.recent if r.get("call") != call]
                    self.recent.clear()
                    self.recent.extend(new_recent)
                    if self.best_target and self.best_target.get("call") == call:
                        self.best_target = None
                self.db.log_event("INFO", f"Instant logged {len(records)} ADIF records from UDP")
                await self.broadcast()

    def update_status(self, message: dict) -> None:
        for key in (
            "dial_frequency", "mode", "dx_call", "report", "tx_enabled",
            "transmitting", "decoding", "rx_df", "tx_df", "de_call",
            "de_grid", "dx_grid", "tr_period", "tx_message",
        ):
            if key in message:
                self.status[key] = message[key]
        self.status["band"] = self.frequency_to_band(self.status["dial_frequency"])
        asyncio.create_task(self.broadcast())

    async def process_decode(self, decode: Decode) -> None:
        call, grid = parse_cq(decode.message)
        if not call:
            return

        entity = self.dxcc.lookup(call)
        band = self.status.get("band", "")
        wanted = self.db.is_wanted(call)
        worked_call = self.db.worked_call(call)
        worked_entity = self.db.worked_entity(entity.id)
        confirmed_entity = self.db.confirmed_entity(entity.id)
        needed_on_band = entity.known and not self.db.entity_on_band(entity.id, band)
        distance, bearing = distance_bearing(
            self.settings.grid,
            grid,
            self.settings.distance_unit,
        )

        priority, reason = self.score_target(
            wanted=wanted,
            entity_known=entity.known,
            worked_call=worked_call,
            worked_entity=worked_entity,
            confirmed_entity=confirmed_entity,
            needed_on_band=needed_on_band,
            snr=decode.snr,
            distance=distance,
            entity_name=entity.name,
            band=band,
        )

        row = {
            "heard_at": utc_now(),
            "time_ms": decode.time_ms,
            "snr": decode.snr,
            "delta_time": decode.delta_time,
            "delta_frequency": decode.delta_frequency,
            "mode": decode.mode,
            "message": decode.message,
            "low_confidence": decode.low_confidence,
            "call": call,
            "grid": grid,
            "entity_id": entity.id,
            "entity_name": entity.name,
            "continent": entity.continent,
            "continent_name": CONTINENT_NAMES.get(entity.continent, ""),
            "cq_zone": entity.cq_zone,
            "itu_zone": entity.itu_zone,
            "flag": entity.flag,
            "distance": distance,
            "bearing": bearing,
            "priority": priority,
            "reason": reason,
            "wanted": wanted,
            "worked_call": worked_call,
            "worked_entity": worked_entity,
            "confirmed_entity": confirmed_entity,
            "needed_on_band": needed_on_band,
            "band": band,
            "latlon": maidenhead_to_latlon(grid),
            "_decode": decode,
            "_received_monotonic": asyncio.get_running_loop().time(),
        }

        self.recent.appendleft(row)
        if row["continent_name"]:
            self.propagation.append(
                (asyncio.get_running_loop().time(), row["continent_name"])
            )

        # Favor the highest current target, but allow a newly heard station with
        # the same score to replace an older recommendation.
        if self.best_target is None or priority >= self.best_target["priority"]:
            self.best_target = row

        self.db.execute(
            """INSERT INTO decodes(
                heard_at, wsjtx_time_ms, snr, delta_time, delta_frequency,
                mode, message, call, grid, entity_id, entity_name, continent,
                cq_zone, itu_zone, flag, distance, bearing, priority, reason,
                wanted, worked_call, worked_entity, confirmed_entity,
                needed_on_band, band
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["heard_at"], row["time_ms"], row["snr"], row["delta_time"],
                row["delta_frequency"], row["mode"], row["message"], row["call"],
                row["grid"], row["entity_id"], row["entity_name"], row["continent"],
                row["cq_zone"], row["itu_zone"], row["flag"], row["distance"],
                row["bearing"], row["priority"], row["reason"], int(row["wanted"]),
                int(row["worked_call"]), int(row["worked_entity"]),
                int(row["confirmed_entity"]), int(row["needed_on_band"]), row["band"],
            ),
        )
        await self.broadcast()

    @staticmethod
    def frequency_to_band(freq: int) -> str:
        mhz = freq / 1_000_000
        ranges = [
            (1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.0, 5.5, "60m"),
            (7.0, 7.3, "40m"), (10.1, 10.15, "30m"), (14.0, 14.35, "20m"),
            (18.068, 18.168, "17m"), (21.0, 21.45, "15m"),
            (24.89, 24.99, "12m"), (28.0, 29.7, "10m"),
            (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
        ]
        for lo, hi, band in ranges:
            if lo <= mhz <= hi:
                return band
        return ""

    @staticmethod
    def score_target(
        *,
        wanted: bool,
        entity_known: bool,
        worked_call: bool,
        worked_entity: bool,
        confirmed_entity: bool,
        needed_on_band: bool,
        snr: int,
        distance: float | None,
        entity_name: str,
        band: str,
    ) -> tuple[int, str]:
        score = max(0, 30 + snr)
        reasons: list[str] = []

        if wanted:
            score += 100
            reasons.append("Wanted target")
        if entity_known and not worked_entity:
            score += 80
            reasons.append(f"New DXCC: {entity_name}")
        elif entity_known and needed_on_band:
            score += 35
            reasons.append(f"{entity_name} needed on {band}")
        if entity_known and worked_entity and not confirmed_entity:
            score += 15
            reasons.append("Worked but unconfirmed")
        if not worked_call:
            score += 10
            reasons.append("New callsign")
        if distance:
            score += min(25, int(distance / 500))
        if snr >= -10:
            score += 8
            reasons.append("Strong copy")

        return score, " · ".join(reasons) or "Interesting CQ"

    @staticmethod
    def success_estimate(row: dict[str, Any]) -> int:
        snr = int(row.get("snr", -30))
        score = int(row.get("priority", 0))
        confidence = 45 + max(-15, min(25, snr + 18))
        confidence += min(20, score // 12)
        if row.get("wanted"):
            confidence += 5
        if row.get("low_confidence"):
            confidence -= 20
        return max(5, min(98, confidence))

    def public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        public = {key: value for key, value in row.items() if not key.startswith("_")}
        public["success_estimate"] = self.success_estimate(row)
        return public

    def propagation_summary(self) -> dict[str, Any]:
        now = asyncio.get_running_loop().time()
        while self.propagation and now - self.propagation[0][0] > 900:
            self.propagation.popleft()
        counts = Counter(region for _, region in self.propagation)
        opening = counts.most_common(1)[0][0] if counts else "No clear opening"
        return {"opening": opening, "counts": dict(counts)}

    @staticmethod
    def _band_from_hz(frequency: int | float) -> str:
        mhz = float(frequency or 0) / 1_000_000
        ranges = [
            (1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.0, 5.5, "60m"),
            (7.0, 7.3, "40m"), (10.1, 10.15, "30m"), (14.0, 14.35, "20m"),
            (18.068, 18.168, "17m"), (21.0, 21.45, "15m"),
            (24.89, 24.99, "12m"), (28.0, 29.7, "10m"),
            (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
        ]
        for lo, hi, band in ranges:
            if lo <= mhz <= hi:
                return band
        return ""

    def band_advisor(self) -> dict[str, Any]:
        current_band = self.status.get("band") or ""
        recent_15 = self.db.band_summary(15)
        recent_60 = self.db.band_summary(60)
        recent_180 = self.db.band_summary(180)

        by_band: dict[str, dict[str, Any]] = {}
        for window, rows in ((15, recent_15), (60, recent_60), (180, recent_180)):
            for row in rows:
                band = row.get("band") or "Unknown"
                item = by_band.setdefault(band, {
                    "band": band, "live": {}, "history": {}, "psk": {
                        "reports": 0, "receivers": 0, "avg_snr": None,
                        "max_distance": None,
                    }
                })
                if window == 15:
                    item["live"] = row
                else:
                    item["history"][str(window)] = row

        # PSK Reporter tells us where our own transmitted signal was heard.
        psk_groups: dict[str, list[dict[str, Any]]] = {}
        for report in self.psk.get("reports", []):
            band = self._band_from_hz(report.get("frequency", 0))
            if band:
                psk_groups.setdefault(band, []).append(report)

        home = maidenhead_to_latlon(self.settings.grid)
        for band, reports in psk_groups.items():
            item = by_band.setdefault(band, {
                "band": band, "live": {}, "history": {}, "psk": {
                    "reports": 0, "receivers": 0, "avg_snr": None,
                    "max_distance": None,
                }
            })
            distances = []
            for report in reports:
                grid = report.get("receiver_grid") or ""
                if home and grid:
                    target = maidenhead_to_latlon(grid)
                    if target:
                        d, _ = distance_bearing(
                            self.settings.grid, grid, self.settings.distance_unit
                        )
                        if d is not None:
                            distances.append(d)
            snrs = [float(r.get("snr", 0)) for r in reports if r.get("snr") is not None]
            item["psk"] = {
                "reports": len(reports),
                "receivers": len({r.get("receiver_call") for r in reports if r.get("receiver_call")}),
                "avg_snr": round(sum(snrs) / len(snrs), 1) if snrs else None,
                "max_distance": round(max(distances), 0) if distances else None,
            }

        ranked = []
        for band, item in by_band.items():
            if band == "Unknown":
                continue
            live = item["live"]
            h60 = item["history"].get("60", {})
            h180 = item["history"].get("180", {})
            psk = item["psk"]

            stations = int(live.get("stations", 0) or 0)
            entities = int(live.get("entities", 0) or 0)
            decodes = int(live.get("decodes", 0) or 0)
            avg_snr = live.get("avg_snr")
            avg_distance = live.get("avg_distance")
            max_distance = live.get("max_distance")
            psk_receivers = int(psk.get("receivers", 0) or 0)

            score = 0.0
            evidence = []
            confidence = "low"

            if stations:
                score += min(42, stations * 2.2)
                score += min(20, entities * 2.0)
                score += min(10, decodes / 8)
                if avg_snr is not None:
                    score += max(0, min(12, (float(avg_snr) + 24) * 0.75))
                if avg_distance:
                    score += min(12, float(avg_distance) / 650)
                confidence = "high"
                evidence.append(f"{stations} stations and {entities} entities decoded in 15 min")
                if avg_snr is not None:
                    evidence.append(f"Average signal {float(avg_snr):+.1f} dB")
                if max_distance:
                    evidence.append(f"Farthest decode {int(float(max_distance)):,} {self.settings.distance_unit}")
            else:
                old_stations = int(h60.get("stations", 0) or h180.get("stations", 0) or 0)
                if old_stations:
                    score += min(28, old_stations * 1.2)
                    evidence.append(f"{old_stations} stations heard in recent history")
                    confidence = "medium"

            if psk_receivers:
                score += min(30, psk_receivers * 2.0)
                if psk.get("max_distance"):
                    score += min(12, float(psk["max_distance"]) / 700)
                evidence.append(f"Your signal heard by {psk_receivers} PSK Reporter receivers")
                if psk.get("max_distance"):
                    evidence.append(f"PSK reach {int(float(psk['max_distance'])):,} {self.settings.distance_unit}")
                if confidence == "low":
                    confidence = "medium"

            # Slightly favor current-band evidence because it is truly live.
            if band == current_band and stations:
                score += 8

            ranked.append({
                **item,
                "score": round(min(100, score), 1),
                "stars": max(1, min(5, int(math.ceil(min(100, score) / 20)))),
                "confidence": confidence,
                "evidence": evidence or ["Not enough recent evidence"],
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        best = ranked[0] if ranked else None
        current = next((x for x in ranked if x["band"] == current_band), None)

        recommendation = "Insufficient data"
        summary = "Monitor more bands or transmit briefly so the advisor can compare activity."
        confidence = "low"
        action = "monitor"

        if best:
            confidence = best["confidence"]
            if current_band and best["band"] == current_band:
                recommendation = f"Stay on {current_band}"
                action = "stay"
                summary = (
                    f"{current_band} has the strongest combined live-decode and outbound-report evidence."
                )
            elif current_band and current:
                margin = best["score"] - current["score"]
                if margin >= 12:
                    recommendation = f"Consider switching to {best['band']}"
                    action = "switch"
                    summary = (
                        f"{best['band']} currently scores {margin:.0f} points better than {current_band}. "
                        "This is advisory only; alternate-band evidence may come from recent history or PSK Reporter."
                    )
                else:
                    recommendation = f"Stay on {current_band} for now"
                    action = "stay"
                    summary = (
                        f"{best['band']} is close, but the advantage is only {margin:.0f} points."
                    )
            else:
                recommendation = f"Try {best['band']}"
                action = "switch"
                summary = f"{best['band']} has the best available propagation evidence."

        return {
            "current_band": current_band,
            "recommendation": recommendation,
            "summary": summary,
            "confidence": confidence,
            "action": action,
            "best_band": best["band"] if best else "",
            "bands": ranked[:8],
            "limitations": (
                "Current-band scores use live WSJT-X decodes. Other-band scores use "
                "recent decode history and PSK Reporter reports of where your own signal was heard."
            ),
        }

    async def monitor_qrz_sync(self) -> None:
        import urllib.request
        import urllib.parse
        while True:
            await asyncio.sleep(10)
            if not self.settings.qrz_auto_log or not self.settings.qrz_api_key:
                continue
            
            unsynced = self.db.query("SELECT * FROM qso WHERE qrz_synced = 0 AND qso_date IS NOT NULL LIMIT 10")
            if not unsynced:
                continue
            
            for row in unsynced:
                call = row.get("call", "")
                if not call:
                    self.db.execute("UPDATE qso SET qrz_synced = 1 WHERE id = ?", (row["id"],))
                    continue
                
                adif = f"<call:{len(call)}>{call}"
                if row.get("band"):
                    band = row["band"]
                    adif += f"<band:{len(band)}>{band}"
                if row.get("mode"):
                    mode = row["mode"]
                    adif += f"<mode:{len(mode)}>{mode}"
                if row.get("qso_date"):
                    qdate = row["qso_date"].replace("-", "")
                    adif += f"<qso_date:{len(qdate)}>{qdate}"
                if row.get("time_on"):
                    ton = row["time_on"].replace(":", "")
                    adif += f"<time_on:{len(ton)}>{ton}"
                if row.get("grid"):
                    grid = row["grid"]
                    adif += f"<gridsquare:{len(grid)}>{grid}"
                adif += "<eor>"
                
                data = urllib.parse.urlencode({
                    "KEY": self.settings.qrz_api_key,
                    "ACTION": "INSERT",
                    "ADIF": adif
                }).encode("utf-8")
                
                req = urllib.request.Request("https://logbook.qrz.com/api", data=data)
                req.add_header("User-Agent", "WSJTX_Operator_Console/1.4.0")
                
                try:
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(None, urllib.request.urlopen, req, None, 10.0)
                    body = response.read().decode("utf-8")
                    if "RESULT=OK" in body.upper() or "RESULT=REPLACE" in body.upper():
                        self.db.execute("UPDATE qso SET qrz_synced = 1 WHERE id = ?", (row["id"],))
                        self.qrz_session_count += 1
                        self.db.log_event("INFO", f"QRZ.com sync successful for {call}")
                    else:
                        self.db.log_event("WARNING", f"QRZ upload failed for {call}: {body.strip()}")
                        self.db.execute("UPDATE qso SET qrz_synced = 1 WHERE id = ?", (row["id"],))
                except Exception as exc:
                    self.db.log_event("ERROR", f"QRZ connection error for {call}: {exc}")
                    await asyncio.sleep(5)
            
            await self.broadcast()

    async def monitor_lotw_sync(self) -> None:
        import asyncio.subprocess
        temp_file = self.user_data_dir / "temp_lotw.adi"
        while True:
            await asyncio.sleep(10)
            if not self.settings.lotw_auto_log or not self.settings.lotw_tqsl_path:
                continue
            
            unsynced = self.db.query("SELECT * FROM qso WHERE lotw_synced = 0 AND qso_date IS NOT NULL LIMIT 10")
            if not unsynced:
                continue
            
            # Construct a temporary ADIF
            adif_data = ""
            for row in unsynced:
                call = row.get("call", "")
                if not call:
                    self.db.execute("UPDATE qso SET lotw_synced = 1 WHERE id = ?", (row["id"],))
                    continue
                adif = f"<call:{len(call)}>{call}"
                if row.get("band"):
                    band = row["band"]
                    adif += f"<band:{len(band)}>{band}"
                if row.get("mode"):
                    mode = row["mode"]
                    adif += f"<mode:{len(mode)}>{mode}"
                if row.get("qso_date"):
                    qdate = row["qso_date"].replace("-", "")
                    adif += f"<qso_date:{len(qdate)}>{qdate}"
                if row.get("time_on"):
                    ton = row["time_on"].replace(":", "")
                    adif += f"<time_on:{len(ton)}>{ton}"
                if row.get("grid"):
                    grid = row["grid"]
                    adif += f"<gridsquare:{len(grid)}>{grid}"
                adif += "<eor>\n"
                adif_data += adif
            
            if not adif_data:
                continue
                
            try:
                temp_file.write_text(adif_data, encoding="utf-8")
                
                cmd = [self.settings.lotw_tqsl_path, "-x", "-d", "-u", "-a", "compliant"]
                if self.settings.lotw_station_location:
                    cmd.extend(["-l", self.settings.lotw_station_location])
                if self.settings.lotw_password:
                    cmd.extend(["-p", self.settings.lotw_password])
                cmd.append(str(temp_file))
                
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                
                if proc.returncode == 0:
                    for row in unsynced:
                        if row.get("call"):
                            self.db.execute("UPDATE qso SET lotw_synced = 1 WHERE id = ?", (row["id"],))
                    self.lotw_session_count += len(unsynced)
                    self.db.log_event("INFO", f"LoTW sync successful for {len(unsynced)} QSOs")
                else:
                    err = stderr.decode('utf-8', errors='ignore').strip() or stdout.decode('utf-8', errors='ignore').strip()
                    self.db.log_event("ERROR", f"LoTW sync failed: {err}")
                    # Mark as synced so we don't get stuck in a loop trying to upload malformed data forever?
                    # Or keep 0. Let's keep 0 but sleep longer on error.
                    await asyncio.sleep(60)
            except Exception as exc:
                self.db.log_event("ERROR", f"LoTW connection/execution error: {exc}")
                await asyncio.sleep(60)
            
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass
            
            await self.broadcast()

    def safe_band_advisor(self) -> dict[str, Any]:
        try:
            return self.band_advisor()
        except Exception as exc:
            self.db.log_event("ERROR", f"Band advisor error: {exc}")
            return {
                "current_band": self.status.get("band") or "",
                "recommendation": "Band Advisor temporarily unavailable",
                "summary": "The rest of WSJT-X Operator Console is still operating.",
                "confidence": "low",
                "action": "monitor",
                "best_band": "",
                "bands": [],
                "limitations": str(exc),
            }

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stats": self.db.stats(),
            "advisor": self.public_row(self.best_target) if self.best_target else None,
            "recent": [self.public_row(row) for row in list(self.recent)[:80]],
            "propagation": self.propagation_summary(),
            "wanted": self.db.wanted_patterns(),
            "awards": self.db.award_breakdown(),
            "top_entities": self.db.top_entities(20),
            "settings": {
                "callsign": self.settings.callsign,
                "grid": self.settings.grid,
                "distance_unit": self.settings.distance_unit,
                "adif_path": self.settings.adif_path,
                "notification_score": self.settings.notification_score,
                "voice_score": self.settings.voice_score,
                "map_tile_url": self.settings.map_tile_url,
                "ntp_server": self.settings.ntp_server,
                "time_warning_seconds": self.settings.time_warning_seconds,
                "qrz_api_key": self.settings.qrz_api_key,
                "qrz_auto_log": self.settings.qrz_auto_log,
                "lotw_auto_log": self.settings.lotw_auto_log,
                "lotw_tqsl_path": self.settings.lotw_tqsl_path,
                "lotw_station_location": self.settings.lotw_station_location,
                "lotw_password": self.settings.lotw_password,
            },
            "radar": self.db.radar(15),
            "band_summary": self.db.band_summary(15),
            "band_advisor": self.safe_band_advisor(),
            "time": self.time_status.public(),
            "health": self.station_health(),
            "psk_reporter": self.psk,
        }

    async def broadcast(self) -> None:
        self._pending_broadcast = True

    async def broadcast_loop(self) -> None:
        while True:
            if getattr(self, '_pending_broadcast', False):
                self._pending_broadcast = False
                await self._do_broadcast()
            await asyncio.sleep(0.25)

    async def _do_broadcast(self) -> None:
        if not self.clients:
            return
        dead = []
        payload = self.snapshot()
        for client in list(self.clients):
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.clients.discard(client)

    def call_target(self, call: str) -> tuple[bool, str]:
        call = (call or "").strip().upper()
        if not self.wsjtx or not call:
            return False, "No target is available"
        if self.status.get("transmitting"):
            return False, "WSJT-X is already transmitting"
        for row in self.recent:
            if row.get("call") != call:
                continue
            age = asyncio.get_running_loop().time() - row["_received_monotonic"]
            if age > self.settings.reply_max_age_minutes * 60:
                return False, "That decode is too old; wait for it to decode again"
            sent = self.wsjtx.send_reply(row["_decode"])
            message = f"Call request sent for {call}" if sent else "WSJT-X endpoint has not been learned yet"
            self.status["command_status"] = message
            return sent, message
        return False, f"{call} is no longer in the recent decode list"

    async def refresh_psk_reporter(self, minutes: int = 60) -> None:
        if self.psk.get("refreshing"):
            return
        self.psk["refreshing"] = True
        self.psk["error"] = ""
        try:
            reports = await self.psk_client.who_heard_me(self.settings.callsign, minutes)
            self.psk["reports"] = reports
            self.psk["last_refresh"] = utc_now()
        except Exception as exc:
            self.psk["error"] = str(exc)
        finally:
            self.psk["refreshing"] = False
            await self.broadcast()

    async def monitor_psk_reporter(self) -> None:
        await asyncio.sleep(2)
        while True:
            await self.refresh_psk_reporter(60)
            await asyncio.sleep(max(60, self.settings.psk_reporter_interval_minutes * 60))

    def call_best(self) -> tuple[bool, str]:
        if not self.wsjtx or not self.best_target:
            return False, "No Smart DX target is available"
        if self.status.get("transmitting"):
            return False, "WSJT-X is already transmitting"

        age = asyncio.get_running_loop().time() - self.best_target["_received_monotonic"]
        if age > self.settings.reply_max_age_minutes * 60:
            return False, "That decode is too old; wait for the station to decode again"

        sent = self.wsjtx.send_reply(self.best_target["_decode"])
        message = (
            f"Call request sent for {self.best_target['call']}"
            if sent else "WSJT-X endpoint has not been learned yet"
        )
        self.status["command_status"] = message
        return sent, message

    def halt_tx(self) -> tuple[bool, str]:
        sent = bool(self.wsjtx and self.wsjtx.send_halt_tx(False))
        message = "Halt TX sent" if sent else "WSJT-X endpoint has not been learned yet"
        self.status["command_status"] = message
        return sent, message

    def import_adif(self, path: Path) -> dict[str, int]:
        imported = 0
        confirmed_count = 0
        for record in read_adif(path):
            call = record.get("CALL", "").upper()
            if not call:
                continue
            entity = self.dxcc.lookup(call)
            confirmed = any(
                record.get(field, "").upper() == "Y"
                for field in ("QSL_RCVD", "LOTW_QSL_RCVD", "EQSL_QSL_RCVD")
            )
            self.db.execute(
                """INSERT INTO qso(
                    call, band, mode, grid, entity_id, confirmed, qso_date, time_on
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(call, band, mode, qso_date, time_on)
                DO UPDATE SET
                    grid=excluded.grid,
                    entity_id=excluded.entity_id,
                    confirmed=MAX(qso.confirmed, excluded.confirmed)""",
                (
                    call,
                    record.get("BAND", ""),
                    record.get("SUBMODE") or record.get("MODE", ""),
                    record.get("GRIDSQUARE", ""),
                    entity.id,
                    int(confirmed),
                    record.get("QSO_DATE", ""),
                    record.get("TIME_ON", ""),
                ),
            )
            imported += 1
            confirmed_count += int(confirmed)
        self.db.log_event("INFO", f"Imported {imported} ADIF records from {path}")
        return {"imported": imported, "confirmed": confirmed_count}

    async def check_time(self) -> dict[str, Any]:
        self.time_status.server = self.settings.ntp_server
        try:
            drift, round_trip = await query_ntp(self.settings.ntp_server)
            self.time_status.drift_seconds = drift
            self.time_status.round_trip_ms = round_trip
            self.time_status.checked_at = datetime.now(timezone.utc).isoformat()
            self.time_status.error = ""
        except Exception as exc:
            self.time_status.error = str(exc)
            self.time_status.checked_at = datetime.now(timezone.utc).isoformat()
        await self.broadcast()
        return self.time_status.public()

    async def synchronize_pc_time(self) -> tuple[bool, str]:
        self.time_status.syncing = True
        await self.broadcast()
        ok, message = await sync_windows_time()
        self.time_status.syncing = False
        self.time_status.sync_message = message
        if ok:
            await asyncio.sleep(1.5)
            await self.check_time()
        else:
            await self.broadcast()
        return ok, message

    async def monitor_time(self) -> None:
        await asyncio.sleep(2)
        while True:
            await self.check_time()
            await asyncio.sleep(max(1, self.settings.ntp_check_minutes) * 60)

    def station_health(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        drift = self.time_status.drift_seconds
        time_ok = drift is not None and abs(drift) <= self.settings.time_warning_seconds

        psk_last = self.psk.get("last_refresh")
        psk_age_minutes = None
        if psk_last:
            try:
                parsed = datetime.fromisoformat(str(psk_last).replace("Z", "+00:00"))
                psk_age_minutes = (now - parsed).total_seconds() / 60
            except ValueError:
                pass

        adif_path = Path(self.settings.adif_path) if self.settings.adif_path else None
        return [
            {
                "name": "WSJT-X",
                "ok": bool(self.status.get("connected")),
                "detail": "Connected" if self.status.get("connected") else "Waiting for UDP",
            },
            {
                "name": "PC clock",
                "ok": time_ok,
                "detail": (
                    f"{drift:+.3f} s drift" if drift is not None
                    else self.time_status.error or "Not checked"
                ),
            },
            {
                "name": "ADIF monitor",
                "ok": bool(adif_path and adif_path.exists()),
                "detail": (
                    str(adif_path) if adif_path and adif_path.exists()
                    else "Path not configured or missing"
                ),
            },
            {
                "name": "Logbook",
                "ok": (self.db.stats().get("qsos", 0) or 0) > 0,
                "detail": f"{self.db.stats().get('qsos', 0)} QSOs loaded",
            },
            {
                "name": "PSK Reporter",
                "ok": not self.psk.get("error") and psk_age_minutes is not None and psk_age_minutes < 15,
                "detail": (
                    f"Updated {psk_age_minutes:.0f} min ago" if psk_age_minutes is not None
                    else self.psk.get("error") or "Waiting for refresh"
                ),
            },
        ]

    async def monitor_adif(self) -> None:
        while True:
            await asyncio.sleep(5)
            configured = self.settings.adif_path.strip()
            if not self.settings.auto_import_adif or not configured:
                continue
            path = Path(configured)
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if mtime <= self.last_adif_mtime:
                continue
            self.last_adif_mtime = mtime
            self.import_adif(path)
            await self.broadcast()
