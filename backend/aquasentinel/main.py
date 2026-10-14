"""AquaSentinel backend (FastAPI).

Run:  ./run.sh   (or: cd backend && .venv/bin/uvicorn aquasentinel.main:app --port 8080)
Docs: http://localhost:8080/docs
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Header, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, reports, runtime
from .hub import Hub, _short
from .simulator import SCENARIOS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
logger = logging.getLogger("aquasentinel")

hub = Hub()
runtime.hub = hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Data source: %s | Gemini: %s | re-plan: %s",
                "simulator" if hub.simulated else "real boat (POST /api/ingest)",
                f"model {config.GEMINI_MODEL}" if config.GEMINI_READY else "NOT configured (agents disabled)", hub.replan_mode)
    task = asyncio.create_task(hub.run())
    yield
    task.cancel()


app = FastAPI(title="AquaSentinel API", version="1.0.0", lifespan=lifespan)
if config.ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def _service():
    """The agent layer, or a clear 503 when Gemini is not configured."""
    if not config.GEMINI_READY:
        raise HTTPException(503, "Gemini is not configured. Set GEMINI_API_KEY and GEMINI_MODEL in Aquaculture_Webapp/.env and restart.")
    from .agents import service
    return service


def _need_sim():
    if not hub.simulated:
        raise HTTPException(409, "Only available with DATA_SOURCE=simulator")


async def _agent_call(coro):
    try:
        return await coro
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(504, "Gemini did not answer in time")
    except Exception as e:
        logger.exception("Agent call failed")
        raise HTTPException(502, f"Gemini call failed: {_short(e)}")


# ----------------------------------------------------------------------------- live
@app.get("/api/state")
def state():
    return hub.snapshot()


@app.websocket("/ws/live")
async def live(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()            # clients only listen; this keeps the socket open
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


# ----------------------------------------------------------------------------- real boat
class BoatReading(BaseModel):
    """One geo-tagged sample from the boat. Any sensor may be null when it failed to read."""
    timestamp: Optional[float] = Field(None, description="Unix seconds (UTC). Defaults to the server's time.")
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    ph: Optional[float] = Field(None, ge=0, le=14)
    do: Optional[float] = Field(None, ge=0, le=30, description="Dissolved oxygen, mg/L")
    temp: Optional[float] = Field(None, ge=-5, le=60, description="°C")
    ec: Optional[float] = Field(None, ge=0, le=100000, description="Conductivity, µS/cm")
    nitrate: Optional[float] = Field(None, ge=0, le=1000, description="mg/L")
    turbidity: Optional[float] = Field(None, ge=0, le=4000, description="NTU")


def _check_ingest_key(key: Optional[str]):
    if config.INGEST_API_KEY and key != config.INGEST_API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")


@app.post("/api/ingest")
async def ingest(reading: BoatReading, x_api_key: Optional[str] = Header(default=None)):
    """Endpoint the real boat posts to over 4G. Disabled while the simulator is the data source."""
    _check_ingest_key(x_api_key)
    if hub.simulated:
        raise HTTPException(409, "The simulator is the data source. Set DATA_SOURCE=boat in .env to accept real readings.")
    now = time.time()
    ts = reading.timestamp or now
    if ts > now + 900:
        raise HTTPException(422, "Timestamp is more than 15 minutes in the future; check the boat's clock")
    r = reading.model_dump(exclude={"timestamp"})
    if all(r[p] is None for p in config.PARAMS):
        raise HTTPException(422, "All sensor values are empty")
    out = await hub.process_reading({"ts": ts, "source": "boat", **r})
    return {"stored": out["id"], "zone": out["zone"], "flagged": bool(out["flagged"])}


@app.post("/api/camera/frame")
async def camera_frame(file: UploadFile = File(...), x_api_key: Optional[str] = Header(default=None)):
    """The real boat uploads its latest camera frame (JPEG) here."""
    _check_ingest_key(x_api_key)
    if hub.simulated:
        raise HTTPException(409, "The simulator is the data source")
    hub.live_frame = {"jpeg": await _read_image(file), "ts": time.time(), "simulated": False}
    await hub.broadcast({"type": "frame", "ts": hub.live_frame["ts"]})
    return {"ok": True}


@app.get("/api/boat/waypoints")
def boat_waypoints(x_api_key: Optional[str] = Header(default=None)):
    """The real boat polls this for its current route (latitude, longitude pairs)."""
    _check_ingest_key(x_api_key)
    m = hub.mission
    return {"mission_id": m and m["id"], "status": m and m["status"], "waypoints": m["waypoints"] if m else []}


async def _read_image(file: UploadFile) -> bytes:
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "Image is larger than 5 MB")
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGB")
        im.thumbnail((1280, 1280))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        raise HTTPException(415, "File is not a readable image")


# ----------------------------------------------------------------------------- camera
@app.get("/api/camera/latest.jpg")
def camera_latest():
    f = hub.live_frame
    if f is None and hub.simulated:
        jpeg, hub._frame_labels = hub.render_frame()
        f = hub.live_frame = {"jpeg": jpeg, "ts": time.time(), "simulated": True}
    if f is None:
        raise HTTPException(404, "No camera frame received yet")
    return Response(f["jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/anomalies/{aid}/frame/{slot}.jpg")
def anomaly_frame(aid: int, slot: str):
    jpeg = hub.frames.get(aid, {}).get(slot)
    if jpeg is None:
        raise HTTPException(404, "No frame stored for this anomaly")
    return Response(jpeg, media_type="image/jpeg")


@app.post("/api/camera/inspect")
async def camera_inspect(file: Optional[UploadFile] = File(default=None)):
    """Run the Visual Inspector on an uploaded photo, or on the latest boat frame when no file is sent."""
    service = _service()
    if file is not None:
        jpeg, simulated = await _read_image(file), False
    elif hub.live_frame:
        jpeg, simulated = hub.live_frame["jpeg"], hub.live_frame["simulated"]
    else:
        raise HTTPException(404, "No camera frame available")
    await hub.log("inspector", "Inspecting " + ("an uploaded photo" if file is not None else "the latest boat frame"),
                  code="inspect_start", uploaded=file is not None)
    out = await _agent_call(service.inspect_frame(jpeg, simulated, hub.language))
    await hub.log("inspector", out.get("summary", "Inspection complete"))
    return out


# ----------------------------------------------------------------------------- simulator controls
class ScenarioIn(BaseModel):
    kind: str
    place: Optional[str] = None
    hours_ago: float = Field(0, ge=0, le=24)


@app.post("/api/scenarios")
async def add_scenario(body: ScenarioIn):
    _need_sim()
    try:
        sc = hub.world.add_scenario(body.kind, body.place, body.hours_ago)
    except ValueError as e:
        raise HTTPException(422, str(e))
    hub.mark_zones_dirty()
    await hub.broadcast({"type": "scenarios", "scenarios": hub.world.describe_scenarios()})
    when = "starting now" if body.hours_ago == 0 else f"started {body.hours_ago:g} h ago"
    await hub.log("simulator", f"Scenario: {SCENARIOS[sc.kind]['label']} at {hub.lake.places[sc.place_id].name}, {when}",
                  code="scenario_started", kind=sc.kind, place_id=sc.place_id, place=hub.lake.places[sc.place_id].name, hours=body.hours_ago)
    return {"scenarios": hub.world.describe_scenarios()}


@app.delete("/api/scenarios")
async def clear_scenarios():
    _need_sim()
    hub.world.clear_scenarios()
    hub.mark_zones_dirty()
    await hub.broadcast({"type": "scenarios", "scenarios": []})
    await hub.log("simulator", "All scenarios cleared; the lake returns to its clean baseline", code="scenarios_cleared")
    return {"scenarios": []}


class BoatCommand(BaseModel):
    action: str                              # pause | resume | home | speed | replan_mode
    value: Optional[float | str] = None


@app.post("/api/boat/command")
async def boat_command(cmd: BoatCommand):
    _need_sim()
    b = hub.boat
    if cmd.action == "pause":
        b.pause()
    elif cmd.action == "resume":
        b.resume()
    elif cmd.action == "home":
        await hub.set_mission([b.home], "Return to dock", "Return to the dock", "operator", meta={"code": "dock"})
    elif cmd.action == "speed":
        try:
            b.speed_scale = max(1.0, min(16.0, float(cmd.value)))
        except (TypeError, ValueError):
            raise HTTPException(422, "speed needs a number between 1 and 16")
    elif cmd.action == "replan_mode":
        if cmd.value not in ("gemini", "onboard"):
            raise HTTPException(422, "replan_mode must be gemini or onboard")
        if cmd.value == "gemini" and not config.GEMINI_READY:
            raise HTTPException(503, "Gemini is not configured")
        hub.replan_mode = str(cmd.value)
        await hub.broadcast({"type": "config", "replan_mode": hub.replan_mode})
    else:
        raise HTTPException(422, f"Unknown action '{cmd.action}'")
    return {"boat": b.state(), "replan_mode": hub.replan_mode}


@app.post("/api/reset")
async def reset():
    """Clear all stored data and restart the demo."""
    await hub.reset()
    return {"ok": True}


# ----------------------------------------------------------------------------- language and voice
class SettingsIn(BaseModel):
    language: str = Field(..., pattern="^(en|ta|hi)$")


@app.post("/api/settings")
async def settings(body: SettingsIn):
    """Language the agents write in (diagnoses, planner replies, inspector notes). Set from the dashboard."""
    hub.language = body.language
    return {"language": hub.language}


AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/aac", "audio/x-m4a"}


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """Voice input for browsers without built-in speech recognition: Gemini turns a short recording into text."""
    service = _service()
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in AUDIO_TYPES:
        raise HTTPException(415, f"Unsupported audio type '{mime or 'unknown'}'")
    data = await file.read(8_000_001)
    if len(data) > 8_000_000:
        raise HTTPException(413, "Recording is larger than 8 MB; keep it under a minute")
    if len(data) < 800:
        raise HTTPException(422, "Recording is empty")
    return {"text": await _agent_call(service.transcribe(data, mime, hub.language))}


# ----------------------------------------------------------------------------- missions
class MissionIn(BaseModel):
    request: str = Field(..., min_length=3, max_length=500)


@app.post("/api/missions")
async def plan_mission(body: MissionIn):
    """Planner agent: plain-language request -> validated waypoint route."""
    service = _service()
    await hub.log("planner", f"Request: “{body.request}”", code="mission_request", request=body.request)
    res = await _agent_call(service.plan_mission(body.request, hub.language))
    await hub.log("planner", res["text"] or "Planning finished", tool_calls=res["tool_calls"])
    return res


class PresetIn(BaseModel):
    area: str = "whole_lake"
    spacing_m: float = Field(110, ge=40, le=400)


@app.post("/api/missions/preset")
async def preset_mission(body: PresetIn):
    """A transect survey without Gemini (works with no API key)."""
    zone = None if body.area == "whole_lake" else body.area.upper()
    if zone and zone not in hub.lake.zone_points:
        raise HTTPException(422, f"Unknown area '{body.area}'")
    pts = hub.lake.transects(body.spacing_m, zone)
    if not pts:
        raise HTTPException(422, "No navigable water for that area and spacing")
    where = "Whole-lake" if zone is None else f"Zone {zone}"
    m = await hub.set_mission(pts, f"{where} survey", f"{where} transects, {body.spacing_m:g} m apart", "preset",
                              meta={"code": "zone" if zone else "whole_lake", "zone": zone, "spacing_m": body.spacing_m})
    return {"mission": m}


@app.get("/api/missions")
def missions():
    return {"missions": hub.store.latest("missions", 20)}


# ----------------------------------------------------------------------------- anomalies and diagnoses
@app.get("/api/anomalies")
def anomalies():
    return {"anomalies": hub.store.latest("anomalies", 50), "diagnoses": hub.store.latest("diagnoses", 50),
            "frames": {aid: list(f) for aid, f in hub.frames.items()}}


@app.post("/api/anomalies/{aid}/diagnose")
async def diagnose(aid: int):
    _service()
    if hub.store.get("anomalies", aid) is None:
        raise HTTPException(404, "No such anomaly")
    return {"diagnosis": await _agent_call(hub.diagnose(aid, manual=True))}


# ----------------------------------------------------------------------------- zones
@app.get("/api/zones")
def zones():
    zh = hub.zone_health
    return {"updated": zh.updated, "zones": zh.latest, "model": zh.model_info, "error": zh.error, "simulated": hub.simulated}


@app.get("/api/zones/{zone}/history")
async def zone_history(zone: str):
    """24 h of inputs and the model's status for every 5-minute step of one zone."""
    zone = zone.upper()
    hist = hub.zone_histories()
    if zone not in hist:
        raise HTTPException(404, "No such zone")
    rows = hist[zone]
    series = await asyncio.to_thread(hub.zone_health.series, zone, rows) if rows else []
    step = max(1, len(rows) // 300)
    return {"zone": zone, "series": series,
            "inputs": [{**r, "timestamp": r["timestamp"].isoformat()} for r in rows[::step]]}


# ----------------------------------------------------------------------------- Ask the Lake, reports, alerts
class AskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=600)
    session_id: Optional[str] = Field(None, max_length=64)


@app.post("/api/ask")
async def ask(body: AskIn):
    service = _service()
    sid = body.session_id or uuid.uuid4().hex
    res = await _agent_call(service.ask(body.question, sid, hub.language))
    return {"session_id": sid, "answer": res["text"], "tool_calls": res["tool_calls"], "model": config.GEMINI_MODEL}


class ReportIn(BaseModel):
    hours: float = Field(1, gt=0, le=336)
    narrative: bool = True


@app.post("/api/reports")
async def create_report(body: ReportIn):
    """Data tables always come from storage. The Gemini narrative is optional."""
    narrative = None
    if body.narrative:
        service = _service()
        await hub.log("reporter", f"Writing the survey report narrative ({body.hours:g} h window)", code="report_writing", hours=body.hours)
        narrative = (await _agent_call(service.write_report(body.hours)))["text"]
    data = reports.collect(hub, body.hours)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"report-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.pdf"
    await asyncio.to_thread(reports.build_pdf, config.REPORTS_DIR / name, data, narrative, config.GEMINI_MODEL)
    rid = hub.store.insert("reports", ts=time.time(), title=f"{hub.lake.name}: last {body.hours:g} h", ai=int(bool(narrative)),
                           markdown=narrative, pdf=name)
    await hub.log("reporter", f"Report #{rid} ready" + ("" if narrative else " (data tables only, no Gemini narrative)"),
                  code="report_ready", report_id=rid, ai=bool(narrative))
    return {"report": hub.store.get("reports", rid)}


@app.get("/api/reports")
def list_reports():
    return {"reports": hub.store.latest("reports", 20)}


@app.get("/api/reports/{rid}/pdf")
def report_pdf(rid: int):
    row = hub.store.get("reports", rid)
    path = row and (config.REPORTS_DIR / row["pdf"]).resolve()
    if not row or config.REPORTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "Report not found")
    return FileResponse(path, media_type="application/pdf", filename=f"aquasentinel-report-{rid}.pdf")


class AlertIn(BaseModel):
    anomaly_id: int


@app.post("/api/alerts")
async def create_alert(body: AlertIn):
    service = _service()
    anomaly = hub.store.get("anomalies", body.anomaly_id)
    if anomaly is None:
        raise HTTPException(404, "No such anomaly")
    diag = next((d for d in hub.store.latest("diagnoses", 50) if d["anomaly_id"] == body.anomaly_id), None)
    if diag is None:
        raise HTTPException(409, "Diagnose this anomaly first; an alert is written from its diagnosis")
    await hub.log("reporter", f"Writing community alerts for anomaly #{body.anomaly_id}", code="alert_writing", anomaly_id=body.anomaly_id)
    out = await _agent_call(service.write_alerts(anomaly, diag["body"]))
    aid = hub.store.insert("alerts", ts=time.time(), anomaly_id=body.anomaly_id, body=out)
    return {"alert": hub.store.get("alerts", aid)}


@app.get("/api/alerts")
def list_alerts():
    return {"alerts": hub.store.latest("alerts", 20)}


# ----------------------------------------------------------------------------- benchmarks and agent status
@app.get("/api/benchmarks")
def benchmarks():
    return hub.benchmarks()


@app.post("/api/benchmarks/visual")
async def visual_benchmark():
    """Visual Inspector on 8 simulated frames whose contents are known. Costs 8 Gemini calls."""
    _need_sim()
    service = _service()
    from . import camera
    cases = [("sewage", 0.9, True), ("sewage", 0.5, False), ("industrial", 0.9, True), ("industrial", 0.6, False),
             ("algal_bloom", 0.9, False), ("farm_runoff", 0.8, False), (None, 0.0, False), (None, 0.0, False)]
    rows, correct, total = [], 0, 0
    for i, (kind, s, near) in enumerate(cases):
        jpeg, truth = camera.render(kind, s, near, seed=1000 + i)
        try:
            out = await _agent_call(service.inspect_frame(jpeg, True))
        except HTTPException as e:
            rows.append({"frame": i + 1, "scenario": kind or "clean water", "error": e.detail})
            continue
        said = {f["label"] for f in out.get("findings", []) if f.get("present")}
        hits = sum((lab in said) == (lab in truth) for lab in camera.VISUAL_LABELS)
        correct, total = correct + hits, total + len(camera.VISUAL_LABELS)
        rows.append({"frame": i + 1, "scenario": kind or "clean water", "drawn": truth, "reported": sorted(said),
                     "labels_correct": hits, "labels_total": len(camera.VISUAL_LABELS)})
    return {"frames": rows, "label_accuracy": round(correct / total, 3) if total else None, "model": config.GEMINI_MODEL,
            "note": "Frames are procedurally drawn stand-ins, so this measures the pipeline, not accuracy on real lake photos."}


@app.get("/api/agents/status")
def agents_status():
    return {"configured": config.GEMINI_READY, "model": config.GEMINI_MODEL or None,
            "key_present": bool(config.GEMINI_API_KEY), "replan_mode": hub.replan_mode}


@app.post("/api/agents/test")
async def agents_test():
    service = _service()
    return await _agent_call(service.self_test())


# ----------------------------------------------------------------------------- built frontend (production / Docker)
_dist = config.PROJECT_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
