# AquaSentinel dashboard

Web dashboard and Gemini agent layer for the AquaSentinel autonomous boat: plan a survey in plain
language, watch live water telemetry on a map, detect pollution, trace the plume to its source,
diagnose the likely cause, and produce reports and local-language alerts.

## Run

```bash
./run.sh          # first run installs everything; then open http://localhost:5173
```

Needs `uv` and Node.js. Add your Gemini key to `.env` (created on first run) and restart:

```
GEMINI_API_KEY=...            # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-3.8-flash # every agent uses exactly this model
```

Without a key the dashboard still runs: simulator, map, charts, anomaly detection, plume tracing,
zone health and table-only PDF reports. The agent features say that Gemini is not configured;
nothing is faked in their place.

Tests: `cd backend && .venv/bin/python -m pytest -q`

## Languages, voice and screen sizes

- **Language**: pick English, தமிழ் or हिन्दी from the selector in the header. The choice is remembered in the
  browser and sent to the backend, so the agents (diagnosis, planner replies, inspector notes) write in it too.
  `?lang=ta` in the URL opens the app in a language directly. Strings live in `frontend/src/i18n.tsx`;
  to add a language, add one dictionary there and its name in `LANGUAGES` in `backend/aquasentinel/agents/service.py`.
- **Typing in any language**: mission requests and Ask the Lake questions go to Gemini as written.
  Ask the Lake answers in the language of the question.
- **Voice**: the microphone button beside the mission box and the Ask the Lake box. Chrome, Edge and Safari use
  the browser's speech recogniser in the selected language. Other browsers record a short clip that Gemini
  transcribes (`POST /api/transcribe`, needs the key). The microphone works only on https or localhost.
- **Still English**: the PDF report (the PDF library cannot shape Tamil or Devanagari), the fish-health model's
  factor names and advice (they come from `../Aquaculture_Model`), tool-call details and backend error messages.
  A stored diagnosis stays in the language it was written in; use "Diagnose again" after switching.
- **Phones and tablets**: one column with a bottom tab bar on phones, two columns from 1024 px. To open it on a
  device on the same Wi-Fi: `HOST=0.0.0.0 ./run.sh` (trusted networks only; there is no login).

The Tamil and Hindi interface text was machine-written. Have a native speaker review it before public use.

## What is where

| Path | Purpose |
|---|---|
| `backend/aquasentinel/main.py` | FastAPI routes and the `/ws/live` WebSocket |
| `backend/aquasentinel/hub.py` | The Survey → Detect → Re-plan → Diagnose loop |
| `backend/aquasentinel/simulator.py` | Lake, pollution scenarios, boat, plume-tracing controller |
| `backend/aquasentinel/anomaly.py` | Robust z-score detector with CPCB limit checks |
| `backend/aquasentinel/zone_health.py` | Calls the fish-health model in `../Aquaculture_Model` |
| `backend/aquasentinel/agents/` | ADK agents (`service.py`) and their tools (`tools.py`) |
| `backend/aquasentinel/reports.py` | PDF report |
| `backend/data/lake.geojson` | Lake outline, inlets, outfalls, dock (demo outline; replace with a real one) |
| `backend/data/cpcb_criteria.json` | CPCB Designated Best Use classes used for grounding |
| `frontend/src/` | React dashboard (Vite, TypeScript, Tailwind, Leaflet) |

## Agents

Built with Google's Agent Development Kit. All use `GEMINI_MODEL` from `.env`.

| Agent | Job | Can call |
|---|---|---|
| Planner | Request → waypoint route; decides how to react to an anomaly | lake map, transect builder, `push_waypoints`, `start_plume_trace` |
| Visual inspector | Camera frame → bloom, foam, oil sheen, waste, outfall | image only |
| Diagnostician | Sensors + image + CPCB + zone health → probable cause with evidence | stats, readings, baseline, CPCB, zone health |
| Reporter | Ask the Lake answers, report narrative, alerts in English, Tamil, Hindi | stats, period comparison, anomalies, diagnoses, CPCB, zone health |

Safeguards: routes are rejected unless every waypoint and leg is on navigable water; data tools are
read-only with fixed query shapes; the simulator's hidden ground truth is never returned by a tool;
automatic diagnoses have a 45 s cooldown.

## The fish-health model

`zone_health.py` imports `predict_latest()` from `../Aquaculture_Model/app/main.py` unchanged and
treats each of the six lake zones as one pond. Inputs: boat temperature and pH, TDS as
0.64 × conductivity, and a turbidity sensor (the one addition to the boat's kit).

Limits worth knowing before a demo:
- The model was trained on simulated pond data and has no dissolved-oxygen or nitrate input. It
  reacts to industrial discharge and algal blooms; sewage and farm runoff mostly leave it at Healthy.
- It needs hours of sustained change. Start a scenario with "Discharge began 6 h ago" (or more) to see it react.
- In the simulator, each zone's 24 h history comes from a virtual fixed station at the zone's most
  exposed point, because one moving boat cannot supply 24 h at every point.

## Connecting the real boat

Set `DATA_SOURCE=boat` in `.env` (and `INGEST_API_KEY`), then have the boat:

```
POST /api/ingest          {"lat":..,"lon":..,"ph":..,"do":..,"temp":..,"ec":..,"nitrate":..,"turbidity":..}
POST /api/camera/frame    multipart JPEG
GET  /api/boat/waypoints  current route to follow
```
with header `X-API-Key`. Plume tracing by gradient runs only in the simulator's onboard controller;
for the real boat the Planner pushes waypoints, and the autopilot must implement the trace itself.

## Deploying

One container serves both API and dashboard (build from the workspace root so the model is included):

```bash
docker build -f Aquaculture_Webapp/Dockerfile -t aquasentinel .
```

Deploy to Cloud Run with a single instance (`--max-instances 1`): live state and SQLite are in
process memory and on local disk. Firebase Hosting can serve `frontend/dist` instead, built with
`VITE_BACKEND_URL=<Cloud Run URL>` and with `ALLOWED_ORIGINS` set on the backend; Hosting rewrites
do not carry WebSockets, so the dashboard connects to Cloud Run directly.

Not built yet from the deck's architecture: Pub/Sub, BigQuery, Firestore, Firebase Auth, and
sending alerts by FCM or SMS. There is no login; `run.sh` binds to localhost for that reason.
Add authentication before exposing the control endpoints on the internet.

## What is simulated

Readings, camera frames and scenario signatures come from the simulator and are labelled as such in
the dashboard and in every PDF. The Benchmarks page measures the pipeline against the simulator's
hidden sources; those are not field-trial results.
