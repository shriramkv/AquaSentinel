# Aquaculture Fish Health API

Classifies fish health as **Healthy / Stressed / Critical** from the ESP32 water-quality rig
(DS18B20 temperature, TDS, turbidity, pH) and serves predictions to a web app through FastAPI.

## Model

**LightGBM** on 80 engineered time-window features, chosen after a leave-one-pond-out comparison
(train on 4 ponds, test on the unseen 5th):

| Model | Macro-F1 | Critical recall | Critical precision |
|---|---|---|---|
| **LightGBM, engineered features (chosen)** | **0.823** | **0.806** | **0.794** |
| LightGBM + XGBoost ensemble | 0.824 | 0.809 | 0.799 |
| XGBoost, engineered features | 0.822 | 0.804 | 0.798 |
| Random Forest, engineered features | 0.812 | 0.764 | 0.789 |
| LightGBM, raw readings only | 0.755 | 0.754 | 0.695 |
| MLP neural net, raw 6 h sequence | 0.748 | 0.729 | 0.682 |

The ensemble adds only 0.001, so the single LightGBM model was kept: it is simpler, faster and explainable.
In production, Critical probability is up-weighted ×1.5 (`CRITICAL_WEIGHT`), which raises Critical recall
to **0.824** for a negligible drop in macro-F1 (0.821). Missing a Critical event costs fish; a false alarm
costs an inspection.

Final validation (production configuration):

| Class | Precision | Recall | F1 |
|---|---|---|---|
| Healthy | 0.958 | 0.973 | 0.965 |
| Stressed | 0.746 | 0.656 | 0.698 |
| Critical | 0.776 | 0.824 | 0.799 |

Healthy↔Critical confusions are rare (369 of ~130k rows); almost all errors are between neighbouring classes.

**Features:** rolling mean/std/min/max over 1 h, 6 h and 24 h; 1 h and 6 h changes; 6 h trends;
time of day; plus domain features: toxic-ammonia fraction from pH and temperature (Emerson), oxygen
saturation from temperature, temperature-compensated TDS, and daily pH swing × turbidity (algal-bloom
indicator, the strongest single feature).

**Cleaning before prediction:** DS18B20 `-127` errors, ADC rail values and out-of-range readings are
removed; single-sample spikes are filtered; gaps up to 30 min are forward-filled; any sampling rate is
resampled to 5 min (17-s ESP32 data gives the same predictions as 5-min data).

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# interactive docs: http://localhost:8000/docs
```

Docker:
```bash
docker build -t fish-health-api .
docker run -p 8000:8000 -e ALLOWED_ORIGINS=https://your-webapp.com fish-health-api
```

Tests: `pip install -r requirements-train.txt && pytest -q`

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_ORIGINS` | `*` | Comma-separated web-app origins for CORS. Set this in production. |
| `API_KEY` | unset | If set, every request needs header `X-API-Key: <key>` |
| `POND_TIMEZONE` | `Asia/Kolkata` | Timezone-aware timestamps (e.g. ThingSpeak UTC) are converted to this; naive ones are taken as local |
| `CRITICAL_WEIGHT` | `1.5` | Higher = catch more Critical events, more false alarms. `1.0` = unweighted |
| `MODEL_PATH` | `models/fish_health_model.joblib` | Model bundle |
| `MAX_PONDS` | `500` | Most ponds `/ingest` tracks at once; further new pond ids get HTTP 429 |
| `LOG_LEVEL` | `INFO` | `DEBUG` adds per-request detail (cleaning reports, buffer sizes, data warnings) |

### Logs

The API and the training scripts log to stderr in one format (`app/logging_config.py`):

```
2026-10-12 10:44:37,092 INFO    app.main | pond P5 @ 2026-03-17 05:40:00: Critical (confidence 1.00, reliable) from 288 reading(s), 24.0 h history, 0 data warning(s), 14 ms
2026-10-12 10:44:37,620 WARNING app.main | pond S1: status changed Healthy -> Critical at 2026-03-17 04:45:00 (confidence 0.53)
```

- `INFO`: model load and configuration at start-up, one line per prediction, ThingSpeak fetches.
- `WARNING`: a pond's status changing between `/ingest` readings, rejected API keys, ThingSpeak
  problems, suspicious input (e.g. a few readings spread over a very long time span), and an open
  `API_KEY` / `ALLOWED_ORIGINS` configuration at start-up.
- The ThingSpeak read key and the `API_KEY` value are never written to the logs.

## Endpoints

| Method | Path | Use |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/model-info` | Model, metrics, features |
| POST | `/predict` | Send recent readings → health now |
| POST | `/predict/series` | Same input → one prediction per 5 min (for charts) |
| GET | `/thingspeak/{channel_id}/predict` | API pulls the last 24 h from ThingSpeak and predicts |
| POST | `/ingest/{pond_id}` | Push one live reading; server keeps 26 h per pond |
| GET | `/ponds/{pond_id}/status` | Latest prediction from `/ingest` |
| DELETE | `/ponds/{pond_id}` | Clear a pond's live history |

Send **24 h** of history for best accuracy (minimum 1 h). With less, `reliable` is `false`.

### `POST /predict`

```json
{
  "pond_id": "P1",
  "readings": [
    {"timestamp": "2026-04-20T14:30:00", "temperature_c": 29.4, "tds_ppm": 512, "turbidity_ntu": 74.5, "ph": 8.9},
    {"timestamp": "2026-04-20T14:35:00", "temperature_c": 29.5, "tds_ppm": 510, "turbidity_ntu": 75.1, "ph": 8.9}
  ]
}
```

Response (abridged):
```json
{
  "pond_id": "P5",
  "timestamp": "2026-03-17T05:40:00",
  "health_status": "Critical",
  "confidence": 0.9973,
  "reliable": true,
  "probabilities": {"Healthy": 0.0, "Stressed": 0.0026, "Critical": 0.9973},
  "top_factors": [
    {"feature": "turbidity_x_ph_range", "description": "Turbidity x daily pH swing - bloom indicator",
     "value": 128.04, "impact": 0.849}
  ],
  "advice": ["Large daily pH swing - likely algal bloom. Expect low oxygen before dawn: run aerators at night ..."],
  "latest_readings": {"temperature_c": 26.1, "tds_ppm": 498.2, "turbidity_ntu": 61.6, "ph": 6.5},
  "data_quality": {"history_hours": 24.0, "missing_fraction_last_hour": 0.0,
                   "sensor_faults_removed": {}, "warnings": []},
  "model_version": "1.0.0"
}
```

`top_factors` are per-prediction TreeSHAP contributions toward the predicted class. `advice` is
rule-based guidance for operators and is not part of the ML model.

### Web app (JavaScript)

```js
const API = "http://localhost:8000";

// Option 1: your app already has readings
const res = await fetch(`${API}/predict`, {
  method: "POST",
  headers: { "Content-Type": "application/json" /*, "X-API-Key": "..." */ },
  body: JSON.stringify({ pond_id: "P1", readings })
});
const p = await res.json();
console.log(p.health_status, p.confidence, p.reliable ? "" : "(provisional)");

// Option 2: let the API read your ESP32's ThingSpeak channel
const r2 = await fetch(`${API}/thingspeak/2653720/predict?hours=24`);
// private channel: add &read_api_key=YOUR_READ_KEY  (the READ key, never the write key)
```

ThingSpeak field mapping follows the current firmware: field1 turbidity, field2 TDS, field3 pH,
field4 temperature.

### ESP32 pushing directly (optional)

`/ingest` needs a real timestamp, so sync time with NTP first. Sketch (not compiled here):

```cpp
#include <HTTPClient.h>
#include <time.h>
// in setup(): configTime(19800, 0, "pool.ntp.org");   // IST = UTC+5:30
// in loop(), after reading sensors:
time_t now; time(&now); char ts[25];
strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%S", localtime(&now));
String body = String("{\"timestamp\":\"") + ts + "\",\"temperature_c\":" + temperature +
              ",\"tds_ppm\":" + tdsValue + ",\"turbidity_ntu\":" + turbidity + ",\"ph\":" + pH + "}";
HTTPClient http;
http.begin("http://YOUR_SERVER:8000/ingest/P1");
http.addHeader("Content-Type", "application/json");
int code = http.POST(body);
http.end();
```

Live buffers are in memory, so run one worker; they reset on restart. For several workers or
durable history, move them to Redis or a database.

`/ingest` rejects a reading with HTTP 422 when its timestamp is more than 15 min ahead of the
server's clock (in `POND_TIMEZONE`) or more than 26 h older than the pond's latest reading, so a
device with a wrong clock cannot erase or distort the history. Timestamps with and without a
timezone can be mixed. Each pond keeps at most 20,000 readings.

## Retrain

```bash
python training/generate_aquaculture_dataset.py   # optional: regenerate simulated data (seed 42)
python training/benchmark.py                       # model comparison
python training/train_final.py                     # writes models/fish_health_model.joblib
```

## Limitations

- Trained on **simulated** data (5 ponds, 90 days, tilapia/carp ranges). Results show what the pipeline
  can learn, not field accuracy. Collect real pond data with observed fish condition and fine-tune before
  operational use.
- Dissolved oxygen and ammonia drive most real fish deaths but are not measured; the model infers risk
  from their indirect signatures. A DO sensor would be the most valuable hardware addition.
- The firmware's turbidity calibration cannot output below ~529 NTU with a 3.3 V ADC; recalibrate the
  sensor before real readings will match the training data.
