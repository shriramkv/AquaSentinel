"""The Gemini agent layer, built with Google's Agent Development Kit (ADK).

Four roles, as in the architecture: Planner, Visual Inspector, Diagnostician, Reporter.
Every agent uses the single model named by GEMINI_MODEL in .env; no other model or provider is
referenced anywhere in this app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel, Field

from .. import config, cpcb, runtime
from . import tools

logger = logging.getLogger(__name__)
logging.getLogger("google_adk").setLevel(logging.WARNING)

# ADK's Gemini client reads the key from the environment
if config.GEMINI_API_KEY:
    os.environ["GOOGLE_API_KEY"] = config.GEMINI_API_KEY
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"

MODEL = config.GEMINI_MODEL
CALL_TIMEOUT_S = 120
LANGUAGES = {"en": "English", "ta": "Tamil (தமிழ், in Tamil script)", "hi": "Hindi (हिन्दी, in Devanagari script)"}


def _in_language(language: str, structured: bool = False) -> str:
    """Instruction that makes an agent write in the dashboard's language. Empty for English."""
    name = LANGUAGES.get(language)
    if not name or language == "en":
        return ""
    if structured:
        return (f"\nLANGUAGE: write every free-text value in {name}, in natural everyday wording, not transliteration. "
                "Keep the JSON keys, the probable_cause, confidence and label values, zone ids, units and numbers exactly as specified in English.")
    return f"\nLANGUAGE: reply in {name}, in natural everyday wording, not transliteration. Keep zone ids, units and numbers as they are."


CAUSES = ["sewage", "agricultural_runoff", "industrial_discharge", "algal_bloom", "natural_variation", "sensor_fault", "undetermined"]

CONTEXT = """You are part of AquaSentinel, an autonomous boat that surveys a lake in India for water pollution.
The boat measures pH, dissolved oxygen (DO, mg/L), temperature (°C), conductivity (EC, µS/cm), nitrate (mg/L)
and turbidity (NTU), geo-tagged. The lake has six zones: NW, N, NE, SW, S, SE.
Use only facts returned by your tools or given in the message. If the data does not support an answer, say so.
Never invent readings, locations, dates or regulatory limits."""


# ----------------------------------------------------------------------------- output schemas
class Finding(BaseModel):
    label: str = Field(description="One of: algal_bloom, foam, oil_sheen, floating_waste, outfall_pipe")
    present: bool
    confidence: float = Field(description="0 to 1")
    note: str = Field(description="What in the image supports this, in a few words")


class VisualFindings(BaseModel):
    findings: list[Finding]
    water_appearance: str = Field(description="Colour and clarity of the water surface in one sentence")
    summary: str = Field(description="One sentence on what the frame suggests about pollution")


class Alert(BaseModel):
    title: str
    body: str = Field(description="2 to 3 short sentences a resident can act on")


class Alerts(BaseModel):
    english: Alert
    tamil: Alert
    hindi: Alert


# ----------------------------------------------------------------------------- agents
planner = LlmAgent(
    name="planner", model=MODEL, tools=tools.PLANNER_TOOLS,
    description="Plans survey routes from plain-language requests and re-plans toward pollution hotspots.",
    instruction=CONTEXT + """
You are the Mission Planner. You turn an officer's request into a boat route, and you re-plan when an anomaly is found.

For a mission request:
1. Call get_lake_map to learn the places and zones.
2. Build the route with plan_transect_survey (for an area) and/or route_to_place (for a named spot). You may combine
   their waypoints in a sensible visiting order, or write your own latitude/longitude waypoints inside the lake.
   "Survey" or "check" a named spot means more than visiting one point: go to the spot with route_to_place, then cover
   the zone it lies in with plan_transect_survey at 60 m spacing. A single waypoint is right only for "go to" or "return to".
3. Send it with push_waypoints. If it is rejected, fix the route and try again (at most three attempts).
4. Reply with two sentences: what the boat will do and which parameters the officer should watch.
If the request is not about surveying this lake, do not push a route; say what you can do instead.

For an anomaly notice:
- If it has not been traced and the boat is surveying or idle, call start_plume_trace with its id so the boat follows
  the gradient to the source. Tracing is the default response to a new anomaly.
- If the boat is already tracing, or the anomaly is weak and matches the natural baseline drift, leave the route alone.
- Reply with one sentence stating the action taken and why.""")

visual_inspector = LlmAgent(
    name="visual_inspector", model=MODEL, output_schema=VisualFindings,
    description="Reads a camera frame from the boat for visible signs of pollution.",
    instruction="""You inspect one forward-facing camera frame taken from a small boat on a lake.
Report on exactly these five labels, each with present true/false: algal_bloom (green scum or film), foam (white froth),
oil_sheen (rainbow or iridescent film), floating_waste (litter, plastic), outfall_pipe (a pipe or drain discharging at the bank).
Judge only what is visible. Mark a label present only when you can point to it in the image; otherwise false with low confidence.""")

diagnostician = LlmAgent(
    name="diagnostician", model=MODEL, tools=tools.DIAGNOSTICIAN_TOOLS,
    description="Fuses sensor readings, visual findings, CPCB criteria and zone health into a probable cause.",
    instruction=CONTEXT + f"""
You are the Diagnostician. You are given one anomaly (parameters off baseline, peak readings, baseline, location, source
estimate) and the Visual Inspector's findings for a camera frame. Work out the most probable cause.

Method:
1. The message already contains what you need: peak readings and the baseline they are compared with, the CPCB class of
   the peak reading (`cpcb_class_at_peak`), and the fish-health model's result for the zone (`zone_health`).
   Compare peak with baseline to get the size and direction of each change.
2. Call a tool only if something essential is missing (for example cpcb_criteria for an exact limit, or
   get_recent_readings to check whether the change is sustained). Most diagnoses need no tool call.
3. Use `zone_health` to say whether the fish-health model reports risk to aquatic life in that zone.
4. Match the pattern. Typical signatures, to be weighed and not applied blindly:
   - sewage: DO falls sharply, conductivity and nitrate rise, turbidity rises, pH slightly lower; foam, litter, an outfall.
   - agricultural_runoff: nitrate rises most, turbidity rises, DO falls moderately, conductivity rises a little; often after rain; green film.
   - industrial_discharge: pH far from neutral, conductivity very high, temperature raised; oil sheen or discoloured water, an outfall.
   - algal_bloom: pH high by day, DO high by day and low before dawn, turbidity up, nitrate may fall; green scum.
   - natural_variation or sensor_fault when changes are small, isolated to one sensor, or physically inconsistent.
5. Set confidence honestly: high only when sensors and image agree and the changes are large; low when evidence conflicts.

Reply with ONE JSON object and nothing else, with these keys:
  "probable_cause": one of {CAUSES}
  "confidence": "low" | "medium" | "high"
  "headline": one sentence an officer can read at a glance
  "evidence": list of 3 to 5 short strings, each citing a number or observation you were given or retrieved
  "against": list of 0 to 2 short strings on what does not fit or is missing
  "cpcb_assessment": one or two sentences on the CPCB class from measured parameters, with its caveat
  "aquatic_life": one sentence on the zone's fish-health model result, including that it cannot see DO or nitrate if relevant
  "recommended_actions": list of 2 to 4 concrete actions for the pollution control officer
  "lab_tests_needed": list of lab analyses that would confirm the cause (for example BOD, coliform, ammonia)""")

_REPORTER_BASE = CONTEXT + """
You are the Reporter. You answer from the stored survey data through your tools.
Zones are named NW (north-west), N, NE, SW, S, SE; name the nearest inlet or outfall from get_lake_map when it helps.
State numbers with units. Say plainly when a period or zone has too few readings to support a conclusion.
The data may come from the built-in simulator; if get_boat_status or the message says so, say the figures are simulated."""

ask_the_lake = LlmAgent(
    name="reporter_ask_the_lake", model=MODEL, tools=tools.REPORTER_TOOLS,
    description="Answers officials' questions about the water body from live and stored data.",
    instruction=_REPORTER_BASE + """
Answer the question in at most 120 words, in plain language, leading with the answer.
Use compare_periods for "got worse / improved" questions, get_parameter_stats for levels, get_hotspots and list_diagnoses
for causes, get_zone_health for risk to fish. Use Markdown sparingly: a short list or one small table only when it helps.""")

report_writer = LlmAgent(
    name="reporter_survey_report", model=MODEL, tools=tools.REPORTER_TOOLS,
    description="Writes the narrative part of a regulator-ready survey report.",
    instruction=_REPORTER_BASE + """
Write the narrative for a survey report addressed to the State Pollution Control Board.
Gather the facts first: get_boat_status, get_parameter_stats for each of the six parameters over the report window,
get_hotspots, list_diagnoses, get_zone_health, and classify_water_quality on the lake-wide means and on the worst peak.
Then write Markdown with exactly these second-level headings:
## Summary
## Findings by zone
## Anomalies and probable sources
## CPCB assessment
## Risk to aquatic life
## Recommended actions
## Limitations of this survey
Keep it under 550 words. Use the measured numbers. Under Limitations, list what the boat does not measure
(coliform, BOD, ammonia) and any caveat your tools reported. Do not add a title, date or signature.""")

alert_writer = LlmAgent(
    name="reporter_community_alert", model=MODEL, output_schema=Alerts,
    description="Writes community alerts in English, Tamil and Hindi.",
    instruction="""You write a public water-quality alert for people living near a lake in Tamil Nadu, India, from the
diagnosis you are given. Write it in English, Tamil (தமிழ்) and Hindi (हिन्दी). Each version has a short title and a body
of two or three short sentences: what was found and where, what people should avoid (for example contact with the water,
fishing, using it for cattle) and that the pollution control board has been informed. Use everyday words, no jargon and no
numbers except the place. Match the stated confidence: if it is low or medium, say the cause is suspected, not confirmed.
The Tamil and Hindi versions must be natural translations in their own scripts, not transliterations.""")

_runners: dict[str, InMemoryRunner] = {}


# ----------------------------------------------------------------------------- running an agent
async def run_agent(agent: LlmAgent, text: str, image: bytes | None = None, session_id: str | None = None,
                    media: tuple[bytes, str] | None = None) -> dict:
    """Send one message to an agent, optionally with a JPEG `image` or other `media` (bytes, MIME type).
    Returns {"text", "tool_calls"}; raises on API errors."""
    if not config.GEMINI_READY:
        raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY and GEMINI_MODEL in .env and restart.")
    runner = _runners.get(agent.name)
    if runner is None:
        runner = _runners[agent.name] = InMemoryRunner(agent=agent, app_name="aquasentinel")
    sid = session_id or uuid.uuid4().hex
    if await runner.session_service.get_session(app_name="aquasentinel", user_id="officer", session_id=sid) is None:
        await runner.session_service.create_session(app_name="aquasentinel", user_id="officer", session_id=sid)
    parts = [types.Part.from_text(text=text)]
    if image:
        parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
    if media:
        parts.append(types.Part.from_bytes(data=media[0], mime_type=media[1]))
    calls: list[dict] = []
    final = ""

    async def consume():
        nonlocal final
        async for event in runner.run_async(user_id="officer", session_id=sid,
                                            new_message=types.Content(role="user", parts=parts)):
            if event.error_code or event.error_message:
                raise RuntimeError(f"Gemini error {event.error_code or ''}: {event.error_message or ''}".strip())
            for fc in event.get_function_calls():
                calls.append({"name": fc.name, "args": dict(fc.args or {})})
            for fr in event.get_function_responses():
                for c in reversed(calls):
                    if c["name"] == fr.name and "result" not in c:
                        c["result"] = _preview(fr.response)
                        break
            if event.is_final_response() and event.content and event.content.parts:
                final = "".join(p.text for p in event.content.parts if p.text and not getattr(p, "thought", False))

    # Gemini sometimes answers 503 "high demand". Retry that, but only while no tool has run, so a boat
    # command is never sent twice. A 429 (quota used up) is not retried: retries would spend more of it.
    for attempt, wait in enumerate((4, 10, None)):
        try:
            await asyncio.wait_for(consume(), CALL_TIMEOUT_S)
            break
        except Exception as e:
            busy = any(k in str(e) for k in ("503", "UNAVAILABLE", "overloaded")) and "RESOURCE_EXHAUSTED" not in str(e)
            if wait is None or not busy or calls:
                raise
            logger.warning("Gemini busy (%s); retry %d in %d s", str(e)[:80], attempt + 1, wait)
            await asyncio.sleep(wait)
    return {"text": final.strip(), "tool_calls": calls}


def _preview(obj, limit: int = 400) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "..."


def parse_json(text: str) -> dict:
    """Extract the JSON object from a model reply (tolerates code fences and surrounding prose)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        out = json.loads(cleaned)
    except ValueError:
        a, b = cleaned.find("{"), cleaned.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("The model did not return JSON")
        out = json.loads(cleaned[a:b + 1])
    if not isinstance(out, dict):
        raise ValueError("The model returned JSON that is not an object")
    return out


# ----------------------------------------------------------------------------- tasks
async def plan_mission(request: str, language: str = "en") -> dict:
    hub = runtime.hub
    before = hub.mission["id"] if hub.mission else None
    hub.pending_request = request
    try:
        res = await run_agent(planner, "Mission request from the officer (it may be in any language): " + request +
                              _in_language(language) + ("\nWrite the push_waypoints summary in that language too." if language != "en" else ""))
    finally:
        hub.pending_request = ""
    res["mission"] = hub.mission if hub.mission and hub.mission["id"] != before else None
    return res


async def replan(anomaly: dict, language: str = "en") -> dict:
    hub = runtime.hub
    notice = {"anomaly": tools._anomaly_view(anomaly), "boat": hub.boat.state(), "units": "ph, do mg/L, temp °C, ec µS/cm, nitrate mg/L, turbidity NTU"}
    return await run_agent(planner, "Anomaly notice. Decide how the boat should respond.\n" + json.dumps(notice, default=str) + _in_language(language))


async def inspect_frame(jpeg: bytes, simulated: bool = False, language: str = "en") -> dict:
    res = await run_agent(visual_inspector, "Inspect this camera frame from the boat." + _in_language(language, structured=True), image=jpeg)
    out = parse_json(res["text"])
    out["frame_is_simulated"] = simulated
    return out


async def diagnose(anomaly: dict, jpeg: bytes | None, simulated_frame: bool = False, language: str = "en") -> dict:
    """Visual Inspector first (if a frame exists), then the Diagnostician. Returns the diagnosis body to store."""
    visual, visual_error = None, None
    if jpeg:
        try:
            visual = await inspect_frame(jpeg, simulated_frame, language)
        except Exception as e:                      # a failed image read should not block the sensor-based diagnosis
            visual_error = f"{type(e).__name__}: {e}"[:300]
            logger.warning("Visual inspection failed: %s", visual_error)
    peak = anomaly.get("peak") or {}
    zone = next((z for z in tools.get_zone_health().get("zones", []) if z.get("zone") == anomaly.get("zone")), None)
    msg = {
        "anomaly": tools._anomaly_view(anomaly),
        "cpcb_class_at_peak": cpcb.classify(peak.get("ph"), peak.get("do"), peak.get("ec")),
        "zone_health": zone or "not available",
        "zone_health_note": "From the fish-health ML model, which uses temperature, TDS, turbidity and pH only; it cannot see DO or nitrate.",
        "units": "ph, do mg/L, temp °C, ec µS/cm, nitrate mg/L, turbidity NTU",
        "visual_findings": visual or f"No usable camera frame ({visual_error or 'none captured'})",
        "note": ("The camera frame is a simulated stand-in generated by the demo simulator." if simulated_frame and visual else None),
    }
    res = await run_agent(diagnostician, "Diagnose this anomaly.\n" + json.dumps(msg, default=str, ensure_ascii=False) + _in_language(language, structured=True))
    try:
        body = parse_json(res["text"])
    except ValueError:
        body = {"probable_cause": "undetermined", "confidence": "low", "headline": "The model's reply could not be parsed.",
                "raw_reply": res["text"][:2000]}
    if body.get("probable_cause") not in CAUSES:
        body["probable_cause"] = str(body.get("probable_cause", "undetermined"))[:60]
    body.update(visual=visual, visual_error=visual_error, tool_calls=res["tool_calls"], model=MODEL, language=language)
    return body


async def ask(question: str, session_id: str, language: str = "en") -> dict:
    hub = runtime.hub
    prefix = "[Data source: built-in simulator] " if hub.simulated else ""
    # The question may be typed or spoken in any language: answer in that language, else the dashboard's
    rule = ("\nLANGUAGE: answer in the language the question is written in. If that is unclear, answer in "
            f"{LANGUAGES.get(language, 'English')}. Keep zone ids, units and numbers as they are.")
    return await run_agent(ask_the_lake, prefix + question + rule, session_id=session_id)


async def write_report(hours: float) -> dict:
    hub = runtime.hub
    src = "The data comes from the built-in simulator, not a field survey. " if hub.simulated else ""
    return await run_agent(report_writer, f"{src}Write the report narrative for the last {hours:g} hours of survey data.")


async def write_alerts(anomaly: dict, diagnosis: dict) -> dict:
    lake = runtime.hub.lake
    near = None
    if anomaly.get("source_lat") is not None:
        hit = lake.nearest_place(*lake.to_xy(anomaly["source_lat"], anomaly["source_lon"]))
        near = hit and hit[0].name
    msg = {"lake": lake.name, "zone": anomaly.get("zone"), "nearest_named_place": near,
           "probable_cause": diagnosis.get("probable_cause"), "confidence": diagnosis.get("confidence"),
           "headline": diagnosis.get("headline"), "recommended_actions": diagnosis.get("recommended_actions")}
    res = await run_agent(alert_writer, "Write the community alert for this finding.\n" + json.dumps(msg, ensure_ascii=False))
    out = parse_json(res["text"])
    out["model"] = MODEL
    return out


transcriber = LlmAgent(
    name="transcriber", model=MODEL,
    description="Turns a short voice recording into text.",
    instruction="""You transcribe one short voice recording from a water-quality officer in India.
Write exactly what was said, in the language and script it was spoken in (for example Tamil in Tamil script, Hindi in
Devanagari, English in Latin script). Do not translate, answer, summarise or add punctuation that changes meaning.
Reply with the transcript only. If there is no intelligible speech, reply with an empty line.""")


async def transcribe(audio: bytes, mime_type: str, language: str = "en") -> str:
    hint = f"The speaker most likely uses {LANGUAGES.get(language, 'English')}, but may use another language."
    res = await run_agent(transcriber, "Transcribe this recording. " + hint, media=(audio, mime_type))
    return res["text"].strip().strip('"')


async def self_test() -> dict:
    """One tiny call to confirm the key and model work."""
    probe = LlmAgent(name="probe", model=MODEL, instruction="Reply with the single word: ready")
    res = await run_agent(probe, "Are you there?")
    return {"ok": True, "model": MODEL, "reply": res["text"][:80]}
