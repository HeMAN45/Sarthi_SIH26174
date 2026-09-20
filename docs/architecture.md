# SIH26174 — System architecture and build plan

Team Hashira · ISRO · AI Human Activity Recognition for On-board BAS Experiments

See [SIH26174-problem-statement.md](SIH26174-problem-statement.md) for the verbatim PS.

---

## 1. Design constraints

Every decision below is driven by four hard constraints from the PS:

1. **Offline standalone** — no cloud inference, no cloud TTS, no CDN assets at runtime.
2. **Edge hardware** — must run on a power- and compute-limited device.
3. **Real time** — continuous video processing with responsive alerts.
4. **Explainable** — a mission-critical system must be able to justify its decisions.

---

## 2. Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Runtime | Python 3.11 | Whole stack is Python-native |
| Object detection | Ultralytics YOLO11 | Clean TensorRT export path for Jetson |
| Hands | MediaPipe Hands | CPU-only, mature, fast |
| Body pose | YOLO11-pose | Survives the Jetson/ARM port; MediaPipe Pose is fragile there |
| Rack frame | OpenCV ArUco | 6-DoF marker pose, built in |
| Procedure schema | Pydantic + YAML | Validation and readable errors for free |
| TTS | Piper | Truly offline, small, natural, runs on a Pi |
| ASR (optional) | Vosk / faster-whisper | Offline command vocabulary |
| Backend | FastAPI + WebSocket | Live state push |
| Frontend | Vite + React (bundled) | Bundled, not CDN — the offline claim depends on this |
| Video I/O | FFmpeg + MediaMTX | RTSP out + segmented local recording |
| Synthetic data | Blender (`bpy`) | Free, scriptable, auto-generates labels |
| Edge target | Jetson Orin Nano 8GB | Demo credibility with an ISRO jury |

**Hard rule:** zero network calls at runtime. A single cloud dependency invalidates the
offline premise and a sharp judge will look for it.

---

## 3. Pipeline

```
Payload camera ──┐
                 ├──> Rack-frame canonicalizer ──> Perception ──> Event bus
Rack ArUco ──────┘                                (3 parallel)        │
                                                                      v
                                        Procedure YAML ──> Procedure engine
                                                                      │
                            ┌────────────┬──────────────┬─────────────┘
                            v            v              v             v
                          Voice    Telemetry log    Video out    Dashboard
```

### 3.1 Rack-frame canonicalization (the orientation differentiator)

ArUco markers on the payload rack yield a 6-DoF rack pose per frame, defining "up"
independently of gravity.

**Key insight:** do NOT run pose estimation and then rotate the output into rack
coordinates. Pose models are trained on gravity-aligned humans, so an inverted astronaut
is never detected in the first place — there is no output to rotate.

**Instead:** rotate the *input image* into the rack-canonical frame before it reaches any
model. The detector then sees a conventionally-oriented person. Orientation invariance for
the cost of a warp.

Add rotation augmentation during training as a second line of defence.

### 3.2 Perception layer

Publishes primitives, never conclusions. Three parallel consumers:

- object detections with states (see 4.1)
- hand keypoints + hand-object contact flags
- rack-relative body pose

None of them know anything about the experiment.

### 3.3 Event bus — build this first

Perception publishes a timestamped JSONL stream; the procedure engine consumes it.

Three payoffs that justify the seam on day one:

- **Replay** — record a session once, replay it a thousand times while tuning.
- **Parallel work** — reasoning layer and dashboard can be built against a mocked stream
  with no camera and no models.
- **Demo insurance** — if the camera fails during the finale, replay a recorded session
  and keep talking.

### 3.4 Procedure engine

Lightweight temporal model smooths per-frame evidence into step probabilities. A
**deterministic** state machine consumes those with hysteresis and emits:
`in_progress` / `complete` / `skipped` / `out_of_order`.

Deterministic is a requirement, not a preference — "why did it say that?" needs an answer.

---

## 4. Procedure-as-config

All experiment-specific knowledge lives in one YAML file. The engine never changes.
This is what converts the project from a single-experiment demo into a platform.

```yaml
procedure:
  id: nested_box_sample
  name: Nested box sample retrieval
  rack_markers: aruco_4x4_50

objects:
  - id: outer_box
    classes: [outer_box_closed, outer_box_open]
  - id: red_box
    classes: [red_box_closed, red_box_open]
  - id: sample_vial
    classes: [sample_vial]

steps:
  - id: s1
    name: Open the outer container
    voice: "Open the outer container."
    preconditions: []
    requires:
      - detect: outer_box_open
        min_conf: 0.6
        hold_frames: 12
    timeout_s: 60

  - id: s2
    name: Remove the red box
    voice: "Remove the red box and secure it on the rack."
    preconditions: [s1]
    requires:
      - contact: [hand, red_box]
      - moved: red_box
        min_disp_mm: 80
```

### 4.1 Object states as detection classes

Model `outer_box_closed` / `outer_box_open` as two distinct YOLO classes rather than
building a separate state classifier. Collapses a subsystem into the existing detector and
makes predicates trivial.

### 4.2 `hold_frames`

Prevents state flicker on a single bad frame. This is the parameter you will spend the
most tuning time on. Budget for it.

---

## 5. Training plan

| Stage | Work | Notes |
|---|---|---|
| 1 | Define the physical experiment | Colour-coded nested boxes + vial + ArUco rack. Keep it simple — the intelligence is in sequence tracking, not task difficulty. |
| 2 | Record 30–50 real runs | Vary performer, lighting, body orientation. **Record failure runs too** — skips, wrong order, hesitation. You need negatives. |
| 3 | Auto-label, then correct | Open-vocab detector pre-labels offline; correct in CVAT / Label Studio. Turns a week into an afternoon. |
| 4 | Scale with Blender | Randomize camera pose, placement, lighting, textures, distractors, and body orientation **across the full sphere**. Labels free from the renderer. |
| 5 | Train + export | Fine-tune YOLO11 from COCO weights → ONNX → TensorRT engine. Train step model on labelled sequences. |

**Target volume:** ~2,000 synthetic + ~800 real frames. Controlled scene, fixed camera,
<12 classes — small-data regime works here.

---

## 6. Output surfaces

**Crew HUD** — what the astronaut sees. One large next-step card, current step, progress
strip. Nothing else. Legible across a room.

**Ground ops view** — what the jury studies. Live video with detection overlays, step
timeline with durations, confidence traces, state machine state, live telemetry byte
counter.

**Artifacts** — hash-chained JSONL log + segmented local video. Demo the chain verifying,
and show the byte count against equivalent raw video size.

The contrast between the two views *is* the mission-realism story.

---

## 7. Voice

**TTS (required by PS).** Piper. Two distinct behaviours: calm prompt on step completion /
next-step suggestion, and an unmistakably different urgent tone for skip or out-of-order.

Pre-synthesize fixed procedure prompts to WAV at load time — real-time synthesis on a
Jetson adds latency in a safety alert path.

**ASR (optional differentiator).** Scope tightly: 3–4 commands only — `repeat`, `next`,
`mark anomaly`, `override`. Gloved astronauts with both hands occupied make this
defensible rather than gimmicky, but only if it works perfectly. Keep the vocabulary small.

---

## 8. Repo structure

```
SIH26/
├── docs/
├── procedures/       # YAML procedure definitions
├── datagen/          # Blender synthetic pipeline
├── perception/       # detection, hands, pose, rack frame
├── reasoning/        # step model + state machine
├── runtime/          # tts, asr, streaming, logging
├── server/           # FastAPI + websocket
├── ui/               # dashboard frontend
├── models/           # weights and TensorRT engines
├── sessions/         # recorded event streams for replay
└── scripts/
```

---

## 9. Build order

Ordered so something is always demoable:

1. Repo, env, procedure schema with Pydantic validation
2. **Event bus + session replay** — before any ML
3. State machine against mocked events (testable, no camera)
4. YOLO on a small real dataset; wire real perception into the bus
5. Voice, hash-chained logging, RTSP streaming
6. Dashboard, both views
7. ArUco rack frame + input canonicalization — the orientation demo
8. Blender pipeline, scale dataset, retrain
9. Jetson port, TensorRT, measure FPS and watts
10. Demo rehearsal

**Steps 2–3 before step 4 is the part teams get wrong.** The state machine is the hardest
logic in the project and needs zero ML to develop. Get it correct against synthetic events
while someone else is still collecting footage.

---

## 10. Immediate next steps

Idea submission deadline is **30 September 2026**. Split the team — do not serialize:

- 2 people → idea submission write-up
- 2 people → technical spike below

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
uv init --python 3.11 && uv add ultralytics mediapipe opencv-contrib-python fastapi uvicorn websockets pydantic pyyaml piper-tts
```

**First milestone (2–3 days):** a working state machine driven by a hand-written JSONL
event file, printing `step 2 complete, next: remove the red box` and `step 4 skipped` to
the terminal. No camera, no models, no dashboard.

If that works, the spine is sound and everything else hangs off it.

**In parallel:** buy the props, record the first ten runs. Footage collection is the long
pole and cannot be compressed later.
