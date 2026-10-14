"""
Fish Health Prediction API (FastAPI)

Run:   uvicorn app.main:app --host 0.0.0.0 --port 8000
Docs:  http://localhost:8000/docs
Logs:  written to stderr; set LOG_LEVEL=DEBUG for per-request detail (see app/logging_config.py)

Endpoints
  GET  /health                  liveness + model loaded
  GET  /model-info              model type, classes, validation metrics, features
  POST /predict                 send recent readings for a pond -> health of the latest moment
  POST /predict/series          same input -> one prediction per 5-min step (for charts)
  POST /ingest/{pond_id}        send ONE live reading; the server keeps 26 h of history per pond
  GET  /ponds/{pond_id}/status  latest prediction for a pond fed through /ingest
  GET  /thingspeak/{channel_id}/predict   pull recent data from the ESP32's ThingSpeak channel and predict
  DELETE /ponds/{pond_id}       clear a pond's live history
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import joblib
import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from .features import MIN_HISTORY_STEPS, SENSORS, build_features
from .explain import describe_feature, advice_for
from .logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- configuration (environment)
MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).resolve().parents[1] / "models/fish_health_model.joblib"))
API_KEY = os.getenv("API_KEY")                                   # optional; set to require X-API-Key
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
POND_TZ = os.getenv("POND_TIMEZONE", "Asia/Kolkata")
BUFFER_HOURS = 26                # /ingest history kept per pond: 24 h for the daily features + 2 h margin
MAX_READINGS = 20_000            # request size cap: 24 h of 17-s ESP32 data is ~5,100 readings.
                                 # Also the most readings one /ingest buffer keeps (oldest are dropped first).
MAX_PONDS = int(os.getenv("MAX_PONDS", "500"))     # /ingest refuses new pond ids beyond this, to bound memory
MAX_CLOCK_AHEAD = timedelta(minutes=15)            # /ingest rejects timestamps further in the future than this

# ----------------------------------------------------------------------------- model bundle
# Loaded once at import time; the bundle is written by training/train_final.py.
try:
    bundle = joblib.load(MODEL_PATH)
except Exception:
    logger.exception("Could not load the model bundle from %s", MODEL_PATH)
    raise
MODELS = bundle["models"]                  # list of fitted classifiers; probabilities are averaged
FEATURES: list[str] = bundle["features"]
CLASSES: list[str] = bundle["classes"]
# The environment variable wins over the value stored with the model
CRITICAL_WEIGHT = float(os.getenv("CRITICAL_WEIGHT", bundle.get("critical_weight", 1.0)))

logger.info("Model loaded from %s: %s, version %s, trained %s, %d model(s), %d features, classes %s",
            MODEL_PATH, bundle.get("model_name"), bundle.get("version", "1.0.0"), bundle.get("trained_at"),
            len(MODELS), len(FEATURES), CLASSES)
logger.info("Config: critical_weight=%s, pond_timezone=%s, api_key=%s, allowed_origins=%s",
            CRITICAL_WEIGHT, POND_TZ, "required" if API_KEY else "not required", ALLOWED_ORIGINS)
if not API_KEY:
    logger.warning("API_KEY is not set - every endpoint is open to anyone who can reach this server")
if "*" in ALLOWED_ORIGINS:
    logger.warning("ALLOWED_ORIGINS is '*' - any website can call this API; set it to the web app's origin in production")
if not any(m.__class__.__name__.startswith("LGBM") for m in MODELS):
    logger.warning("No LightGBM model in the bundle - `top_factors` will be empty in every prediction")

app = FastAPI(
    title="Aquaculture Fish Health API",
    version=bundle.get("version", "1.0.0"),
    description="Classifies fish health (Healthy / Stressed / Critical) from ESP32 water-quality sensors: "
                "temperature, TDS, turbidity and pH.",
)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def check_key(x_api_key: Optional[str] = Header(default=None)):
    """Route dependency: when API_KEY is set, reject requests without a matching X-API-Key header."""
    if API_KEY and x_api_key != API_KEY:
        logger.warning("Rejected request: %s X-API-Key header", "wrong" if x_api_key else "missing")
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


# ----------------------------------------------------------------------------- schemas
class Reading(BaseModel):
    """One sensor sample. Any sensor may be omitted (null) when it failed to read."""
    timestamp: datetime = Field(..., examples=["2026-04-20T14:35:00"])
    temperature_c: Optional[float] = Field(None, examples=[29.4])
    tds_ppm: Optional[float] = Field(None, examples=[512.0])
    turbidity_ntu: Optional[float] = Field(None, examples=[74.5])
    ph: Optional[float] = Field(None, examples=[8.9])


class PredictRequest(BaseModel):
    """Body of /predict and /predict/series: a batch of readings for one pond."""
    pond_id: str = Field("default", examples=["P1"])
    readings: list[Reading] = Field(..., min_length=1, max_length=MAX_READINGS,
                                    description="Recent readings, any order. Send the last 24 h for best accuracy; "
                                                "at least 1 h is needed.")


class Factor(BaseModel):
    """One feature that pushed the prediction toward (positive) or away from (negative) the predicted class."""
    feature: str
    description: str
    value: Optional[float]
    impact: float = Field(..., description="Contribution to the predicted class score (log-odds); larger = stronger")


class DataQuality(BaseModel):
    """How much the input data can be trusted; drives the `reliable` flag."""
    history_hours: float
    missing_fraction_last_hour: float
    sensor_faults_removed: dict
    warnings: list[str]


class Prediction(BaseModel):
    """Health status of a pond at the time of its latest reading, with explanation and data-quality info."""
    pond_id: str
    timestamp: datetime
    health_status: str
    confidence: float
    reliable: bool = Field(..., description="False when there is under 1 h of history or the last hour is "
                                            "mostly missing - show the status as provisional")
    probabilities: dict[str, float]
    top_factors: list[Factor]
    advice: list[str]
    latest_readings: dict[str, Optional[float]]
    data_quality: DataQuality
    model_version: str


class SeriesPoint(BaseModel):
    """Prediction for one 5-minute step of /predict/series."""
    timestamp: datetime
    health_status: str
    probabilities: dict[str, float]


class SeriesResponse(BaseModel):
    pond_id: str
    points: list[SeriesPoint]
    model_version: str


# ----------------------------------------------------------------------------- core
def _local_naive(ts: datetime) -> pd.Timestamp:
    """Pond-local wall-clock time without a timezone.

    The model learned daily cycles in the pond's LOCAL time. Timezone-aware timestamps
    (e.g. ThingSpeak's UTC "Z") are converted to POND_TIMEZONE; naive ones are taken as local.
    """
    t = pd.Timestamp(ts)
    return t.tz_convert(POND_TZ).tz_localize(None) if t.tzinfo else t


def _readings_df(pond_id: str, readings: list[Reading]) -> pd.DataFrame:
    """Turn validated readings into the DataFrame that build_features() expects (naive local timestamps)."""
    df = pd.DataFrame([r.model_dump() for r in readings])
    df["timestamp"] = [_local_naive(r.timestamp) for r in readings]
    df["pond_id"] = pond_id
    if df[SENSORS].isna().all().all():
        logger.warning("pond %s: rejected %d reading(s) - every sensor value is empty", pond_id, len(df))
        raise HTTPException(status_code=422, detail="All sensor values are empty")
    logger.debug("pond %s: %d reading(s) received, %s .. %s", pond_id, len(df),
                 df["timestamp"].min(), df["timestamp"].max())
    return df


def _proba(X: np.ndarray) -> np.ndarray:
    """Average member probabilities, then up-weight Critical (missing it costs fish)."""
    P = np.mean([m.predict_proba(X) for m in MODELS], axis=0)
    P[:, CLASSES.index("Critical")] *= CRITICAL_WEIGHT
    return P / P.sum(axis=1, keepdims=True)                 # renormalise so each row sums to 1 again


def _explain(x_row: np.ndarray, cls: int, k: int = 5) -> list[Factor]:
    """Per-feature contributions from the LightGBM member (TreeSHAP)."""
    lgbm = next((m for m in MODELS if m.__class__.__name__.startswith("LGBM")), None)
    if lgbm is None:
        return []
    # pred_contrib returns, for every class, one value per feature plus a trailing bias term:
    # a flat array of n_classes * (n_features + 1). Slice out the block of the predicted class.
    contrib = lgbm.booster_.predict(x_row, pred_contrib=True)
    n = len(FEATURES) + 1
    c = np.asarray(contrib).reshape(-1)[cls * n: cls * n + len(FEATURES)]
    order = np.argsort(-np.abs(c))[:k]                      # k strongest, in either direction
    return [Factor(feature=FEATURES[i], description=describe_feature(FEATURES[i]),
                   value=None if np.isnan(x_row[0, i]) else round(float(x_row[0, i]), 4),
                   impact=round(float(c[i]), 4)) for i in order]


def predict_latest(pond_id: str, df: pd.DataFrame) -> Prediction:
    """Build features from `df` and classify the most recent 5-minute step.

    Shared by /predict, /ingest and /thingspeak. Besides the class it reports how far the
    result can be trusted (`reliable`, `data_quality`) and why the model decided so (`top_factors`).
    """
    t0 = time.perf_counter()
    feats, faults = build_features(df)
    last = feats.iloc[[-1]]                                 # latest 5-min step, kept as a 1-row frame
    X = last[FEATURES].to_numpy(dtype=float)
    p = _proba(X)[0]
    cls = int(p.argmax())

    # ---- data-quality checks: each one adds a warning the web app can show to the operator
    history_steps = int(len(feats))
    missing = float(last["missing_frac_1h"].iloc[0])
    warnings = []
    if history_steps < MIN_HISTORY_STEPS:
        warnings.append(f"Only {history_steps * 5} min of history; send at least 1 h (24 h recommended). "
                        "Prediction is low-confidence.")
    elif history_steps < 288:
        warnings.append(f"{history_steps * 5 / 60:.1f} h of history; daily-cycle features (24 h) are incomplete.")
    if missing > 0.3:
        warnings.append(f"{missing:.0%} of the last hour is missing (Wi-Fi dropouts or sensor faults).")
    removed = {s: v for s, v in faults[pond_id].items() if v["out_of_range"] or v["spikes"]}
    if removed:
        warnings.append("Some sensor faults/spikes were removed before prediction: " +
                        ", ".join(f"{s} ({v['out_of_range'] + v['spikes']})" for s, v in removed.items()))
    # A sensor is stale when it has no value even after the 30-min forward-fill
    stale = [s for s in SENSORS if np.isnan(last[s.split('_')[0]].iloc[0])]
    if stale:
        warnings.append(f"No recent valid value for: {', '.join(stale)}")

    latest = {s: (None if np.isnan(v) else round(float(v), 3))
              for s, v in zip(SENSORS, last[[s.split('_')[0] for s in SENSORS]].iloc[0])}
    reliable = history_steps >= MIN_HISTORY_STEPS and missing <= 0.3 and not stale
    pred = Prediction(
        pond_id=pond_id,
        timestamp=last["timestamp"].iloc[0].to_pydatetime(),
        health_status=CLASSES[cls],
        confidence=round(float(p[cls]), 4),
        reliable=reliable,
        probabilities={c: round(float(v), 4) for c, v in zip(CLASSES, p)},
        top_factors=_explain(X, cls),
        advice=advice_for(last.iloc[0], CLASSES[cls]),
        latest_readings=latest,
        data_quality=DataQuality(history_hours=round(history_steps * 5 / 60, 2),
                                 missing_fraction_last_hour=round(missing, 3),
                                 sensor_faults_removed=removed, warnings=warnings),
        model_version=app.version,
    )
    logger.info("pond %s @ %s: %s (confidence %.2f, %s) from %d reading(s), %.1f h history, "
                "%d data warning(s), %.0f ms",
                pond_id, pred.timestamp, pred.health_status, pred.confidence,
                "reliable" if reliable else "PROVISIONAL", len(df), history_steps * 5 / 60,
                len(warnings), (time.perf_counter() - t0) * 1000)
    for w in warnings:
        logger.debug("pond %s data warning: %s", pond_id, w)
    return pred


# ----------------------------------------------------------------------------- live buffers for /ingest
# In-process state: lost on restart and NOT shared between workers (run uvicorn with one worker).
_buffers: dict[str, deque] = {}            # pond_id -> readings of the last BUFFER_HOURS, oldest first
_newest: dict[str, datetime] = {}          # pond_id -> latest timestamp seen; the buffer window ends here
_last_pred: dict[str, Prediction] = {}     # pond_id -> most recent prediction, served by /ponds/{id}/status
_lock = threading.Lock()                   # FastAPI runs these sync routes in a thread pool


# ----------------------------------------------------------------------------- routes
@app.get("/health")
def health():
    """Liveness probe. No API key needed so load balancers and Docker can call it."""
    return {"status": "ok", "model_loaded": True, "model_version": app.version}


@app.get("/model-info", dependencies=[Depends(check_key)])
def model_info():
    """Static description of the model: metrics from training, feature list and expected input."""
    return {
        "model": bundle.get("model_name"),
        "version": app.version,
        "trained_at": bundle.get("trained_at"),
        "classes": CLASSES,
        "critical_weight": CRITICAL_WEIGHT,
        "validation": bundle.get("validation"),
        "top_features": bundle.get("top_features"),
        "n_features": len(FEATURES),
        "features": FEATURES,
        "input": {"sensors": SENSORS, "recommended_history_hours": 24, "minimum_history_hours": 1,
                  "resampled_to": "5 min"},
        "notes": bundle.get("notes"),
    }


@app.post("/predict", response_model=Prediction, dependencies=[Depends(check_key)])
def predict(req: PredictRequest):
    """Stateless prediction: the caller sends the recent history, nothing is stored."""
    return predict_latest(req.pond_id, _readings_df(req.pond_id, req.readings))


@app.post("/predict/series", response_model=SeriesResponse, dependencies=[Depends(check_key)])
def predict_series(req: PredictRequest):
    """One prediction per 5-minute step of the submitted history, for plotting a status timeline."""
    t0 = time.perf_counter()
    feats, _ = build_features(_readings_df(req.pond_id, req.readings))
    P = _proba(feats[FEATURES].to_numpy(dtype=float))
    pts = [SeriesPoint(timestamp=t.to_pydatetime(), health_status=CLASSES[int(p.argmax())],
                       probabilities={c: round(float(v), 4) for c, v in zip(CLASSES, p)})
           for t, p in zip(feats["timestamp"], P)]
    logger.info("pond %s series: %d reading(s) -> %d point(s) (%s), %.0f ms", req.pond_id, len(req.readings),
                len(pts), ", ".join(f"{c} {n}" for c, n in zip(CLASSES, np.bincount(P.argmax(1), minlength=len(CLASSES)))),
                (time.perf_counter() - t0) * 1000)
    return SeriesResponse(pond_id=req.pond_id, points=pts, model_version=app.version)


@app.post("/ingest/{pond_id}", response_model=Prediction, dependencies=[Depends(check_key)])
def ingest(pond_id: str, reading: Reading):
    """Append one live reading to the pond's buffer and predict from the buffered history.

    Returns 422 for a timestamp that is in the future or older than the buffer window, and 429
    when MAX_PONDS ponds are already being tracked.
    """
    # Store every reading as naive pond-local time, so timezone-aware and naive timestamps
    # from the same pond can be compared with each other
    ts = _local_naive(reading.timestamp).to_pydatetime()
    now = pd.Timestamp.now(tz=POND_TZ).tz_localize(None).to_pydatetime()
    # A device with a wrong clock must not move the buffer window: one far-future timestamp
    # would otherwise make all real history look too old and delete it
    if ts > now + MAX_CLOCK_AHEAD:
        logger.warning("pond %s: rejected reading dated %s - ahead of the server's pond time %s",
                       pond_id, ts, now.replace(microsecond=0))
        raise HTTPException(status_code=422, detail=(
            f"Timestamp {ts.isoformat()} is in the future (pond time on the server is "
            f"{now.replace(microsecond=0).isoformat()}, timezone {POND_TZ}). Check the device clock or POND_TIMEZONE."))
    reading = reading.model_copy(update={"timestamp": ts})

    with _lock:
        if pond_id not in _buffers:
            if len(_buffers) >= MAX_PONDS:
                logger.warning("pond %s: rejected - already tracking the maximum of %d ponds", pond_id, MAX_PONDS)
                raise HTTPException(status_code=429, detail=(
                    f"Already tracking {MAX_PONDS} ponds (MAX_PONDS). Clear unused ones with DELETE /ponds/{{pond_id}}."))
            logger.info("pond %s: first live reading - starting a new %d h buffer (%d pond(s) tracked)",
                        pond_id, BUFFER_HOURS, len(_buffers) + 1)
            # maxlen bounds memory even if timestamps never advance; the oldest reading is dropped first
            _buffers[pond_id] = deque(maxlen=MAX_READINGS)
        buf = _buffers[pond_id]
        # The window ends at the NEWEST timestamp seen, not at the reading that just arrived,
        # so late or out-of-order readings cannot shift it
        newest = max(_newest.get(pond_id, ts), ts)
        cutoff = newest - timedelta(hours=BUFFER_HOURS)
        if ts < cutoff:
            logger.warning("pond %s: rejected reading dated %s - older than the %d h window ending %s",
                           pond_id, ts, BUFFER_HOURS, newest)
            raise HTTPException(status_code=422, detail=(
                f"Timestamp {ts.isoformat()} is more than {BUFFER_HOURS} h older than the latest reading "
                f"for this pond ({newest.isoformat()})."))
        _newest[pond_id] = newest
        buf.append(reading)
        # Drop readings that have fallen out of the window. Only the left end is checked: a late
        # reading sits out of order and is removed once the ones before it have expired.
        dropped = 0
        while buf and buf[0].timestamp < cutoff:
            buf.popleft()
            dropped += 1
        snapshot = list(buf)                   # copy, so the slow prediction runs outside the lock
    logger.debug("pond %s: buffer holds %d reading(s) after dropping %d older than %s",
                 pond_id, len(snapshot), dropped, cutoff)
    pred = predict_latest(pond_id, _readings_df(pond_id, snapshot))
    with _lock:
        previous = _last_pred.get(pond_id)
        if pond_id in _buffers:                # skip if the pond was cleared while predicting
            _last_pred[pond_id] = pred
    # A change of status is the event an operator cares about, so it is logged above the per-reading noise
    if previous is not None and previous.health_status != pred.health_status:
        logger.warning("pond %s: status changed %s -> %s at %s (confidence %.2f)", pond_id,
                       previous.health_status, pred.health_status, pred.timestamp, pred.confidence)
    return pred


@app.get("/ponds/{pond_id}/status", response_model=Prediction, dependencies=[Depends(check_key)])
def pond_status(pond_id: str):
    """Last prediction made by /ingest for this pond (not recomputed, so it can be old)."""
    if pond_id not in _last_pred:
        logger.info("pond %s: status requested but no live data has been ingested", pond_id)
        raise HTTPException(status_code=404, detail=f"No live data ingested for pond '{pond_id}'")
    return _last_pred[pond_id]


# ThingSpeak channel field -> sensor column. Must match the order the ESP32 firmware writes the fields in.
THINGSPEAK_FIELDS = {"field1": "turbidity_ntu", "field2": "tds_ppm", "field3": "ph", "field4": "temperature_c"}


@app.get("/thingspeak/{channel_id}/predict", response_model=Prediction, dependencies=[Depends(check_key)])
def thingspeak_predict(channel_id: int, read_api_key: Optional[str] = None,
                       hours: float = Query(24, gt=0, le=720, description="History to pull, in hours (max 30 days)"),
                       pond_id: Optional[str] = None):
    """Pull the last `hours` of the ESP32's ThingSpeak channel and predict. Field mapping follows the
    firmware: field1 turbidity, field2 TDS, field3 pH, field4 temperature. Timestamps are UTC and are
    converted to POND_TIMEZONE. `read_api_key` is only needed for private channels."""
    params = {"minutes": int(hours * 60), "results": 8000}       # 8000 is ThingSpeak's per-request maximum
    if read_api_key:
        params["api_key"] = read_api_key
    # The read key is deliberately left out of every log line
    logger.info("ThingSpeak channel %d: fetching the last %s h (%s channel)", channel_id, hours,
                "private" if read_api_key else "public")
    t0 = time.perf_counter()
    try:
        r = httpx.get(f"https://api.thingspeak.com/channels/{channel_id}/feeds.json", params=params, timeout=20)
    except httpx.HTTPError as e:
        logger.error("ThingSpeak channel %d: request failed after %.1f s: %s", channel_id,
                     time.perf_counter() - t0, type(e).__name__)
        raise HTTPException(status_code=502, detail=f"Could not reach ThingSpeak: {e}")
    # ThingSpeak answers "-1" (sometimes with HTTP 200) for an unknown channel or a wrong read key
    if r.status_code != 200 or r.text.strip() == "-1":
        logger.warning("ThingSpeak channel %d: HTTP %s - wrong channel id or read key?", channel_id, r.status_code)
        raise HTTPException(status_code=502, detail=f"ThingSpeak returned {r.status_code}; check channel id / read key")
    try:
        payload = r.json()
    except ValueError:                         # e.g. an HTML maintenance page served with HTTP 200
        payload = None
    if not isinstance(payload, dict):
        logger.warning("ThingSpeak channel %d: response body is not the expected JSON object", channel_id)
        raise HTTPException(status_code=502, detail="ThingSpeak returned an unexpected response body")
    feeds = payload.get("feeds") or []
    logger.info("ThingSpeak channel %d: %d feed entr%s in %.1f s", channel_id, len(feeds),
                "y" if len(feeds) == 1 else "ies", time.perf_counter() - t0)
    if not feeds:
        raise HTTPException(status_code=404, detail="No ThingSpeak data in the requested window")
    if len(feeds) >= params["results"]:
        logger.warning("ThingSpeak channel %d: hit the %d-entry limit, so the oldest part of the requested "
                       "%s h window is missing", channel_id, params["results"], hours)

    def num(v):
        """ThingSpeak sends values as strings (or null when a field was not written)."""
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Entries without a usable timestamp are skipped instead of failing the whole request
    readings = []
    for f in feeds:
        try:
            readings.append(Reading(timestamp=f["created_at"],
                                    **{col: num(f.get(fld)) for fld, col in THINGSPEAK_FIELDS.items()}))
        except (ValidationError, KeyError, TypeError):
            continue
    if len(readings) < len(feeds):
        logger.warning("ThingSpeak channel %d: skipped %d of %d entries with a missing or invalid timestamp",
                       channel_id, len(feeds) - len(readings), len(feeds))
    if not readings:
        raise HTTPException(status_code=502, detail="ThingSpeak data could not be parsed: no entry has a valid timestamp")
    pid = pond_id or f"thingspeak-{channel_id}"
    return predict_latest(pid, _readings_df(pid, readings))


@app.delete("/ponds/{pond_id}", dependencies=[Depends(check_key)])
def clear_pond(pond_id: str):
    """Forget a pond's live buffer and last prediction (e.g. after moving or recalibrating the sensors)."""
    with _lock:
        buf = _buffers.pop(pond_id, None)
        _newest.pop(pond_id, None)
        _last_pred.pop(pond_id, None)
    logger.info("pond %s: live history cleared (%d buffered reading(s) removed)", pond_id, len(buf) if buf else 0)
    return {"cleared": pond_id}
