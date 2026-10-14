"""Simulator, detector, tracing, zone model, CPCB and storage. No network, no Gemini."""
import math
import time

import pytest

from aquasentinel import camera, cpcb
from aquasentinel.anomaly import Detector
from aquasentinel.geo import lake
from aquasentinel.simulator import SCENARIOS, TRUTH_MIN, Boat, World
from aquasentinel.store import Store
from aquasentinel.zone_health import ZoneHealth


def run_survey(kind: str | None, ticks: int = 4000):
    """Sail the whole-lake survey; start `kind` after a clean stretch and trace the first anomaly."""
    world = World(lake)
    boat = Boat(lake, world)
    det = Detector()
    boat.score_fn = det.score
    done = {}
    boat.on_trace_done = lambda aid, est, why: done.update(est=est, why=why)
    boat.set_route([boat.home] + lake.transects())
    now, sc, traced = time.time(), None, False
    stats = {"tp": 0, "fp": 0, "fn": 0, "outside": 0, "flags": 0}
    for i in range(ticks):
        now += 0.5
        if kind and i == 240:
            sc = world.add_scenario(kind)
        boat.step(0.5, now)
        stats["outside"] += not lake.contains(boat.x, boat.y)
        if i % 2 == 0:
            values, truth = world.sample(boat.x, boat.y, now)
            res = det.update({"ts": now, **values})
            polluted = truth >= TRUTH_MIN
            stats["flags"] += res["flagged"]
            stats["tp"] += res["flagged"] and polluted
            stats["fp"] += res["flagged"] and not polluted
            stats["fn"] += (not res["flagged"]) and polluted
            if res["opened"] and not traced:
                traced = boat.start_trace(res["opened"]["params"], 1)
        if boat.mode == "idle" and i > 300:
            break
    return stats, done, sc


def test_lake_geometry():
    assert len(lake.zones) == 6
    assert lake.contains(0, 0) and not lake.contains(5000, 0)
    route = lake.transects()
    assert len(route) >= 6
    assert all(lake.navigable(x, y) for x, y in route)
    assert all(lake.path_ok(a, b) for a, b in zip(route, route[1:]))
    lat, lon = lake.to_ll(120.0, -80.0)
    x, y = lake.to_xy(lat, lon)
    assert abs(x - 120) < 0.2 and abs(y + 80) < 0.2


def test_clean_lake_raises_no_alarm():
    stats, done, _ = run_survey(None, ticks=1500)
    assert stats["outside"] == 0
    assert stats["flags"] == 0
    assert not done


@pytest.mark.parametrize("kind", list(SCENARIOS))
def test_scenario_is_detected_and_traced_to_source(kind):
    stats, done, sc = run_survey(kind)
    assert stats["outside"] == 0, "boat left the lake"
    assert stats["tp"] > 0, "plume was never flagged"
    precision = stats["tp"] / (stats["tp"] + stats["fp"])
    recall = stats["tp"] / (stats["tp"] + stats["fn"])
    assert precision >= 0.75 and recall >= 0.75, (precision, recall)
    assert done, "trace did not finish"
    sx, sy, d = lake.nearest_shore(*done["est"])
    ex, ey = (sx, sy) if d < 60 else done["est"]
    assert math.hypot(ex - sc.x, ey - sc.y) < 25, "source estimate is more than 25 m from the true source"


def test_zone_model_risk_rises_under_sustained_industrial_discharge():
    zh = ZoneHealth()
    assert zh.error is None, zh.error
    now = time.time()
    clean = World(lake)
    res = zh.refresh({z["id"]: clean.station_rows(z, now) for z in lake.zones})
    assert all(p["available"] and p["reliable"] and p["health_status"] == "Healthy" for p in res.values())
    dirty = World(lake)
    sc = dirty.add_scenario("industrial", hours_ago=12)
    zone = lake.zone_of(*lake.pull_inside(sc.x, sc.y))
    before = res[zone]["probabilities"]["Healthy"]
    res = zh.refresh({z["id"]: dirty.station_rows(z, now) for z in lake.zones})
    # Whether the class flips to Stressed depends on the time of day the 24 h window ends (the model
    # uses daily cycles), so the robust check is that the risk rises clearly against clean water.
    assert res[zone]["probabilities"]["Healthy"] < before - 0.2, (before, res[zone]["probabilities"])
    assert len(res[zone]["top_factors"]) > 0
    series = zh.series(zone, dirty.station_rows(next(z for z in lake.zones if z["id"] == zone), now))
    assert len(series) == 288


def test_zone_model_reports_missing_data_instead_of_guessing():
    zh = ZoneHealth()
    assert zh.predict("NW", [])["available"] is False


def test_cpcb_classification():
    assert cpcb.classify(7.5, 6.5, 400)["best_class_on_measured_parameters"] == "A"
    assert cpcb.classify(7.5, 5.2, 400)["best_class_on_measured_parameters"] == "B"
    assert cpcb.classify(7.5, 4.2, 400)["best_class_on_measured_parameters"] == "C"
    assert cpcb.classify(7.5, 2.0, 400)["best_class_on_measured_parameters"] == "E"
    assert cpcb.classify(5.0, 2.0, 3000)["best_class_on_measured_parameters"] == "below E"
    assert cpcb.status("do", 3.0) == "breach" and cpcb.status("turbidity", 500) == "none"


def test_camera_frames_and_labels():
    jpeg, labels = camera.render("sewage", 0.9, True, seed=1)
    assert jpeg[:2] == b"\xff\xd8" and {"foam", "floating_waste", "outfall_pipe"} <= set(labels)
    assert camera.render(None, 0.0, False, seed=2)[1] == []
    assert camera.render("industrial", 0.8, False, seed=3)[1] == ["oil_sheen"]


def test_store_keeps_truth_away_from_readers(tmp_path):
    st = Store(tmp_path / "t.db")
    now = time.time()
    for i in range(10):
        st.add_reading({"ts": now - i, "lat": 11.0, "lon": 77.0, "zone": "N", "ph": 7.0 + i / 10, "do": 6.0, "temp": 28.0,
                        "ec": 400.0, "nitrate": 4.0, "turbidity": 10.0, "source": "simulator", "flagged": int(i < 3), "truth": 2.0 if i < 4 else 0.0})
    assert all("truth" not in r for r in st.recent_readings(5))
    s = st.stats("ph", now - 60)[0]
    assert s["n"] == 10 and abs(s["mean"] - 7.45) < 1e-6
    assert st.confusion(now - 60, 1.0) == {"tp": 3, "fp": 0, "fn": 1, "tn": 6, "n": 10}


def test_discharge_already_active_at_startup_is_caught_on_first_pass():
    """The baseline must not be learned inside a plume that exists before the first reading."""
    world = World(lake)
    sc = world.add_scenario("industrial", hours_ago=12)
    boat = Boat(lake, world)
    boat.speed_scale = 16
    det = Detector()
    boat.set_route([boat.home] + lake.transects())
    now, first_flag_distance = time.time(), None
    for i in range(400):
        now += 0.5
        boat.step(0.5, now)
        if i % 2 == 0:
            values, _ = world.sample(boat.x, boat.y, now)
            if det.update({"ts": now, **values})["flagged"]:
                first_flag_distance = math.hypot(boat.x - sc.x, boat.y - sc.y)
                break
    assert first_flag_distance is not None, "plume present from the start was never flagged"
    assert first_flag_distance > 100, "flag came only after the boat was already at the source"
