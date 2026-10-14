"""Aquatic-life risk per lake zone, scored by the existing fish-health model.

The model lives in MODEL_DIR (../Aquaculture_Model). Its own `predict_latest()` is called
directly, so cleaning, the 80 engineered features, the Critical up-weighting, TreeSHAP factors
and the operator advice are exactly those of that service. Each zone is treated as one pond.

Inputs the model needs and where they come from here:
  temperature_c  boat temperature
  ph             boat pH
  tds_ppm        0.64 x boat conductivity (µS/cm)
  turbidity_ntu  turbidity sensor (the one addition to the boat's kit)
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config

logger = logging.getLogger(__name__)

MODEL_NOTE = ("LightGBM fish-health classifier trained on simulated pond data for tilapia and Indian major carps. "
              "It indicates risk to aquatic life from temperature, TDS, turbidity and pH patterns; it does not "
              "measure dissolved oxygen or ammonia and has not been validated on lake field data.")


class ZoneHealth:
    def __init__(self):
        self.error: str | None = None
        self.model_info: dict = {}
        self._lock = threading.Lock()
        self._rows: dict[str, deque] = {}          # zone -> readings from a real boat (boat mode)
        self._tz = ZoneInfo(config.POND_TZ)
        self.latest: dict[str, dict] = {}
        self.updated: float = 0.0
        try:
            if not (config.MODEL_DIR / "app" / "main.py").exists():
                raise FileNotFoundError(f"No fish-health model service found in {config.MODEL_DIR}")
            sys.path.insert(0, str(config.MODEL_DIR))
            # The model service warns at import about its own HTTP settings (API key, CORS). Its HTTP
            # layer is not used here, so those warnings would only mislead.
            logging.getLogger("app.main").setLevel(logging.ERROR)
            from app import main as model_api                   # loads the joblib bundle once
            self._api = model_api
            logging.getLogger("app.main").setLevel(logging.WARNING)      # one INFO line per prediction is too noisy here
            self.model_info = {"name": model_api.bundle.get("model_name"), "version": model_api.app.version,
                               "trained_at": model_api.bundle.get("trained_at"), "classes": model_api.CLASSES,
                               "n_features": len(model_api.FEATURES), "critical_weight": model_api.CRITICAL_WEIGHT,
                               "note": MODEL_NOTE}
        except Exception as e:                                  # the dashboard still works without the model
            logger.exception("Fish-health model could not be loaded")
            self._api = None
            self.error = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------ data from a real boat
    def add_boat_reading(self, zone: str, ts: float, temp, ec, turbidity, ph) -> None:
        row = {"timestamp": datetime.fromtimestamp(ts, self._tz).replace(tzinfo=None), "temperature_c": temp,
               "tds_ppm": None if ec is None else ec * config.TDS_PER_EC, "turbidity_ntu": turbidity, "ph": ph}
        with self._lock:
            buf = self._rows.setdefault(zone, deque(maxlen=20000))
            buf.append(row)
            cutoff = row["timestamp"] - timedelta(hours=26)
            while buf and buf[0]["timestamp"] < cutoff:
                buf.popleft()

    def boat_rows(self, zone: str) -> list[dict]:
        with self._lock:
            return list(self._rows.get(zone, ()))

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
        self.latest = {}

    # ------------------------------------------------------------------ prediction
    def _readings(self, rows: list[dict]):
        return [self._api.Reading(**r) for r in rows]

    def predict(self, zone: str, rows: list[dict]) -> dict:
        """Health of one zone from its history rows (timestamp, temperature_c, tds_ppm, turbidity_ntu, ph)."""
        if self._api is None:
            return {"zone": zone, "available": False, "reason": self.error}
        if not rows:
            return {"zone": zone, "available": False, "reason": "No readings in this zone yet"}
        try:
            pred = self._api.predict_latest(zone, self._api._readings_df(zone, self._readings(rows)))
        except Exception as e:
            detail = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
            return {"zone": zone, "available": False, "reason": str(detail)}
        out = pred.model_dump(mode="json")
        out.update(zone=zone, available=True)
        return out

    def series(self, zone: str, rows: list[dict]) -> list[dict]:
        """Status for every 5-minute step of the history (for the 24 h timeline)."""
        if self._api is None or not rows:
            return []
        req = self._api.PredictRequest(pond_id=zone, readings=self._readings(rows))
        res = self._api.predict_series(req)
        return [{"t": p.timestamp.isoformat(), "status": p.health_status, "p": p.probabilities} for p in res.points]

    def refresh(self, histories: dict[str, list[dict]]) -> dict[str, dict]:
        self.latest = {z: self.predict(z, rows) for z, rows in histories.items()}
        self.updated = time.time()
        return self.latest
