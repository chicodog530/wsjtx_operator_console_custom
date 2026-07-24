from __future__ import annotations

import asyncio
import logging
import os
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dxassistant.core import DxAssistant
from dxassistant.audio_waterfall import AudioWaterfall

ROOT = Path(__file__).resolve().parent

def user_data_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "WSJTX-Operator-Console"
    return Path.home() / ".local" / "share" / "WSJTX-Operator-Console"

USER_DATA = user_data_directory()
USER_DATA.mkdir(parents=True, exist_ok=True)
(USER_DATA / "logs").mkdir(exist_ok=True)

# Preserve settings and logbook for users upgrading from the pre-public builds.
if os.name == "nt":
    legacy = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "KE0CGB-RCC"
else:
    legacy = Path.home() / ".local" / "share" / "KE0CGB-RCC"
if legacy.exists():
    for name in ("settings.json", "dxassistant.db"):
        old = legacy / name
        new = USER_DATA / name
        if old.exists() and not new.exists():
            try:
                shutil.copy2(old, new)
            except OSError:
                pass

# One-time migration from the old portable-folder layout.
for name in ("settings.json", "dxassistant.db"):
    old = ROOT / name
    new = USER_DATA / name
    if old.exists() and not new.exists():
        try:
            shutil.copy2(old, new)
        except OSError:
            pass

logger = logging.getLogger("wsjtx_operator_console")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(
        USER_DATA / "logs" / "command_center.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

assistant = DxAssistant(ROOT, USER_DATA)
audio = AudioWaterfall()

app = FastAPI(title="WSJT-X Operator Console", version="1.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.on_event("startup")
async def startup() -> None:
    await assistant.start()
    if assistant.settings.audio_auto_start:
        device = None if assistant.settings.audio_device < 0 else assistant.settings.audio_device
        ok, message = audio.start(
            device,
            assistant.settings.audio_sample_rate,
            assistant.settings.audio_fft_size,
            assistant.settings.audio_fps,
            assistant.settings.audio_device_name,
        )
        logger.info("Audio auto-start: %s", message)


@app.on_event("shutdown")
async def shutdown() -> None:
    audio.stop()
    await assistant.stop()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})




@app.get("/changelog")
async def changelog():
    return FileResponse(ROOT / "CHANGELOG.md", media_type="text/markdown; charset=utf-8")


@app.get("/technical-description")
async def technical_description():
    return FileResponse(ROOT / "TECHNICAL_DESCRIPTION.md", media_type="text/markdown; charset=utf-8")

@app.get("/api/status")
async def status():
    return assistant.snapshot()


@app.post("/api/call/{call}")
async def call_target(call: str):
    ok, message = assistant.call_target(call)
    if not ok:
        raise HTTPException(409, message)
    await assistant.broadcast()
    return PlainTextResponse(message)


@app.post("/api/call-best")
async def call_best():
    ok, message = assistant.call_best()
    if not ok:
        raise HTTPException(409, message)
    await assistant.broadcast()
    return PlainTextResponse(message)


@app.post("/api/halt-tx")
async def halt_tx():
    ok, message = assistant.halt_tx()
    if not ok:
        raise HTTPException(409, message)
    await assistant.broadcast()
    return PlainTextResponse(message)


@app.post("/api/wanted")
async def add_wanted(
    pattern: str = Form(...),
    kind: str = Form("call"),
    note: str = Form(""),
):
    pattern = pattern.strip().upper()
    if not pattern:
        raise HTTPException(400, "Pattern is required")
    if kind not in {"call", "prefix"}:
        raise HTTPException(400, "Kind must be call or prefix")
    assistant.db.execute(
        "INSERT OR REPLACE INTO wanted(pattern, kind, note) VALUES (?,?,?)",
        (pattern, kind, note.strip()),
    )
    await assistant.broadcast()
    return {"ok": True}


@app.delete("/api/wanted/{pattern}")
async def delete_wanted(pattern: str):
    assistant.db.execute("DELETE FROM wanted WHERE pattern=?", (pattern.upper(),))
    await assistant.broadcast()
    return {"ok": True}


@app.post("/api/import-adif")
async def import_adif(file: UploadFile = File(...)):
    data = await file.read()
    temp = USER_DATA / "_uploaded_log.adi"
    temp.write_bytes(data)
    result = assistant.import_adif(temp)
    await assistant.broadcast()
    return result


@app.get("/api/history")
async def history(
    limit: int = 500,
    query: str = "",
    band: str = "",
    entity: str = "",
):
    return assistant.db.searchable_history(
        limit=limit,
        query=query.strip(),
        band=band.strip(),
        entity=entity.strip(),
    )


@app.get("/api/radar")
async def radar(minutes: int = 15):
    return {
        "radar": assistant.db.radar(minutes),
        "bands": assistant.db.band_summary(minutes),
        "history": assistant.db.propagation_history(24),
    }


@app.get("/api/entity/{entity_id}")
async def entity_profile(entity_id: int):
    return assistant.db.entity_profile(entity_id)


@app.get("/api/events")
async def events(limit: int = 100):
    return assistant.db.query(
        "SELECT * FROM app_events ORDER BY id DESC LIMIT ?",
        (min(max(limit, 1), 500),),
    )



@app.get("/api/analytics")
async def analytics():
    return {
        "awards": assistant.db.award_breakdown(),
        "top_entities": assistant.db.top_entities(50),
        "stats": assistant.db.stats(),
    }


@app.post("/api/settings")
async def save_settings(
    callsign: str = Form(...),
    grid: str = Form(...),
    distance_unit: str = Form("mi"),
    adif_path: str = Form(""),
    notification_score: int = Form(110),
    voice_score: int = Form(120),
    ntp_server: str = Form("time.google.com"),
    time_warning_seconds: float = Form(1.0),
    qrz_api_key: str = Form(""),
    qrz_auto_log: bool = Form(False),
    lotw_auto_log: bool = Form(False),
    lotw_tqsl_path: str = Form(""),
    lotw_station_location: str = Form(""),
    lotw_password: str = Form(""),
):
    assistant.settings.callsign = callsign.strip().upper()
    assistant.settings.grid = grid.strip().upper()
    assistant.settings.distance_unit = distance_unit
    assistant.settings.adif_path = adif_path.strip()
    assistant.settings.notification_score = max(0, min(notification_score, 300))
    assistant.settings.voice_score = max(0, min(voice_score, 300))
    assistant.settings.ntp_server = ntp_server.strip() or "time.google.com"
    assistant.settings.time_warning_seconds = max(0.1, min(time_warning_seconds, 5.0))
    assistant.settings.qrz_api_key = qrz_api_key.strip()
    assistant.settings.qrz_auto_log = qrz_auto_log
    assistant.settings.lotw_auto_log = lotw_auto_log
    assistant.settings.lotw_tqsl_path = lotw_tqsl_path.strip()
    assistant.settings.lotw_station_location = lotw_station_location.strip()
    assistant.settings.lotw_password = lotw_password.strip()
    assistant.time_status.server = assistant.settings.ntp_server
    assistant.settings_store.save()
    assistant.status["de_call"] = assistant.settings.callsign
    assistant.status["de_grid"] = assistant.settings.grid
    await assistant.broadcast()
    return {"ok": True}


@app.post("/api/settings/verify_tqsl")
async def verify_tqsl(path: str = Form("")):
    target = Path(path.strip() or r"C:\Program Files (x86)\TrustedQSL\tqsl.exe")
    if target.exists() and target.is_file():
        return {"ok": True, "message": "TQSL executable found!"}
    return {"ok": False, "message": "TQSL executable not found at that location."}


@app.post("/api/psk-refresh")
async def psk_refresh(minutes: int = 60):
    await assistant.refresh_psk_reporter(minutes)
    return assistant.psk


@app.post("/api/time/check")
async def check_time():
    return await assistant.check_time()


@app.post("/api/time/sync")
async def synchronize_time():
    ok, message = await assistant.synchronize_pc_time()
    if not ok:
        raise HTTPException(409, message)
    return PlainTextResponse(message)



@app.get("/api/audio/devices")
async def audio_devices():
    return {"devices": audio.devices(), "status": audio.status.public()}


@app.get("/api/audio/status")
async def audio_status():
    return audio.status.public()


@app.post("/api/audio/start")
async def audio_start(
    device: int = Form(-1),
    sample_rate: int = Form(48000),
    fft_size: int = Form(4096),
    fps: int = Form(15),
):
    selected = None if device < 0 else device
    requested_name = ""
    try:
        requested_name = next((d["name"] for d in audio.devices() if d["id"] == device), "")
    except Exception:
        requested_name = ""
    ok, message = audio.start(selected, sample_rate, fft_size, fps, requested_name)
    if not ok:
        raise HTTPException(409, message)
    assistant.settings.audio_device = audio.status.device if audio.status.device is not None else device
    assistant.settings.audio_device_name = audio.status.device_name or requested_name
    assistant.settings.audio_sample_rate = audio.status.sample_rate
    assistant.settings.audio_fft_size = fft_size
    assistant.settings.audio_fps = fps
    assistant.settings_store.save()
    return {"ok": True, "message": message, "status": audio.status.public()}


@app.post("/api/audio/stop")
async def audio_stop():
    audio.stop()
    return {"ok": True, "status": audio.status.public()}


@app.websocket("/ws/waterfall")
async def waterfall_socket(websocket: WebSocket):
    await websocket.accept()
    last = -1
    try:
        while True:
            frame = audio.latest()
            binary = audio.latest_binary()
            if frame and binary and frame.get("sequence") != last:
                last = frame["sequence"]
                await websocket.send_bytes(binary)
            else:
                await asyncio.sleep(0.04)
    except WebSocketDisconnect:
        pass

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    assistant.clients.add(websocket)
    await websocket.send_json(assistant.snapshot())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        assistant.clients.discard(websocket)
