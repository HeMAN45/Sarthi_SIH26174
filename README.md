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
| `--no-body` | start with body tracking off, to save CPU on a slow machine |
| `--no-trained` | start with the stock objects only, leaving your trained detector off |
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
**body** meters.

**Body tracking** runs by default: YOLO11-pose draws the skeleton on the feed, finds both
hands, notices which object **each** hand touches, and reads thirteen body actions:

| One hand (left or right) | Both hands / body | Movements |
|---|---|---|
| raise a hand · hand to face · hand on head · reach out | both hands up · hands together · arms crossed · arms out · hands on hips (standing) | wave · lift · lower · clap |

They are measured against the body's own axis (hips to shoulders, or shoulders to head), not
the image, so they read the same for an operator upright, lying down or upside down — the "no
fixed up" condition in SIH26174. Movements are read from the last couple of seconds, so do
them slowly: a laptop manages 3–4 frames a second with detection and pose.
Turn it off from the camera HUD to save CPU; a procedure whose steps need it keeps it on.

**Alerts** are spoken and take over the top of the feed until acknowledged: a step skipped,
a step done out of sequence, a stall, a step it cannot verify — and a **wrong object**:
pick up the phone while the step asks for the bottle and it says *"Wrong object: the phone
is for step 3. Now: Pick up the bottle."* straight away, in either run mode. **Strict**
mode also judges the next step, so doing it early is flagged out of sequence; **Clean**
judges only the current one. When a run completes or is ended, a **debrief** shows the
verdict, per-step timing, the sealed SHA-256 chain head and the downlink ratio.

**Several objects at once.** Every object the procedure asks about is reported. A
procedure that relates things — a hand touching the bottle, the bottle standing in its home
spot — sees everything in view; one that asks you to *show* objects counts those held up
close (at least 6 % of the frame), so two can be shown together and a book at the back of
the desk is not "shown".

**Procedures** — build an experiment and **save it by name**; it appears under *Your
experiments* and can be run, loaded, edited or deleted any time, restarts included. Each step
is an object, a body action, or both — "hand to face" with the bottle is *drinking* — in your
own words, with **what is done with the object** (show it, hold it, pour from it, move it)
and **which hand** (either, left, right). Using the other hand raises a spoken *wrong hand*
alert. Pouring is read from the bottle tipping over; the stock detector often loses a tipped
bottle, so train yours with pouring photos before relying on it. Saved experiments are ordinary procedure files in `data/experiments/`. The
built-in library sits below them. A procedure the loaded detector cannot perceive is refused
with the missing classes named; **Run anyway** is an explicit choice.

**Models** — teach it new objects and states in five stages: **Classes → Capture → Train
→ Test → Deploy**. Pick the procedure you are training for and it creates the classes and
shows which step each one serves. Capture from the camera (single or burst) or upload images
and video, then train in one of two ways:

- **Objects** (recommended) — a detector that learns *where* the object is. The stock
  detector proposes a box around the object in every photo (no drawing), you review them,
  and your hand, arm and face fall outside the box, so they become background by
  construction. It learns what it calls your object ("book") and prefers that box in every
  photo, ignores thin strips like a table edge, and takes a closer look for that name where
  it found nothing — those boxes are marked *faint* in the review grid. Works for everyday
  objects: books, bottles, cups, phones. Slower: about a minute per epoch on a laptop.
- **Whole scene** — a classifier over the whole picture. Fast, and right for scenes, but it
  learns *anything* that differs between your photo folders.

Then **test live**: the page shows what the model makes of the current frame, by the same
rule a run uses, and one click files a wrong frame under the right class for the next
training. Deploy starts the procedure with your model.

**Your objects join the 80 stock ones.** A detector trained in Objects mode runs beside the
stock one, so after Deploy the builder lists *Your trained objects* first and every stock
object is still there — one step can ask for the stock bottle, the next for your trained
cap state. It is re-attached every time SARTHI starts (`--no-trained` to leave it off). A
Whole-scene classifier is the exception: it replaces the detector while deployed.

**Archive** — every run, filterable by outcome, with its steps, alerts and downloads.
**Verify chain** re-hashes the telemetry from genesis and names the exact record if any
byte was altered.

> **Why an empty arm used to count as the object.** A whole-scene classifier learns whatever
> differs between its folders. If every "book" photo has your arm coming in from the left and
> every background photo has you sitting in the middle, it learns *arm from the left = book* —
> and scores 100 % on held-out photos that share the same framing. More background photos do
> not fix that; learning where the object is does. Use **Objects** mode for things you hold.
> In **Whole scene** mode, background must show everything the camera will see that is not a
> target — empty hands, a finger, your face, other objects, *and your arm in the same place
> as in the target photos* — and the Test stage is where you find what it still gets wrong.

### Recipe: the drink-water experiment

*Pick up the bottle → open the cap → drink → close the cap → put it back* ships in two forms.

**No training — `drink_water_body.yaml`.** The stock detector finds the bottle, body tracking
does the rest: *pick up* is a hand touching the bottle, *open* and *close* are both hands
together at it, *drink* is a hand at the face with the bottle in view, and *put it back* is the
bottle standing in the **home** spot — drawn on the camera view as a saffron box — for two
seconds. Procedures → Built-in → Run.

**Trained — `drink_water.yaml`,** for when you want the cap itself verified. Only the cap
state is learned; picking up, drinking and putting back are measured as above.

1. **Models** → *Training for* **Drink water from a bottle** → **Create them**. You get
   `bottle_closed`, `bottle_open` and `background` — "bottle" itself the stock detector
   already knows.
2. Capture 40–60 photos of each: your bottle with the cap on, and with it off (the open neck
   visible) — in your hand and standing on the table, near and far, turned, both hands.
   Give `background` the scene *without* the bottle: empty hands, a finger, your face, the
   empty table.
3. **Train** in **Objects** mode (15 epochs, about 15–20 minutes on a laptop). Review the
   boxes first; then **Test model**: cap on, cap off, empty hand.
4. **Deploy & run.** Stand the bottle in the saffron **home** box before you start. Run in
   **Strict** mode to have "put it back" before "close the cap" flagged out of sequence.

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
