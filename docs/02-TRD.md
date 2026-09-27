# TRD - Technical Requirements Document

**Project:** ORBITAL-HAR · SIH26174 · Team Hashira
**Version:** 1.0 · 2026-09-20
**Supersedes:** the stack summary in [architecture.md](architecture.md)

---

## 1. Hardware profiles

| Profile | Spec | Role |
|---|---|---|
| **DEV** | Ryzen 7, NVIDIA RTX laptop GPU *(exact model + VRAM to be confirmed - sets batch size)*, ~24 GB RAM, 150 GB free | Primary development and the fallback demo machine |
| **TRAIN** | College GPU lab | Synthetic dataset renders, full training runs, hyperparameter sweeps |
| **EDGE** | Jetson Orin Nano 8 GB *(not yet procured - see implementation plan gate M4)* | Target deployment; credibility demo |
| **CAM** | USB UVC webcam, 1080p30, fixed mount, manual focus preferred | Capture |

**Fallback position.** If EDGE is not procured, the demo runs on DEV with power and latency
measured and published, plus a documented extrapolation. The architecture does not change -
only the export target does.

### 1.1 Storage budget (150 GB ceiling is a real constraint)

| Item | Budget |
|---|---|
| Python env, CUDA, PyTorch | 15 GB |
| Blender + scene assets | 5 GB |
| Real footage (50 runs × ~4 min, H.264) | 15 GB |
| Synthetic renders (JPEG q90, 720p) | 3 GB |
| Derived training sets | 5 GB |
| Model checkpoints and experiments | 10 GB |
| Dev session recordings | 20 GB |
| **Subtotal** | **73 GB** |
| Reserve | 77 GB |

**Rules.** Render to JPEG, never PNG. Keep only the best two checkpoints per experiment;
prune weekly. Session recordings older than 14 days are deleted unless tagged `keep`.
Raw footage is the only irreplaceable asset - back it up to the college lab or external
storage.

---

## 2. Software stack (pinned)

| Component | Package | Version |
|---|---|---|
| Runtime | Python | 3.11.x |
| Env manager | uv | latest |
| Detection / pose | `ultralytics` | 8.3.x |
| Hands | `mediapipe` | 0.10.x |
| Vision utils, ArUco | `opencv-contrib-python` | 4.10.x |
| Tensors | `torch`, `torchvision` | 2.4.x + CUDA 12.1 |
| Config schema | `pydantic` | 2.9.x |
| YAML | `pyyaml` | 6.x |
| API | `fastapi`, `uvicorn[standard]` | 0.115.x / 0.31.x |
| DB | `sqlite3` (stdlib) + `sqlalchemy` | 2.0.x |
| TTS | `piper-tts` | 1.2.x |
| ASR (optional) | `vosk` | 0.3.x |
| Video | FFmpeg (system binary) | ≥6.0 |
| RTSP server | MediaMTX | ≥1.9 |
| Frontend | React + Vite + TypeScript | 18.x / 5.x |
| Tests | `pytest`, `pytest-asyncio` | 8.x |
| Lint / format | `ruff` | 0.6.x |

**Prohibited at runtime:** any HTTP client call to a non-loopback address, any CDN-hosted
asset, any hosted inference or TTS API. CI enforces this (§12.4).

---

## 3. Module decomposition

| Module | Package | Responsibility | Must not |
|---|---|---|---|
| Capture | `perception.capture` | Frame acquisition, timestamping, back-pressure | Know about procedures |
| Rack frame | `perception.rackframe` | ArUco detection, 6-DoF rack pose, canonicalizing warp | Know about procedures |
| Detector | `perception.detect` | Object + state detection | Know about procedures |
| Hands | `perception.hands` | Hand landmarks, contact inference | Know about procedures |
| Pose | `perception.pose` | Body keypoints in rack frame | Know about procedures |
| Bus | `core.bus` | Event publish / subscribe / persist / replay | Contain domain logic |
| Procedure | `reasoning.procedure` | Schema load, validation, predicate compilation | Touch frames |
| Engine | `reasoning.engine` | Step state machine, confidence, verdicts | Touch frames |
| Voice | `runtime.voice` | TTS synthesis and playback, ASR commands | Decide verdicts |
| Telemetry | `runtime.telemetry` | Hash-chained JSONL writer + verifier | Decide verdicts |
| Video out | `runtime.videoout` | RTSP publish, segmented local recording | Decide verdicts |
| Store | `runtime.store` | SQLite persistence | Decide verdicts |
| Server | `server.app` | FastAPI, WebSocket fan-out, static assets | Contain domain logic |
| UI | `ui/` | React dashboard | Contain domain logic |

**Architectural invariant.** Perception modules never import from `reasoning`, and
`reasoning` never imports from `perception`. They communicate only through `core.bus`.
A CI import-linter rule enforces this.

---

## 4. Event bus contract

Single append-only stream. One JSON object per line. This is the seam that makes replay,
parallel development, and demo insurance possible.

### 4.1 Envelope

```json
{"t": 1758374512.482, "seq": 10423, "src": "detect", "type": "detection", "v": 1, "payload": {}}
```

| Field | Type | Meaning |
|---|---|---|
| `t` | float | Unix epoch seconds, capture time, not publish time |
| `seq` | int | Monotonic, gap-free per session; gaps indicate dropped frames |
| `src` | string | Producing module |
| `type` | string | Discriminator for `payload` |
| `v` | int | Payload schema version |

### 4.2 Event types

| Type | Producer | Payload |
|---|---|---|
| `frame` | capture | `{frame_id, w, h, dropped}` |
| `rack` | rackframe | `{found, markers[], rotation_deg, quat[4], translation_mm[3], quality}` |
| `detection` | detect | `{frame_id, objects[{cls, conf, bbox[4], track_id, centroid_mm[3]}]}` |
| `hand` | hands | `{frame_id, hands[{side, landmarks[21][3], conf}]}` |
| `contact` | hands | `{frame_id, pairs[{hand, object_track_id, conf}]}` |
| `pose` | pose | `{frame_id, keypoints[17][3], frame_ref: "rack", conf}` |
| `step_state` | engine | `{step_id, state, confidence, evidence[], reason}` |
| `alert` | engine | `{kind, severity, step_id, expected_step_id, message}` |
| `crew_action` | server | `{action, step_id, actor: "crew"}` |
| `system` | any | `{level, component, message, metrics{}}` |

`alert.kind` ∈ `skip` · `out_of_order` · `wrong_object` · `wrong_hand` · `stall` · `unverified` · `free_float` · `degraded`

A `wrong_object` alert names the step the object belongs to in `step_id` and the step
the operator should be doing in `expected_step_id` (§7.7).

### 4.3 Replay

Any recorded stream is replayable at 1×, fast, or stepped, into an engine instance with no
camera and no models loaded. **Replay is a first-class mode, not a test harness** - it is
the demo fallback if the camera fails in front of the jury.

---

## 5. Procedure definition schema

Validated by Pydantic on load. Invalid files are rejected with the offending field path
(FR-11); the system must never start on a partially valid procedure.

### 5.1 Structure

```yaml
procedure:
  id: string                    # stable identifier
  name: string
  version: int
  rack_markers: string          # ArUco dictionary name
  vocabulary: string            # object vocabulary this procedure requires

markers: [string]               # rack fiducials referenced by name; validated

objects:
  - id: string
    classes: [string]           # detector classes representing this object's states
    tether_required: bool       # optional, drives free-float advisory

regions:                        # optional; required by `dwell`
  - id: string
    rect: [x0, y0, x1, y1]      # fractions of frame width/height

groups:                         # optional order-independent sets
  - id: string
    members: [step_id]

steps:
  - id: string
    name: string
    voice: string               # spoken prompt
    preconditions: [step_id]
    group: string | null
    requires: [predicate]       # ALL must hold
    any_of: [predicate]         # optional; ANY may satisfy
    timeout_s: int | null
    on_timeout: stall | skip | ignore
```

### 5.2 Predicate types

Written compactly: the predicate's type is the key, its primary argument is the value,
and everything else is a sibling key.

| Predicate | Form | Semantics |
|---|---|---|
| `detect` | `detect: <class>` + `min_conf, hold_frames, min_area` | Class present above the floor for N consecutive frames; `min_area` (fraction of the frame) makes it "held up to the camera" in a scene procedure |
| `absent` | `absent: <class>` + `min_conf, hold_frames` | Class not present for N frames |
| `contact` | `contact: [a, b]` + `min_conf, hold_frames, side` | Hand-object or tool-object contact; `side: left \| right` requires that hand |
| `moved` | `moved: <object>` + `min_disp_mm` **or** `min_frac` | Displaced since step start: in rack millimetres (markers needed), or as a fraction of the frame (any webcam) |
| `tilted` | `tilted: <object>` + `min_ratio, min_conf, hold_frames` | Tipped over: box at least `min_ratio` (0.65) as wide as tall. With `contact`, that is pouring |
| `near` | `near: <object>` + `to, max_mm, hold_frames` | Within distance of `to`, which may be a **marker or another object** |
| `dwell` | `dwell: <object>` + `region, seconds` | Held inside a declared region for a duration |
| `count` | `count: <class>` + `n, min_conf, hold_frames` | Exactly N instances present |
| `gesture` | `gesture: <name>` + `side, min_conf, hold_frames` | A body action held for N frames, read from pose in the body's own frame (list below) |

Gestures are orientation-free by construction: *up* is the hips-to-shoulders axis (or shoulders-to-head when the hips are out of frame), *across* runs from the right shoulder to the left, and distances are in shoulder widths, so an operator working inverted raises a hand exactly as one standing does. Unknown gesture names are refused at load.

| Kind | Gestures | Read from |
|---|---|---|
| One hand (`side: left \| right`) | `hand_raised`, `hand_to_face`, `hand_on_head`, `reaching` | One frame |
| Both hands / body | `both_hands_raised`, `hands_together`, `arms_crossed`, `arms_out`, `hands_on_hips` | One frame |
| Movements | `waving`, `lifting`, `lowering` (one hand); `clapping` (both) | The last 2-2.5 s of wrist positions in body coordinates |

`hands_on_hips` also needs both knees in view: seated at a desk the pose model guesses hips under the table edge, and hands resting on the desk then look exactly like hands on hips (170 desk photos, no knee ever above 0.1 confidence). Movements need frame rate: at the 3-4 FPS a laptop CPU gives with pose and detection, a wave or clap must be slow and wide. A movement made while holding an object is `contact` plus the movement - "lift the bottle with your right hand" is `contact: [hand, bottle], side: right` + `gesture: lifting, side: right`.

The stock detector loses a bottle once it is tipped past ~30° (1 of 10 rotated photos found at 30-45°, none at 60-90°), so a reliable `tilted` needs the object trained on the device with pouring photos.

Every predicate also accepts `latch: bool` (default false). A latched predicate, once
satisfied during a step activation, stays satisfied for the remainder of that activation.
This is required for transient evidence: *"the operator did pick up the red box"* is true
of the step even though the hand is long gone by the time the box reaches its marker.
Latching is per-activation state and is held by the engine, not the evaluator.

**`min_conf` is a detection floor, not a decision threshold.** It asks whether the evidence
exists at all; whether it is good enough to act on is the engine's call via `τ_complete`
and `τ_abstain` (§7.3). Setting `min_conf` above `τ_abstain` makes the abstention path
unreachable - a predicate could never be both satisfied and too weak to trust. The default
is 0.35, matching the detector's publish floor.

Predicates are pure functions of the last W seconds of the event stream. They are
side-effect free and independently unit-testable - this is what makes the engine
developable with zero ML present.

---

## 6. Perception specification

### 6.1 Rack frame and canonicalization

1. Detect ArUco markers (`DICT_4X4_50`) on the rack plane each frame.
2. Solve rack pose via `solvePnP` against the known marker layout.
3. Derive the in-plane rotation θ between rack-up and image-up.
4. **Rotate the frame by −θ before any inference runs.**
5. Publish the rack event including `quality` (markers found / expected).

**Rationale (do not "optimise" this away).** Pose and detection models are trained almost
entirely on gravity-aligned imagery. An inverted operator is not detected at all, so
rotating model *output* into rack coordinates fixes nothing - there is no output. Rotating
the *input* restores the models' training distribution. This is the entire orientation
differentiator, and it costs one warp.

When fewer than two markers are visible, hold the last good rotation for up to 2 s, then
publish `alert.degraded` and fall back to identity.

### 6.2 Object detection

| Parameter | Value |
|---|---|
| Model | YOLO11s (DEV/EDGE), YOLO11m for accuracy comparison |
| Input | 640×640 letterboxed |
| Classes | See appendix A |
| Confidence floor | 0.35 publish, 0.60 predicate default |
| NMS IoU | 0.5 |
| Tracking | ByteTrack, for stable `track_id` across frames |
| Export | PyTorch → ONNX → TensorRT FP16 for EDGE |

Object **states are modelled as distinct classes** (`red_box_open` vs `red_box_closed`),
not as a downstream classifier. This collapses a subsystem into the detector and makes
predicates trivial.

**What is reported depends on the procedure.** A *scene* procedure - rack markers, or any
`contact`, `near`, `moved`, `dwell` or `count` predicate - gets every object in view: a
bottle standing in its home spot is small in frame and must still count. Any other
procedure is a *presentation* ("show the bottle to the camera"): only objects filling at
least `min_area` of the frame (default 6 %) are reported, every one of them, so two objects
can be shown at once and a book lying at the back of the desk is not "presented". The
largest rejected candidate is drawn with a *hold closer* hint. `Procedure.is_scene` decides.

**Objects trained on this device join the stock ones.** A detector trained in the Models
tab runs beside the stock COCO-80 model rather than replacing it; each model is asked only
for the classes the loaded procedure wants, and a model nothing is wanted from is not run.
Where a trained class shares a stock name, the trained model answers for it. The last
trained detector is re-attached at start-up (`--no-trained` opts out). A whole-scene
classifier is different: it reports one label for the whole frame and does replace the
detector.

### 6.3 Hands and contact

MediaPipe Hands, max 2 hands, detection confidence 0.5, tracking confidence 0.5.

Contact is inferred, not learned in v1: a hand-object pair is in contact when any fingertip
landmark falls within the object bbox expanded by 8%, held for ≥3 frames. Revisit only if
measured precision is below 0.9.

### 6.4 Body pose

YOLO11-pose, 17 COCO keypoints, consumed from the already-canonicalized frame so output is
natively rack-relative. **MediaPipe Pose is deliberately not used** - its ARM support is
unreliable and it would not survive the EDGE port.

Full SMPL-based mesh recovery is explicitly out of scope for real time. If the richer 3D
visual is wanted for the demo, run it offline at ≤2 Hz as a side channel on recorded video.

---

## 7. Procedure engine specification

### 7.1 Step states

`PENDING` → `ACTIVE` → one of `COMPLETE` · `SKIPPED` · `OUT_OF_ORDER` · `UNVERIFIED` ·
`OVERRIDDEN` · `STALLED`

### 7.2 Transition rules

| From | To | Condition |
|---|---|---|
| PENDING | ACTIVE | All preconditions COMPLETE/OVERRIDDEN and it is the earliest such step |
| ACTIVE | COMPLETE | All `requires` predicates true for `hold_frames`, confidence ≥ τ_complete |
| ACTIVE | UNVERIFIED | Predicates true but confidence < τ_complete for > dwell window |
| ACTIVE | STALLED | `timeout_s` elapsed with no predicate progress |
| PENDING | SKIPPED | A later step reaches COMPLETE while this one is not complete |
| PENDING | OUT_OF_ORDER | Its predicates fire while its preconditions are unmet |
| any | OVERRIDDEN | Crew override received |

### 7.3 Confidence and abstention

Per-step confidence is the minimum of contributing predicate confidences, temperature-scaled
against a held-out calibration set. Thresholds: `τ_complete = 0.75`, `τ_abstain = 0.50`.

Below `τ_abstain`, the engine **must** publish `UNVERIFIED` and an `alert.unverified`. It
must never silently advance on weak evidence. This is a hard safety requirement (FR-18) -
a confidently wrong verdict is worse than no verdict.

### 7.4 Hysteresis

Every transition requires evidence stable for `hold_frames` (default 12 ≈ 0.4 s at 30 FPS).
This single parameter dominates false-alert rate and will consume the most tuning time.
It is per-predicate overridable.

### 7.5 Completion frontier

Only steps within `completion_lookahead` (default 2) of the frontier - the earliest step
still awaiting a verdict - are evaluated. Active steps are always evaluated.

This is not an optimisation. Without it, any step whose predicates happen to describe the
world's *resting* state fires on frame one: PROC-B's "close both boxes" is trivially true
before anyone has opened them, which would complete step 9 immediately and mark the whole
procedure skipped. **Ambient state is not evidence that the operator did something.**

The cost is that jumping more than the lookahead ahead goes undetected. That is the safer
failure: the system stays silent rather than inventing a verdict.

The live console sets the lookahead per run mode: **Clean 0** (only the current step is
judged) and **Strict 1** (the next step too, with `strict_preconditions`). A step done
before its turn is therefore only *verdicted* in Strict mode; §7.7 is what catches it in
both.

### 7.6 Completion and preconditions

By default a step completes when its evidence says it happened, whether or not its
preconditions were met. The system reports what it observed; it does not refuse to believe
its own eyes. Precondition violations surface as **skip alerts on the steps that were
passed over**, which is what the crew actually needs to hear.

An `out_of_order` alert is reserved for genuine sequence inversion - a completing step whose
unmet precondition sits *later* in the procedure. Doing step 5 with step 4 undone is one
mistake and raises one alert, not two. Alert noise is the fastest route to a muted
assistant (NFR-04).

Setting `strict_preconditions: true` gates completion instead: the step enters
`OUT_OF_ORDER` and re-evaluates once its preconditions are satisfied.

### 7.7 Wrong object

Handling is not resting state. When the operator picks up an object that only a step
beyond the lookahead uses, the engine raises `wrong_object` at once - "Wrong object: the
phone is for step 3. Now: Pick up the bottle." Handled means a hand on it; in a
presentation procedure, being presented is handling it. The rules that keep it quiet:

- **Advisory.** No step changes state; putting the phone down is the right recovery.
- **Earlier objects are fine.** Anything a step up to the lookahead uses is allowed - the
  bottle is still in hand after "pick up the bottle". Objects the lookahead will judge are
  left to its verdict, so one mistake raises one alert.
- **It must appear.** Something in view since the step began (plus a 2 s grace for the
  camera settling) is the resting scene, not an action - the bottle standing at home at the
  start of a run is the last step's state, not a skip.
- **Held, then rationed.** Six consecutive frames of evidence, the right object not also in
  hand, and one alert per step and object per 10 s.

`EngineConfig.wrong_object_*` tunes or disables it. Objects no step uses at all are not
alerted: the engine only knows what the procedure names.

### 7.8 Wrong hand

A step whose `gesture` or `contact` names a `side` raises `wrong_hand` (severity medium)
when the other hand does it for six frames and the named hand is not doing it too - "Wrong
hand: use your left hand. Now: Raise your left hand". Advisory, one per step per 10 s, and
only for what started during the step: the right hand still up from the previous step
("raise your right hand") is not a mistake. `EngineConfig.wrong_hand_*` tunes or disables
it. The line is pre-rendered at load, since the step fixes its wording.

---

## 8. Voice specification

| Aspect | Spec |
|---|---|
| Engine | Piper, `en_US-lessac-medium` |
| Prompt synthesis | Fixed procedure prompts pre-rendered to WAV at load (FR-34) |
| Dynamic speech | Synthesized on demand; only for non-fixed content |
| Routine tone | Normal rate, normal pitch |
| Alert tone | Faster rate, raised pitch, preceded by a 200 ms attention tone |
| Latency budget | ≤400 ms from verdict to audio start |
| Interruption | Alerts pre-empt routine prompts; routine prompts queue |
| Mute | Crew-controllable; mute state is logged |
| ASR (optional) | Vosk small model, fixed grammar: `repeat`, `next`, `mark anomaly`, `override` |

---

## 9. Telemetry specification

### 9.1 Record format

```json
{"seq":42,"t":"2026-09-20T13:24:51.482Z","ev":"step_complete","step":"s3",
 "conf":0.91,"dur_s":18.4,"evidence":["detect:red_box_open","contact:hand,red_box"],
 "prev":"a3f1…","hash":"9b2c…"}
```

### 9.2 Hash chain

```
hash_n = SHA256( prev_hash_n || canonical_json(record_n without `hash`) )
prev_hash_0 = "0" * 64
```

Canonical JSON: sorted keys, no whitespace, UTF-8. The verifier walks the file and reports
`OK` or the sequence number of the **first** broken link.

### 9.3 Size discipline

Short keys, no pretty-printing, no redundant fields, one record per state transition -
never per frame. Budget ≤50 KB per procedure hour (FR-43). The dashboard displays live
bytes written alongside the equivalent raw-video figure at 8 Mbps (FR-47).

---

## 10. Video output specification

| Aspect | Spec |
|---|---|
| Local recording | FFmpeg, H.264, 60 s segments, `sessions/<id>/video/seg_%05d.mp4` |
| Crash safety | Segmented output; a kill loses at most one segment |
| Live stream | FFmpeg → MediaMTX → RTSP at a configured host:port |
| Dashboard preview | MJPEG over loopback HTTP - chosen for reliability under demo conditions, not efficiency |
| Overlay | Rendered into the dashboard preview only; recorded and streamed video stay clean |

---

## 11. Performance budgets

Per-frame at 1280×720 on DEV. Perception stages run concurrently, so the frame budget is
the max of the parallel branches, not their sum.

| Stage | Budget |
|---|---|
| Capture + canonicalize | 6 ms |
| Detection | 18 ms |
| Hands | 12 ms |
| Pose | 14 ms |
| Predicate evaluation | 3 ms |
| Bus publish + persist | 2 ms |
| **Frame total** | **≤45 ms (≈22 FPS)** |

EDGE targets, to be measured not assumed: ≥15 FPS, ≤4 GB RAM, ≤12 W.

**Degradation ladder** when the budget is exceeded: drop pose to every 3rd frame → drop
hands to every 2nd frame → reduce detector input to 512 → reduce capture to 15 FPS. Each
step is logged as `alert.degraded` and shown in the dashboard. The system must never
silently degrade.

---

## 12. Quality and testing

### 12.1 Layers

| Layer | Scope | Tool |
|---|---|---|
| Unit | Predicates, hash chain, schema validation, state transitions | pytest |
| Golden replay | Recorded sessions → expected verdict sequence | pytest + fixtures |
| Integration | Bus → engine → telemetry → WebSocket | pytest-asyncio |
| Perception | mAP on held-out set, upright and rotated | Ultralytics val |
| Soak | 2 h continuous run, memory and FPS traced | scripted |

### 12.2 Golden replay corpus

Minimum ten recorded sessions committed as fixtures, each with an expected verdict
sequence: three clean runs, two with skips, two out-of-order, one occluded, one inverted,
one with a stall. **The engine is developed against this corpus before real perception
exists**, and every corpus case must pass before any release tag.

### 12.3 Calibration set

A held-out set never used for training, used solely to fit temperature scaling for §7.3.

### 12.4 CI gates

`ruff` clean · all tests pass · import-linter boundary rule (§3) · **offline check: a test
runs the full pipeline with outbound non-loopback sockets blocked and asserts success**.

---

## 13. Packaging and deployment

Single entry point `python -m orbital_har`, config via `config/runtime.yaml` and env
overrides. Model weights bundled, not downloaded. Cold start ≤30 s (FR-62). Frontend built
to static assets and served by FastAPI from disk - no dev server, no CDN, in any
demo-facing configuration.

---

## Appendix A - Object vocabulary and procedures

Both procedures share **one vocabulary**, so loading the second requires **no retraining**.
This is what makes the D-02 demo honest.

### A.1 Detector classes (11)

`outer_box_closed` · `outer_box_open` · `red_box_closed` · `red_box_open` ·
`yellow_box_closed` · `yellow_box_open` · `sample_vial` · `tweezers` · `tether_clip` ·
`rack_surface` · `glove`

### A.2 Prop kit

Hinged outer container (neutral grey) · small red box with lid · small yellow box with lid ·
sample vial with coloured cap · tweezers · tether clips or velcro strips · rigid board as
rack surface · four printed ArUco markers at known spacing · tripod or clamp mount ·
coloured gloves (optional, improves hand segmentation).

All locally purchasable. Total expected cost is low; see the implementation plan.

### A.3 PROC-A - Nested sample retrieval (6 steps, the build target)

| # | Step | Key predicates |
|---|---|---|
| 1 | Open the outer container | `detect outer_box_open` |
| 2 | Remove the red box, place on marker R | `contact hand,red_box` + `near red_box,R` |
| 3 | Remove the yellow box, place on marker Y | `contact hand,yellow_box` + `near yellow_box,Y` |
| 4 | Open the red box | `detect red_box_open` |
| 5 | Transfer the vial to the yellow box using tweezers | `contact tweezers,sample_vial` + `near sample_vial,yellow_box` |
| 6 | Close the yellow box and secure it | `detect yellow_box_closed` + `near tether_clip,yellow_box` |

Natural failure cases for the corpus: skipping 4 and going to 5; doing 3 before 2.

### A.4 PROC-B - Dual-sample cross-transfer (10 steps, the live-load demo)

Same objects, different sequence, and it exercises features PROC-A does not: an
order-independent group, a dwell-based verification hold, and a multi-condition step.

| # | Step | Notes |
|---|---|---|
| 1 | Open the outer container | |
| 2 | Remove the **yellow** box, place on marker Y | reversed vs PROC-A |
| 3 | Remove the red box, place on marker R | |
| 4 | Open the yellow box | **group `g1`** - order-independent |
| 5 | Open the red box | **group `g1`** - order-independent |
| 6 | Transfer the vial from red to yellow with tweezers | |
| 7 | Hold the vial to camera for verification | `dwell` 2 s - new predicate type |
| 8 | Return the vial to the red box | |
| 9 | Close both boxes | two `detect` predicates ANDed |
| 10 | Return both boxes to the container and close it | |

---

**Related:** [PRD](01-PRD.md) · [App flow](03-APP-FLOW.md) · [UI/UX brief](04-UIUX-BRIEF.md) ·
[Backend schema](05-BACKEND-SCHEMA.md) · [Implementation plan](06-IMPLEMENTATION-PLAN.md)
