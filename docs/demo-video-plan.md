# AquaSentinel: 2:30 product demo video plan

## Context

You need a 150-second demo video of AquaSentinel for the AI Builder Cup. It shows only the working product and starts with the product already running. Every action and claim below matches what is built in `Aquaculture_Webapp/`. Nothing from the deck that was not built (BigQuery, Pub/Sub, Firebase Auth, SMS or push alerts, the real boat on water) appears in the video.


**Story the video tells.** An officer gives the boat a voice mission. A hidden industrial discharge is planted. The boat detects it, Gemini re-plans the route, and the boat traces the plume to its source. Gemini diagnoses the cause from sensors, camera and CPCB criteria, and the fish-health ML model flags the zone. An official then asks a question by voice in Tamil, and the team produces alerts in three languages and a PDF report. The video ends on the measured benchmarks and the phone view.

**Choice of scenario.** Use an industrial discharge that began 12 h ago. Reasons:
- It was detected and traced to within about 9 m of the source in testing.
- It is the scenario that makes your LightGBM zone model turn **Stressed**. Sewage leaves the model at Healthy, because the model has no DO or nitrate input.
- Its camera frame shows an oil sheen and an outfall pipe.

---

## 1. Storyboard: exactly 150 s

| # | Time | Duration | Feature | Screen |
|---|---|---|---|---|
| 1 | 0:00–0:10 | 10 s | Live telemetry, map, KPI tiles | Live mission page, boat surveying |
| 2 | 0:10–0:28 | 18 s | Voice mission → Gemini Planner → validated route | Mission planner card + map |
| 3 | 0:28–0:38 | 10 s | Plant a hidden discharge (simulator) | Scenario card |
| 4 | 0:38–0:58 | 20 s | Detection → Gemini re-plan → plume trace → source estimate | Map, tiles, diagnosis card, activity feed |
| 5 | 0:58–1:18 | 20 s | Gemini diagnosis (sensors + camera + CPCB) and the true-source check | Diagnosis card, camera, map |
| 6 | 1:18–1:30 | 12 s | Your LightGBM fish-health model per zone, with TreeSHAP | Zone health page |
| 7 | 1:30–1:50 | 20 s | Tamil interface + voice question + Tamil answer | Language selector, Ask the Lake |
| 8 | 1:50–2:05 | 15 s | Alerts in three languages + PDF report | Reports & alerts page, PDF |
| 9 | 2:05–2:18 | 13 s | Measured benchmarks | Benchmarks page |
| 10 | 2:18–2:30 | 12 s | Phone layout in Hindi | Phone-size view |

Total: 10 + 18 + 10 + 20 + 20 + 12 + 20 + 15 + 13 + 12 = **150 s**.

### Scene 1 · 0:00–0:10 · Live dashboard
- **On screen:** the Live mission page in English, light theme. Six KPI tiles with values, the map with the boat moving and a trail of coloured dots, charts filling in.
- **Action:** no clicks. Move the cursor slowly across the KPI row, then to the boat on the map.
- **Edit:** open cold, with no title card. Slow 1.0 → 1.08 zoom toward the map over the scene.
- **Caption (bottom-left, 0:01–0:05):** `Live · simulated lake`
- **VO:** "This is AquaSentinel, live. Our autonomous boat is surveying the lake and streaming pH, oxygen, temperature, conductivity, nitrate and turbidity every second."

### Scene 2 · 0:10–0:28 · Voice mission
- **On screen:** the Mission planner card (right column), then the map.
- **Actions:**
  1. Click the **microphone** button beside "Tell the boat what to survey…". It turns red and pulses.
  2. Say clearly: **"Survey the south-west zone and check conductivity."** The words appear in the box as you speak.
  3. Click **Send** (the teal arrow button).
  4. The Gemini reply appears under the box. The mission line changes to "planned by Gemini". A new dashed route appears on the map and the boat turns toward the south-west.
- **Edit:**
  - Zoom to the Mission card (about 1.4×) for steps 1–3.
  - Keep the live audio of the spoken command; duck the music under it.
  - Jump-cut the Gemini wait. Show the spinner for about 1 s, then cut to the reply.
  - Zoom out to the map for step 4.
- **Captions:** `Voice → Gemini Planner` (step 1); `Every leg checked to stay on water` (step 4).
- **VO:** "Missions are given in plain language, even by voice." *[spoken command plays]* "Gemini's planner agent turns it into GPS waypoints, checks every leg stays on water, and re-routes the boat."

### Scene 3 · 0:28–0:38 · Plant a hidden discharge
- **On screen:** the "Simulator: pollution scenarios" card.
- **Actions:**
  1. Confirm "Discharge began" shows **12 h ago**. Set it before recording.
  2. Click **Industrial discharge**. The button shows "· active". The activity feed logs the scenario.
- **Edit:** zoom to the Scenario card (about 1.3×). Cursor highlight on the button.
- **Caption:** `Hidden scenario: the agents are told nothing`
- **VO:** "This lake is simulated, so I'll plant a hidden industrial discharge that began twelve hours ago. The agents are told nothing."

### Scene 4 · 0:38–0:58 · Detect → re-plan → trace (time-lapse)
- **On screen:** full Live page, then the map.
- **What happens (no clicks; just watch):**
  1. Map dots near the south-west drain get red rings. The pH and Conductivity tiles show warning or "Limit breached".
  2. The activity feed shows "Anomaly #1 in zone SW…", then "Asking Gemini how to respond…".
  3. The diagnosis card shows "Re-plan (Gemini): …".
  4. The boat chip changes to **Tracing plume**. The boat pulses, draws an orange trace line along the shoreline, and a red **!** source-estimate marker appears.
- **Edit:**
  - Time-lapse at about 4×; real time is roughly 60–90 s.
  - Cut in on the KPI tiles (1.3×) as they turn, then on the map.
  - Hold real speed for 1.5 s when the **!** appears.
- **Captions:** `Time-lapse · boat at 16× demo speed` (whole scene); `Source estimate` (pointing at **!**).
- **VO:** "The detector flags pH, conductivity and temperature off their baseline. Gemini decides to chase it: the boat leaves its route, climbs the pollution gradient, then follows the shoreline until the signal peaks, and marks its source estimate."

### Scene 5 · 0:58–1:18 · Gemini diagnosis + proof
- **On screen:** the Gemini diagnosis card, then the camera card, then the map.
- **Actions:**
  1. Scroll the right column so the diagnosis card fills the view.
  2. Move the cursor down through the card:
     - Likely cause (expected: **Industrial discharge**, with a confidence level)
     - Evidence bullets
     - Camera chips (expected: oil sheen, outfall pipe)
     - CPCB line
     - Recommended actions
     - Lab tests
  3. Glance at the Boat camera card: the frame shows the oil sheen.
  4. Back on the map, tick **show true source**. A black **×** appears next to the red **!**.
- **Edit:**
  - Zoom 1.5× on the "Likely cause" box, then pan down the card at a steady pace.
  - Final zoom on the **!** and **×** together, held 2 s.
  - Jump-cut out the scrolling.
- **Captions:**
  - `Gemini fuses sensors + camera + CPCB criteria` (step 2)
  - `Simulated camera frame` (step 3)
  - `Black × = true hidden source` (step 4)
- **VO:** "The diagnostician fuses sensor readings, the camera frame and CPCB criteria. Likely cause: industrial discharge, with evidence, what doesn't fit, recommended actions and confirming lab tests. And the simulator's hidden source? Right where the boat said."
- **If Gemini names a different cause,** read the cause it actually gave. Do not edit the screen.

### Scene 6 · 1:18–1:30 · Zone health (your ML model)
- **Actions:**
  1. Click **Zone health** in the top navigation.
  2. Click the **South-west · SW** card. Expected: **Stressed**, with a confidence figure.
  3. Show the factor bars on the left and the 24 h status strip on the right, which ends in yellow.
- **Edit:** zoom to the SW card, then to the factor bars. Cut the page-load frames.
- **Caption:** `Our LightGBM model · TreeSHAP factors`
- **VO:** "Our LightGBM fish-health model scores every zone. The south-west is now stressed, and its SHAP factors show exactly why."

### Scene 7 · 1:30–1:50 · Tamil + voice question
- **Actions:**
  1. Open the language selector in the header and choose **தமிழ்**. The whole interface switches to Tamil.
  2. Click **ஏரியிடம் கேள்** (Ask the Lake).
  3. Click the **microphone** and say in Tamil: **"எந்த மண்டலத்தில் நீரின் தரம் மோசமாக உள்ளது, ஏன்?"** ("Which zone has poor water quality, and why?")
  4. Click **Send**. The answer appears in Tamil.
  5. Click the **"n கருவி அழைப்புகள்"** (tool calls) toggle under the answer to expand it.
- **Edit:**
  - Hold for 1 s on the moment the interface flips to Tamil.
  - Keep the live Tamil audio.
  - Jump-cut the answer wait, and zoom 1.3× on the answer.
- **Captions:** `Tamil voice question` (step 3); `Answer grounded in live data · tool calls shown` (step 5).
- **VO:** "Switch the whole interface to Tamil or Hindi. Officials can question the lake by voice, in their own language." *[Tamil question plays]* "The answer comes back in Tamil, grounded in live data, with every tool call shown."
- **No Tamil speaker?** Click the first suggestion chip instead, which is in Tamil. Change the VO to "…ask questions in their own language".

### Scene 8 · 1:50–2:05 · Alerts + PDF report
- **Actions:**
  1. Click **அறிக்கைகள் & எச்சரிக்கைகள்** (Reports & alerts).
  2. In Community alerts, choose **#1** in the anomaly dropdown and click **Write alerts**. Three cards appear: English, Tamil, Hindi.
  3. In Survey report, leave the window on "last 1 h" with Gemini narrative ticked, and click **Generate report**.
  4. Click **PDF** on the new report. Scroll the PDF for 3 s: the parameter tables, anomaly #1 with its source estimate and cause, the zone table with SW Stressed, and the Gemini narrative.
- **Edit:**
  - Jump-cut both Gemini waits.
  - Zoom 1.3× on the three alert cards.
  - Fast scroll (2×) through the PDF.
- **Caption:** `PDF: tables from stored data + Gemini narrative`
- **VO:** "One click writes community alerts in English, Tamil and Hindi. Another builds a regulator-ready PDF: data tables straight from storage, plus Gemini's narrative."

### Scene 9 · 2:05–2:18 · Benchmarks
- **Actions:**
  1. Switch the language back to **English** (one selection).
  2. Click **Benchmarks**.
  3. Point at:
     - the "These are simulator results" notice
     - the Source localisation row (metres, ✓ met)
     - the Anomaly detection row (precision and recall)
     - the End-to-end latency row
- **Edit:** zoom 1.3× on the table and pan row by row.
- **Caption:** `Simulator results, not field trials`
- **VO:** "And every claim is measured against the simulator's hidden truth: source-location error in metres, detection precision and recall, and end-to-end latency."

### Scene 10 · 2:18–2:30 · Phone view
- **On screen:** a phone-size view (recorded separately, see section 3).
  - Interface in Hindi, dark theme.
  - Two-column KPI tiles, the map, bottom tab bar.
  - Tap the **ज़ोन** tab to show the zone cards, then back to **लाइव**.
- **Edit:** phone frame centred on a plain background matching the page colour. Hard cut in; fade to black over the last 0.5 s.
- **Caption:** `Phone · Hindi`
- **VO:** "It runs on phones and tablets, in every language. AquaSentinel: it doesn't just measure water quality, it explains it, and acts on it."

---

## 2. Voice-over script (about 260 words, roughly 2.3 words per second with room for the spoken commands)

| Time | Line |
|---|---|
| 0:00 | This is AquaSentinel, live. Our autonomous boat is surveying the lake and streaming pH, oxygen, temperature, conductivity, nitrate and turbidity every second. |
| 0:10 | Missions are given in plain language, even by voice. |
| 0:14 | *[live: "Survey the south-west zone and check conductivity."]* |
| 0:18 | Gemini's planner agent turns it into GPS waypoints, checks every leg stays on water, and re-routes the boat. |
| 0:28 | This lake is simulated, so I'll plant a hidden industrial discharge that began twelve hours ago. The agents are told nothing. |
| 0:38 | The detector flags pH, conductivity and temperature off their baseline. Gemini decides to chase it: the boat leaves its route, climbs the pollution gradient, then follows the shoreline until the signal peaks, and marks its source estimate. |
| 0:58 | The diagnostician fuses sensor readings, the camera frame and CPCB criteria. Likely cause: industrial discharge, with evidence, what doesn't fit, recommended actions and confirming lab tests. And the simulator's hidden source? Right where the boat said. |
| 1:18 | Our LightGBM fish-health model scores every zone. The south-west is now stressed, and its SHAP factors show exactly why. |
| 1:30 | Switch the whole interface to Tamil or Hindi. Officials can question the lake by voice, in their own language. |
| 1:38 | *[live Tamil question]* |
| 1:42 | The answer comes back in Tamil, grounded in live data, with every tool call shown. |
| 1:50 | One click writes community alerts in English, Tamil and Hindi. Another builds a regulator-ready PDF: data tables straight from storage, plus Gemini's narrative. |
| 2:05 | And every claim is measured against the simulator's hidden truth: source-location error in metres, detection precision and recall, and end-to-end latency. |
| 2:18 | It runs on phones and tablets, in every language. AquaSentinel: it doesn't just measure water quality, it explains it, and acts on it. |

**Delivery.** Calm and confident, mid tempo. Record it after the picture edit is locked, reading against the cut.

**Music** is optional: a light ambient or electronic bed about 20 dB below the voice. Mute it under the two spoken commands.

---

## 3. Recording instructions

### A. Before recording (allow 30 minutes)

1. **Gemini quota (critical).** One full run uses roughly 25–35 Gemini requests, and the report narrative alone is about 10. You need a rehearsal run plus a recording run. The free tier hit its limit at 20 a day on one model, so turn on billing for the key's project in AI Studio before you start. Without billing, do one rehearsal of Scenes 2–8 the day before and record the next day with fresh quota.
2. **Model:** `.env` has `GEMINI_MODEL=gemini-3.5-flash-lite`. Note the name for the caption in Scene 5.
3. **Start:** `./run.sh`, then open `http://localhost:5173` in **Chrome**. Chrome is required for the browser speech recogniser in Scenes 2 and 7.
4. **Browser:**
   - Use a fresh Chrome profile with no extensions or bookmarks bar.
   - Set page zoom to 110%, window full-screen at 1920×1080.
   - Use the light theme (the sun/moon button).
   - Language: English.
5. **Microphone permission:** in a rehearsal, click the mic once and choose "Allow", so the permission prompt never appears on camera. Turn on macOS Do Not Disturb and hide the Dock.
6. **Rehearse the whole sequence once** (Scenes 2–9). It checks four things that have never been run against real Gemini:
   - the Tamil answer
   - the alerts
   - the report narrative
   - the Planner's south-west route
7. **Reset for the real take.** Click **Reset demo** in the Scenario card. Then:
   - Demo speed → **16×**.
   - Re-plan after anomaly → **Gemini planner**.
   - Discharge began → **12 h ago**.
   - Do **not** click a scenario yet.
8. Wait about 90 s, so the baseline forms, the charts fill and the first zone health run completes. Then start recording.

### B. Exact recording sequence

Record the main run as **one continuous take** (about 5–8 minutes of real time) and cut it down later. The app's state carries forward between scenes, so separate takes would not line up.

1. Wait 10 s on the Live page. **Scene 1**
2. Mic → say "Survey the south-west zone and check conductivity." → Send → wait for the reply and the new route. **Scene 2**
3. Click **Industrial discharge**. **Scene 3**
4. Keep your hands off. Watch until the red **!** appears and the diagnosis card fills, which can take up to 2 minutes. **Scenes 4–5**
5. Scroll the diagnosis card, look at the camera card, tick **show true source**. **Scene 5**
6. Zone health → SW card. **Scene 6**
7. Language → தமிழ் → Ask the Lake → mic → Tamil question → Send → expand tool calls. **Scene 7**
8. Reports & alerts → anomaly #1 → Write alerts → Generate report → PDF → scroll. **Scene 8**
9. Language → English → Benchmarks. **Scene 9**
10. Stop recording.

**Second take (Scene 10).**
1. Open Chrome DevTools device mode (Cmd+Opt+M) and pick iPhone 14 Pro Max.
2. Load `http://localhost:5173/?lang=hi&theme=dark#live`.
3. Record about 20 s: scroll once, tap ज़ोन, tap लाइव.
4. Crop to the device frame in the edit.

**Recorder.** Screen Studio, which adds automatic zooms and smooths the cursor, or OBS at 1080p60 plus any editor. Turn on cursor highlight and click rings. Record the microphone during takes so the two spoken commands are captured.

### C. Editing rules

- **Allowed:** jump cuts that remove waiting, and time-lapse with a visible `Time-lapse` caption. Never change on-screen text, numbers or results.
- **Speed:** Scene 4 at about 4×. PDF scroll at 2×. Everything else at real speed.
- **Zooms:** 1.3–1.5×, eased over about 0.4 s, at most one zoom move per action.
- **Transitions:** straight cuts between scenes. A 0.2 s cross-dissolve only at page changes. No wipes or slides.
- **Captions:** one style throughout, small white text on a dark pill at the bottom-left, at most about 6 words, each shown 2–4 s.

### D. Delays and errors, handled honestly

| Situation | What to do |
|---|---|
| Gemini is slow | Keep recording. Cut the wait in the edit. |
| "Gemini busy (503)" | The app already retries. If an error still shows: for a diagnosis, click **Diagnose again**; for anything else, re-record that scene. |
| 429 quota error | Stop. Enable billing or wait for the quota reset. Do not fake output. |
| Speech not recognised | Click the mic and say it again. On a second failure, type the words and change the narration to "in plain language". |
| Detection is slow | It depends on where the boat is. Keep recording and use more time-lapse. |
| Gemini names a different cause, or SW is not Stressed | Narrate what is on screen. If SW is Healthy, re-check that "Discharge began" was 12 h ago. |

---

## 4. Feature coverage checklist

**Covered in the video**
- [x] Live telemetry: six parameters, KPI tiles with CPCB status chips, charts, map trail coloured by value
- [x] Voice input in English (browser speech recognition)
- [x] Gemini Planner: natural language → waypoints, with the on-water route check
- [x] Pollution scenario simulator with hidden ground truth
- [x] Streaming anomaly detection (flagged readings, red rings)
- [x] Gemini re-plan decision → onboard plume tracing → shoreline search → source estimate
- [x] Gemini Visual Inspector (camera chips in the diagnosis) on a simulated frame
- [x] Gemini Diagnostician: cause, confidence, evidence, against, CPCB, actions, lab tests
- [x] True-source check on the map
- [x] Your LightGBM fish-health model per zone, with TreeSHAP factors and the 24 h timeline
- [x] Multilingual interface (Tamil, then Hindi on the phone)
- [x] Ask the Lake by Tamil voice, with a Tamil answer and visible tool calls
- [x] Community alerts in English, Tamil and Hindi
- [x] PDF report with stored-data tables and a Gemini narrative
- [x] Benchmarks measured against the simulator
- [x] Phone layout and dark theme

**Built but left out for time**
- Inspecting an uploaded photo with the Visual Inspector
- Preset surveys, pause and dock, demo-speed and re-plan-mode controls (only set up off-camera)
- The 8-frame Visual Inspector benchmark run
- Telemetry time-window switch; tablet layout
- Real-boat endpoints: `/api/ingest`, camera upload, waypoint polling
- Gemini transcription fallback for browsers without speech recognition
- Docker / Cloud Run packaging

**Not built, so never mentioned:** BigQuery, Pub/Sub, Firestore, Firebase Auth, SMS or push sending, field-trial results, the boat on real water.

---

## 5. Final quality checklist

- [ ] Runtime exactly **2:30.00**, with the first product frame at 0:00
- [ ] No title card, problem slide, team slide, architecture slide or outro card
- [ ] "Simulated" is visible or spoken in Scenes 1, 3, 5 and 9; time-lapse is labelled in Scene 4
- [ ] Every number and result on screen is unedited app output
- [ ] Narration matches what is on screen, especially the cause in Scene 5 and the SW status in Scene 6
- [ ] Both spoken commands are audible and legible as they transcribe
- [ ] Text is readable at 1080p: zoom in on the cards and tables
- [ ] No permission prompts, notifications, DevTools panels, error toasts or personal tabs in frame
- [ ] The API key never appears; `.env` is never opened on screen
- [ ] Voice peaks around -3 dB and the music bed stays about 20 dB below the voice
- [ ] Captions use one consistent style and stay on screen for at least 2 s
- [ ] Exported at 1080p H.264, 30 or 60 fps, and watched once at full screen and once on a phone

## Verification

Before the real take, run the rehearsal in step A6 end to end. It confirms that every scene's expected result appears with the current model and quota. Then check the cut against the quality checklist above.
