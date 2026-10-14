"""Run: pytest -q   (from the project root)

End-to-end tests of the API against the trained model in models/, using windows cut from the
simulated training dataset. Nothing is sent over the network: ThingSpeak is mocked.
Add `-o log_cli=true -o log_cli_level=INFO` to see the API's log lines while the tests run.
"""
import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)        # importing app.main loads the model bundle
DATA = pd.read_csv(Path(__file__).resolve().parents[1] / "training/aquaculture_fish_health_dataset.csv",
                   parse_dates=["timestamp"])


def window(pond, end, hours=24):
    """Readings of `pond` for the `hours` up to `end`, as JSON-ready dicts, plus the true label at `end`."""
    end = pd.Timestamp(end)
    g = DATA[(DATA.pond_id == pond) & (DATA.timestamp <= end) & (DATA.timestamp > end - pd.Timedelta(hours=hours))]
    recs = json.loads(g[["timestamp", "temperature_c", "tds_ppm", "turbidity_ntu", "ph"]]
                      .to_json(orient="records", date_format="iso"))
    # to_json marks timestamps as UTC ("Z"); strip it so the API reads them as naive local pond time
    return [{**r, "timestamp": r["timestamp"].rstrip("Z")} for r in recs], g.health_status.iloc[-1]


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_model_info():
    j = client.get("/model-info").json()
    assert j["classes"] == ["Healthy", "Stressed", "Critical"] and j["n_features"] > 50


def test_predict_critical_and_healthy():
    """A clearly Critical and a clearly Healthy 24 h window are classified correctly."""
    crit = DATA[(DATA.pond_id == "P5") & (DATA.health_status == "Critical")].timestamp.iloc[400]
    recs, _ = window("P5", crit)
    j = client.post("/predict", json={"pond_id": "P5", "readings": recs}).json()
    assert j["health_status"] == "Critical" and j["reliable"]
    assert abs(sum(j["probabilities"].values()) - 1) < 1e-3 and j["top_factors"]

    ok = DATA[(DATA.pond_id == "P1") & (DATA.health_status == "Healthy")].timestamp.iloc[5000]
    recs, _ = window("P1", ok)
    assert client.post("/predict", json={"pond_id": "P1", "readings": recs}).json()["health_status"] == "Healthy"


def test_short_history_flagged():
    """30 min of history is below the 1 h minimum: the prediction must be marked unreliable."""
    recs, _ = window("P3", "2026-04-02 15:00", hours=0.5)
    j = client.post("/predict", json={"pond_id": "P3", "readings": recs}).json()
    assert j["reliable"] is False and j["data_quality"]["warnings"]


def test_sensor_fault_removed():
    """A DS18B20 disconnect (-127) is reported as a fault and replaced by the last valid temperature."""
    recs, _ = window("P3", "2026-04-02 15:00", hours=3)
    recs[-1]["temperature_c"] = -127.0
    j = client.post("/predict", json={"pond_id": "P3", "readings": recs}).json()
    assert "temperature_c" in j["data_quality"]["sensor_faults_removed"]
    assert 15 < j["latest_readings"]["temperature_c"] < 40


def test_ingest_and_status():
    """Readings pushed one at a time build up server-side history that /status then reports."""
    client.delete("/ponds/T1")      # start clean: the live buffers are module-level state
    recs, _ = window("P2", "2026-03-20 18:00", hours=2)
    for r in recs:
        last = client.post("/ingest/T1", json=r)
    assert last.status_code == 200
    assert client.get("/ponds/T1/status").json()["data_quality"]["history_hours"] >= 1.9
    assert client.get("/ponds/unknown/status").status_code == 404


def test_series():
    """12 h of 5-min readings give one point per step."""
    recs, _ = window("P4", "2026-04-25 12:00", hours=12)
    j = client.post("/predict/series", json={"pond_id": "P4", "readings": recs}).json()
    assert len(j["points"]) == 144      # 12 h x 12 steps per hour


def test_thingspeak(monkeypatch):
    """ThingSpeak is mocked: same window as the Critical test, in ThingSpeak's UTC format and field names."""
    import app.main as m
    crit = DATA[(DATA.pond_id == "P5") & (DATA.health_status == "Critical")].timestamp.iloc[400]
    recs, _ = window("P5", crit)
    feeds = [{"created_at": pd.Timestamp(r["timestamp"]).tz_localize("Asia/Kolkata").tz_convert("UTC")
              .strftime("%Y-%m-%dT%H:%M:%SZ"),
              "field1": str(r["turbidity_ntu"]), "field2": str(r["tds_ppm"]),
              "field3": str(r["ph"]), "field4": str(r["temperature_c"])} for r in recs]

    class Resp:                     # stand-in for httpx.Response with only what the endpoint uses
        status_code, text = 200, "{}"
        def json(self):
            return {"channel": {"id": 2653720}, "feeds": feeds}

    monkeypatch.setattr(m.httpx, "get", lambda *a, **k: Resp())
    j = client.get("/thingspeak/2653720/predict").json()
    assert j["health_status"] == "Critical" and j["pond_id"] == "thingspeak-2653720"
    assert j["timestamp"].startswith(str(crit.date()))      # UTC was converted back to pond-local time


def test_bad_input():
    """An empty list and a reading with no sensor values are both rejected with 422."""
    assert client.post("/predict", json={"readings": []}).status_code == 422
    assert client.post("/predict", json={"readings": [{"timestamp": "2026-04-01T10:00:00"}]}).status_code == 422


# ----------------------------------------------------------------------------- /ingest robustness
GOOD = {"temperature_c": 28.0, "tds_ppm": 300.0, "turbidity_ntu": 20.0, "ph": 7.5}


def test_ingest_mixed_timezones():
    """A timezone-aware reading after a naive one is accepted and lands on the same local timeline."""
    client.delete("/ponds/T2")
    assert client.post("/ingest/T2", json={"timestamp": "2026-04-01T10:00:00", **GOOD}).status_code == 200
    r = client.post("/ingest/T2", json={"timestamp": "2026-04-01T04:35:00Z", **GOOD})      # 10:05 IST
    assert r.status_code == 200 and r.json()["timestamp"] == "2026-04-01T10:05:00"


def test_ingest_future_timestamp_keeps_history():
    """A reading from a device with a wrong (future) clock is rejected and the buffer survives."""
    import app.main as m
    client.delete("/ponds/T3")
    recs, _ = window("P2", "2026-03-20 18:00", hours=2)
    for r in recs:
        client.post("/ingest/T3", json=r)
    held = len(m._buffers["T3"])
    future = (pd.Timestamp.now() + pd.Timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%S")
    assert client.post("/ingest/T3", json={"timestamp": future, **GOOD}).status_code == 422
    assert len(m._buffers["T3"]) == held == len(recs)
    assert client.post("/ingest/T3", json={"timestamp": "2026-03-20T18:05:00", **GOOD}).status_code == 200


def test_ingest_too_old_rejected():
    """A reading older than the 26 h window is refused; a late one inside the window is accepted."""
    client.delete("/ponds/T4")
    assert client.post("/ingest/T4", json={"timestamp": "2026-04-10T12:00:00", **GOOD}).status_code == 200
    assert client.post("/ingest/T4", json={"timestamp": "2026-04-08T12:00:00", **GOOD}).status_code == 422
    assert client.post("/ingest/T4", json={"timestamp": "2026-04-10T11:00:00", **GOOD}).status_code == 200


def test_ingest_memory_is_bounded(monkeypatch):
    """Repeated timestamps cannot grow a buffer past MAX_READINGS, and MAX_PONDS caps the pond count."""
    import app.main as m
    client.delete("/ponds/T5")
    monkeypatch.setattr(m, "MAX_READINGS", 50)
    for _ in range(80):
        client.post("/ingest/T5", json={"timestamp": "2026-04-01T10:00:00", **GOOD})
    assert len(m._buffers["T5"]) == 50

    monkeypatch.setattr(m, "MAX_PONDS", len(m._buffers))
    assert client.post("/ingest/T-new", json={"timestamp": "2026-04-01T10:00:00", **GOOD}).status_code == 429
    assert "T-new" not in m._buffers
    assert client.post("/ingest/T5", json={"timestamp": "2026-04-01T10:05:00", **GOOD}).status_code == 200


# ----------------------------------------------------------------------------- ThingSpeak robustness
def _thingspeak_returns(monkeypatch, feeds=None, bad_json=False):
    import app.main as m

    class Resp:
        status_code, text = 200, "<html>maintenance</html>" if bad_json else "{}"
        def json(self):
            if bad_json:
                raise ValueError("not JSON")
            return {"feeds": feeds}

    monkeypatch.setattr(m.httpx, "get", lambda *a, **k: Resp())


def test_thingspeak_skips_bad_entries(monkeypatch):
    """Entries without a valid timestamp are skipped; the valid ones are still used."""
    ok = {"created_at": "2026-04-01T04:30:00Z", "field1": "20", "field2": "300", "field3": "7.5", "field4": "28"}
    _thingspeak_returns(monkeypatch, [{"created_at": None, "field1": "1"}, {"field1": "1"}, ok])
    r = client.get("/thingspeak/1/predict")
    assert r.status_code == 200 and r.json()["timestamp"] == "2026-04-01T10:00:00"

    _thingspeak_returns(monkeypatch, [{"created_at": None, "field1": "1"}])
    assert client.get("/thingspeak/1/predict").status_code == 502


def test_thingspeak_bad_body_and_hours(monkeypatch):
    """A non-JSON body is a 502 (upstream problem); nonsensical `hours` values are a 422 (caller problem)."""
    _thingspeak_returns(monkeypatch, bad_json=True)
    assert client.get("/thingspeak/1/predict").status_code == 502
    for bad in ("-5", "0", "nan", "inf"):
        assert client.get(f"/thingspeak/1/predict?hours={bad}").status_code == 422
