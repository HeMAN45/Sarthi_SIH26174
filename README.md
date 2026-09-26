# SARTHI

**S**atellite **A**ssistant for **R**esearch, **T**actical **H**olistic, & **I**nterface

On-board procedure supervision for BAS experiments.
Smart India Hackathon 2026 · **SIH26174** · ISRO / Department of Space · **Team Hashira**

A fixed payload camera watches an astronaut execute a scientific experiment. The system
knows the expected step sequence, recognises what is actually being done, prompts the next
action, raises a voice alert on a skipped or out-of-order step, and emits a few kilobytes
of verified telemetry instead of a video downlink.

Everything runs offline on an edge device. No cloud, no network, no exceptions.

---

## Quickstart

```bash
uv sync --group dev
```

### Voice (optional, but it is how alerts are meant to be heard)

```bash
uv pip install piper-tts
uv run python -m piper.download_voices en_US-lessac-medium --data-dir models/voices
```

Speech then runs **on the device**: every prompt a procedure can utter is
synthesized to WAV when the procedure loads, so an alert only has to play a
file. Alerts pre-empt step prompts. Without a voice model the dashboard falls
back to browser speech and says so on screen — which also means alerts are only
heard while a browser tab is open, so install the voice for anything
demo-facing. The model is a ~60 MB local file; nothing reaches the network at
run time.

### 1. Live web dashboard (recommended)

```bash
uv run python scripts/web_demo.py
```

Open **http://localhost:8000**. The console opens in **Ready** with the camera off.
**New run** powers the camera, opens a hash-chained telemetry log and starts judging the
procedure; **End run** seals the log and releases the camera. A local FastAPI server runs
the camera → detection → reasoning-engine loop and streams the annotated video (MJPEG)
plus live state (WebSocket) to the built dashboard.

| Flag | Effect |
|---|---|
| `--world` | use **YOLO-World** open-vocabulary detection (any object you can name) |
| `--procedure demo_live4` | 4-step demo instead of 3 |
| `--autostart` | start a run at launch instead of waiting for **New run** |
| `--no-mirror` | show the camera's true view; mirrored (selfie-style) is the default |
| `--camera 1` | pick a different camera |
| `--port 8000` | change the port |

### 2. Desktop (OpenCV) demo

```bash
uv run python scripts/live_demo.py
```

Keys: `k` skip current step · `r` restart · `o` out-of-order run · `s` summary · `q` quit.
Add `--preview` to see what class the detector assigns to each object.

### 3. No camera at all — the reasoning spine

```bash
uv run orbital-har validate proc_a
uv run orbital-har demo proc_a_skip_s4
uv run pytest
```

---

## What the dashboard can do

Four sections, switchable with keys `1`–`4` (`M` mutes, `Esc` acknowledges an alert).
Three themes from the palette button — Graphite (default), Slate and Daylight — and a
**Mirror** toggle on the camera: a webcam facing you moves the wrong way unless mirrored.
Mirroring changes the picture only; detection and every coordinate use the true view.

**Mission** — the live console. Camera with a heads-up display, the current step with its
spoken prompt, a rolling confidence trace drawn against τ-complete and τ-abstain, the
live evidence checklist, the procedure timeline, and the downlink / telemetry / clock /
perception meters. Alerts take over the top of the feed until acknowledged. When a run
completes or is ended, a **debrief** shows the verdict, per-step timing, the sealed
SHA-256 chain head and the downlink ratio.

**Procedures** — run any procedure in the `procedures/` library, or compose a quick
sequence from objects the detector already knows. A procedure the loaded detector cannot
perceive is refused with the missing classes named; **Run anyway** is an explicit choice.
With `--world` you can type *any* object name, not just a fixed list.

**Models** — teach it new objects and states in five stages: **Classes → Capture → Train
→ Test → Deploy**. Pick the procedure you are training for and it creates the classes and
shows which step each one serves. Capture from the camera (single or burst) or upload images
and video, train on-device, then **test live**: the page shows what the model thinks of the
current frame, by the same rule a run uses, and one click files a wrong frame under the
right class for the next training. Deploy starts the procedure with your model. No bounding
boxes required — image classification is what makes "just add pictures" work, and it is how
object *states* are captured.

**Archive** — every run, filterable by outcome, with its steps, alerts and downloads.
**Verify chain** re-hashes the telemetry from genesis and names the exact record if any
byte was altered.

> **Background is the class that matters most.** A classifier always answers with one of its
> classes, so `background` is how it says "none of these" — and it must show *everything the
> camera will see that is not a target*: the empty scene, your **empty hands**, a single
> finger, your face, other objects. Train fifty photos of an object against twenty of a blank
> wall and the model learns "not a blank wall = the object"; then a raised finger completes
> the step. Give background at least as many photos as your biggest class, and use the Test
> stage to hunt down what it still gets wrong. Predictions are additionally gated on
> confidence **and** margin over the runner-up.

### Recipe: the drink-water experiment

`procedures/drink_water.yaml` judges *pick up the bottle → open the cap → drink → close the
cap → put it back* from five scene states:

1. **Models** → *Training for* **Drink water from a bottle** → **Create them**. You get
   `bottle_home`, `holding_closed`, `holding_open`, `drinking` and `background`.
2. Mark the bottle's home spot (a coaster or a sheet of paper). Capture 30–50 photos of each
   state from the laptop camera, moving between shots. Give `background` the most: empty
   spot, empty hands, fingers, face, other objects, the bottle somewhere that is not home.
3. **Train** (20 epochs), then **Test model**: perform each state and check every verdict.
   File anything wrong under the right class and train again.
4. **Deploy & run.** One class serves two steps — `holding_closed` is "picked up" at step 1
   and "cap closed again" at step 4 — because the engine only judges the step you are on.
   Run in **Strict** mode to have drinking before opening flagged out of sequence.

---

## How it fits together

```
camera + rack markers → canonicalizer → perception → event bus → engine → voice
                                                          ↑          ↓    telemetry
                                                   procedure.yaml  verdicts  dashboard
```

The **event bus** is the seam. Perception publishes observations; the engine consumes them
and knows nothing about cameras or models. That split is what lets the entire reasoning
layer be built and regression-tested before a single model is trained — and it is the demo
fallback if a camera dies in front of a jury.

The **procedure is a YAML file**. Adding an experiment means adding a file, never touching
the engine. PROC-A and PROC-B share one object vocabulary, so loading the second requires
no retraining.

---

## Layout

| Path | Contents |
|---|---|
| `docs/` | PRD, TRD, app flow, UI/UX brief, backend schema, implementation plan |
| `procedures/` | Procedure definitions (PROC-A, PROC-B, demo procedures) |
| `scripts/` | `web_demo.py` (dashboard + training), `live_demo.py` (OpenCV demo) |
| `src/orbital_har/core/` | Event types and the bus |
| `src/orbital_har/reasoning/` | Schema, predicates, window, engine |
| `src/orbital_har/runtime/` | SQLite store, hash-chained telemetry, session runner |
| `src/orbital_har/server/` | FastAPI + WebSocket |
| `ui/` | React dashboard: Mission, Procedures, Models, Archive |
| `tests/` | Unit tests and the golden replay corpus |

Start with [CLAUDE.md](CLAUDE.md) for the invariants, then [docs/](docs/README.md).

> **Note:** the Python package is still named `orbital_har` (the project's earlier working
> title). The product name is **Sarthi**; renaming the package is a mechanical change kept
> separate from feature work.

---

## Training the BAS-prop detector

The pipeline is built; it needs footage. See
[docs/07-DATASET-GUIDE.md](docs/07-DATASET-GUIDE.md) for the prop kit and shot list.

```bash
uv run orbital-har dataset init --root datasets/bas   # class list + layout
# drop frames into datasets/bas/raw/ as take03_0147.jpg
uv run orbital-har dataset label --root datasets/bas  # zero-shot pre-labels
uv run orbital-har dataset split --root datasets/bas  # split by clip, not frame
uv run orbital-har dataset stats --root datasets/bas  # readiness report
uv run orbital-har train  --data datasets/bas/data.yaml
uv run orbital-har eval   --weights runs/bas/weights/best.pt --save
uv run orbital-har export runs/bas/weights/best.pt --format onnx
```

The nine classes are derived from the procedure YAML, never hardcoded. Training
augments rotation to ±180° with vertical flips — there is no floor in orbit, and
that is the cheap part of the orientation requirement.

`eval` works today without any footage: it scores step verdicts and alert recall
against the golden corpus and fits the abstention thresholds. It **refuses** to
emit fitted thresholds from a corpus in which every verdict is correct, because
"the lowest confidence present" is not a calibration.

## Status

The reasoning spine, telemetry hash chain, store, server and live demos work end to end.
Detection currently uses a **pretrained stand-in model** (COCO / YOLO-World) plus optional
on-device trained classifiers. The **trained BAS-prop model** — dataset generation,
labelling and training on real footage — is the next milestone and the primary deliverable.
See the [implementation plan](docs/06-IMPLEMENTATION-PLAN.md).
