"""Settings read from Aquaculture_Webapp/.env (see .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"

load_dotenv(PROJECT_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
GEMINI_READY = bool(GEMINI_API_KEY and GEMINI_MODEL)

DATA_SOURCE = os.getenv("DATA_SOURCE", "simulator").strip().lower()      # simulator | boat
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "").strip()
REPLAN_MODE = os.getenv("REPLAN_MODE", "gemini").strip().lower()         # gemini | onboard
# Web origins allowed to call the API from another host (comma-separated). Empty = same origin only.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

_model_dir = Path(os.getenv("MODEL_DIR", "../Aquaculture_Model"))
MODEL_DIR = _model_dir if _model_dir.is_absolute() else (PROJECT_DIR / _model_dir).resolve()

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "aquasentinel.db"))
LAKE_FILE = Path(os.getenv("LAKE_FILE", DATA_DIR / "lake.geojson"))
POND_TZ = os.getenv("POND_TIMEZONE", "Asia/Kolkata")

# The six parameters the boat reports. `turbidity` is the sensor added for the fish-health model.
PARAMS = ["ph", "do", "temp", "ec", "nitrate", "turbidity"]
PARAM_META = {
    "ph": {"label": "pH", "unit": "", "digits": 2},
    "do": {"label": "Dissolved oxygen", "unit": "mg/L", "digits": 2},
    "temp": {"label": "Temperature", "unit": "°C", "digits": 1},
    "ec": {"label": "Conductivity", "unit": "µS/cm", "digits": 0},
    "nitrate": {"label": "Nitrate", "unit": "mg/L", "digits": 1},
    "turbidity": {"label": "Turbidity", "unit": "NTU", "digits": 1},
}
TDS_PER_EC = 0.64        # TDS (ppm) ~ 0.64 x conductivity (µS/cm), the usual freshwater approximation
