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

Open **http://localhost:8000**. A local FastAPI server runs the camera → detection →
reasoning-engine loop and streams the annotated video (MJPEG) plus live state (WebSocket)
to a self-contained browser UI — step timeline, next-step prompt spoken aloud, alert
banner, run summary and controls.

| Flag | Effect |
|---|---|
| `--world` | use **YOLO-World** open-vocabulary detection (any object you can name) |
| `--procedure demo_live4` | 4-step demo instead of 3 |
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

**Build an experiment in the browser.** Click **＋ Build experiment**, pick objects, order
them into a sequence, and run it live — no YAML editing, no restart. With `--world` you can
type *any* object name, not just a fixed list.

**Teach it new objects and states.** Click **🧠 Train model** to create classes
(e.g. `open_book`, `closed_book`), capture images straight from the camera
(**📷 Snap** / **📷 ×10**) or upload files, train a classifier on-device, and click
**Use** to run it live. No bounding boxes required — image classification is what makes
"just add pictures" work, and it is how object *states* are captured.

> A classifier always returns one of its classes, so a **`background`** class (images of the
> empty scene / covered lens) is required — it is the "nothing here" escape hatch. Without
> it the model confidently guesses a real object when nothing is present. Predictions are
> additionally gated on confidence **and** margin over the runner-up.

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
| `ui/` | React dashboard shell |
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
