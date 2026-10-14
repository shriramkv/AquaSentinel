"""HTTP API and the agent layer. Gemini is replaced by a scripted stub model, so nothing leaves the machine."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from aquasentinel import config, main, runtime
from aquasentinel.agents import service, tools


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_state_and_gated_endpoints(client):
    s = client.get("/api/state").json()
    assert s["config"]["simulated"] and not s["config"]["gemini_ready"]
    assert s["mission"]["planner"] == "preset" and len(s["lake"]["zones"]) == 6
    # No key: agent endpoints refuse clearly instead of inventing output
    for path, body in [("/api/ask", {"question": "hello"}), ("/api/missions", {"request": "survey the lake"}), ("/api/alerts", {"anomaly_id": 1})]:
        r = client.post(path, json=body)
        assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
    # Simulator is the source: the real-boat endpoint is closed
    assert client.post("/api/ingest", json={"lat": 11.0, "lon": 77.0, "ph": 7.0}).status_code == 409
    assert client.post("/api/scenarios", json={"kind": "nonsense"}).status_code == 422
    assert client.get("/api/camera/latest.jpg").headers["content-type"] == "image/jpeg"


def test_report_without_gemini_is_tables_only(client):
    r = client.post("/api/reports", json={"hours": 1, "narrative": False})
    assert r.status_code == 200 and r.json()["report"]["ai"] == 0
    pdf = client.get(f"/api/reports/{r.json()['report']['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get("/api/reports/9999/pdf").status_code == 404


def test_ingest_validation_in_boat_mode(client, monkeypatch):
    monkeypatch.setattr(main.hub, "simulated", False)
    lat, lon = main.hub.lake.to_ll(0, 0)
    ok = client.post("/api/ingest", json={"lat": lat, "lon": lon, "ph": 7.2, "do": 6.5, "temp": 28, "ec": 410, "nitrate": 4})
    assert ok.status_code == 200 and ok.json()["zone"] in main.hub.lake.zone_points
    assert client.post("/api/ingest", json={"lat": lat, "lon": lon}).status_code == 422                 # no sensor values
    assert client.post("/api/ingest", json={"lat": lat, "lon": lon, "ph": 99}).status_code == 422       # impossible pH
    assert client.post("/api/ingest", json={"lat": lat, "lon": lon, "ph": 7, "timestamp": 9e12}).status_code == 422
    monkeypatch.setattr(config, "INGEST_API_KEY", "secret")
    assert client.post("/api/ingest", json={"lat": lat, "lon": lon, "ph": 7}).status_code == 401
    assert client.post("/api/ingest", json={"lat": lat, "lon": lon, "ph": 7}, headers={"X-API-Key": "secret"}).status_code == 200


def test_planner_tools_validate_routes(client):
    hub = runtime.hub
    plan = tools.plan_transect_survey("N", 80)
    assert plan["waypoints"] >= 2 and "error" not in tools.route_to_place("north_inlet")
    assert "error" in tools.plan_transect_survey("Atlantis", 80)

    async def go():
        before = hub.mission["id"]
        far = await tools.push_waypoints([12.5], [78.0], "outside the lake")
        assert far["accepted"] is False and "outside the lake" in far["error"]
        assert hub.mission["id"] == before, "a rejected route must not replace the mission"
        assert (await tools.push_waypoints([11.0], [77.0, 77.1], "bad lengths"))["accepted"] is False
        good = await tools.push_waypoints(plan["latitudes"], plan["longitudes"], "North zone survey")
        assert good["accepted"] and hub.mission["planner"] == "gemini" and hub.mission["id"] != before
    asyncio.run(go())


def test_data_tools_never_expose_ground_truth(client):
    for out in (tools.get_recent_readings("all", 20), tools.get_parameter_stats("nitrate", 1), tools.get_hotspots(1),
                tools.compare_periods("do", 0.1, 0.1), tools.get_zone_health(), tools.get_lake_map(), tools.get_boat_status()):
        text = json.dumps(out, default=str)
        assert "truth" not in text and "scenario" not in text
    assert "error" in tools.get_parameter_stats("mercury", 1)
    assert tools.classify_water_quality(7.4, 3.1, 500)["best_class_on_measured_parameters"] == "E"


class ScriptedLlm(BaseLlm):
    """Stands in for Gemini: first asks for a tool, then answers using the tool's result."""
    model: str = "scripted"

    async def generate_content_async(self, llm_request, stream=False):
        last = llm_request.contents[-1].parts[-1]
        if last.function_response is None:
            call = types.Part.from_function_call(name="classify_water_quality",
                                                 args={"ph": 7.4, "dissolved_oxygen_mg_l": 3.1, "conductivity_us_cm": 500})
            yield LlmResponse(content=types.Content(role="model", parts=[call]))
        else:
            cls = last.function_response.response["best_class_on_measured_parameters"]
            reply = "```json\n" + json.dumps({"probable_cause": "sewage", "confidence": "medium", "headline": f"Class {cls}"}) + "\n```"
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=reply)]))


def test_agent_run_collects_tool_calls_and_parses_json(client, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_READY", True)
    agent = LlmAgent(name="scripted_diagnostician", model=ScriptedLlm(), instruction="test", tools=[tools.classify_water_quality])
    res = asyncio.run(service.run_agent(agent, "Diagnose"))
    assert [c["name"] for c in res["tool_calls"]] == ["classify_water_quality"]
    assert '"E"' in res["tool_calls"][0]["result"]
    assert service.parse_json(res["text"]) == {"probable_cause": "sewage", "confidence": "medium", "headline": "Class E"}


def test_parse_json_rejects_prose():
    assert service.parse_json('Here you go: {"a": 1} hope it helps') == {"a": 1}
    with pytest.raises(ValueError):
        service.parse_json("I could not decide.")


def test_agents_use_only_the_env_model():
    for a in (service.planner, service.visual_inspector, service.diagnostician, service.ask_the_lake, service.report_writer, service.alert_writer):
        assert a.model == config.GEMINI_MODEL
