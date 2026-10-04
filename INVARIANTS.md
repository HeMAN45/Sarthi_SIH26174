# ORBITAL-HAR - project instructions

On-board procedure supervision for BAS experiments.
SIH 2026 problem statement **SIH26174** (ISRO / Department of Space) · Team Hashira.

---

## Read before writing code

The design is settled and documented. Do not re-derive it.

| Doc | When to read |
|---|---|
| [docs/01-PRD.md](docs/01-PRD.md) | Requirement IDs, acceptance criteria, scope boundaries |
| [docs/02-TRD.md](docs/02-TRD.md) | Stack, module contracts, event schema, model specs, budgets |
| [docs/03-APP-FLOW.md](docs/03-APP-FLOW.md) | Runtime behaviour, state machine, every scenario, API surface |
| [docs/04-UIUX-BRIEF.md](docs/04-UIUX-BRIEF.md) | Screens, components, tokens, states |
| [docs/05-BACKEND-SCHEMA.md](docs/05-BACKEND-SCHEMA.md) | SQLite DDL, JSONL formats, hash chain |
| [docs/06-IMPLEMENTATION-PLAN.md](docs/06-IMPLEMENTATION-PLAN.md) | Phases, ownership, descope ladder |
| [docs/07-DATASET-GUIDE.md](docs/07-DATASET-GUIDE.md) | Props, shot list, labelling, training, what to report |
| [docs/SIH26174-problem-statement.md](docs/SIH26174-problem-statement.md) | The verbatim brief |

**These docs are the contract.** A code change that contradicts a doc must update the doc
in the same change. Docs that drift are worse than no docs.

---

## Invariants - never violate these

1. **Zero network at runtime.** No cloud inference, no hosted TTS, no CDN fonts or assets,
   no telemetry upload. Only loopback and the configured RTSP endpoint. CI enforces this.
2. **Perception and reasoning never import each other.** They communicate only through
   `core.bus`. Enforced by import-linter.
3. **The engine never guesses.** Below `τ_abstain` it publishes `UNVERIFIED` and says so.
   A confidently wrong verdict is worse than no verdict.
4. **Canonicalize the input frame, not the output pose.** Rotating model output does not
   work - the model never detected the inverted operator in the first place. See TRD §6.1.
5. **Procedures are config, never code.** Nothing experiment-specific belongs in the engine.
   Adding a procedure means adding a YAML file.
6. **Telemetry is append-only and hash-chained.** Never rewrite a record. Never skip a link.
7. **SQLite is never on the per-frame path.** Transitions and health timers only.
8. **Object states are detector classes** (`red_box_open`), not a downstream classifier.
9. **Losing video output must never stop supervision.** P-MEDIA is fire-and-forget.
10. **Never degrade silently.** Every degradation is logged, surfaced in the UI, and given a
    reason.
11. **Ambient state is not evidence.** Only steps near the frontier are judged. "Close both
    boxes" is trivially true before anyone opened them - without the lookahead guard in
    `Engine._eligible`, far-future steps fire on frame one. See TRD §7.5.
12. **`min_conf` is a detection floor, not a decision threshold.** Raising it above
    `τ_abstain` makes the "cannot verify" path unreachable. Decisions belong to the engine's
    τ values. See TRD §5.2.

---

## Structure

```
core/        bus, types                      reasoning/  schema, predicates, window, engine
perception/  capture, detect, rackframe,     runtime/    store, telemetry, session, runner,
             pose, hands, pipeline                       voice, videoout, training
datagen/     vocabulary, dataset, autolabel, server/     app (FastAPI + WebSocket)
             train, evaluate, export         ui/         React dashboard
procedures/  PROC-A, PROC-B, demo YAML       migrations/ numbered SQL
tests/       unit, golden replay corpus      scripts/    launchers only
```

**`scripts/` holds launchers, never logic.** The import-linter contract covers
`orbital_har` and nothing else, so perception or reasoning code living in a script is
code outside the boundary it is supposed to obey. If a script grows a class, move it.

## Conventions

Python 3.11, `uv`, ruff, type hints on public functions, pytest. Frontend React + TypeScript
+ Vite, built to static assets served by FastAPI - **never a dev server in any demo-facing
configuration**.

Runtime settings are launcher flags (`scripts/web_demo.py`). The engine's live thresholds,
holds and lookahead live in one place, `reasoning.engine.live_config`. The
`config/runtime.yaml` the TRD describes is not built. Never scatter paths, thresholds, IPs
or model filenames through the code.

Branches `feat/<area>-<slug>`, conventional commits. Golden replay tests must pass before
merging anything touching `reasoning/`.

## Testing

Predicates, state transitions, hash chain, and schema validation are unit tested. The
**golden corpus** is the primary regression suite: scripted scenarios in
`simkit/fixtures.py`, each with the verdicts the engine must reach - clean PROC-A and
PROC-B runs, a skip, weak evidence (cannot verify), rack markers lost, a stall, and a step
done out of sequence then recovered (Strict). Every case is replayed under the engine's
defaults (`tests/test_golden.py`) and under the live configuration
(`tests/test_live_timing.py`); `orbital-har fixture <name>` writes one out as a JSONL
stream. Not yet covered: recorded (rather than scripted) runs, and an inverted operator,
which is a perception question. Every case passes before any release tag.

The reasoning layer is developed and tested entirely without a camera or a model. If you
find yourself needing perception to test the engine, the seam has been broken.

---

## Current state

**The pipeline runs end to end on a camera.** 518 tests pass; ruff, the import-boundary
contract and the offline guard are all green.

Built and working:

- **reasoning** - schema, nine predicates (incl. body `gesture`, `tilted`, picture-space
  `moved`, hand `side` on `contact`), step state machine, crew skip/override, calibrated
  abstention, free-float advisory (D-07), wrong-object and wrong-hand alerts (TRD §7.7-7.8),
  golden corpus. Live runs use `live_config`: one step of lookahead, holds as durations,
  evidence never inherited from the step before (TRD §7.4-7.5). `orbital-har eval --save`
  records its scores on the corpus: 38/38 verdicts, 4/4 alerts, 0 false alerts per 10 min.
  Those are scripted cases, not footage: they prove the reasoning, not the camera.
- **perception** - `rackframe` (ArUco + homography to rack millimetres, input-frame
  canonicalization), `pose` (YOLO11-pose), `hands` (palm-from-forearm plus geometric
  contact inference), `colours` (solid-coloured blocks found by hue, no model),
  `tracking` (stable ids over every source, a miss of up to 0.4 s bridged), `gestures` (thirteen body actions -- postures and movements --
  measured in the body's own frame, so they read the same upright, lying or inverted), `detect` (boxes, or an on-device
  classifier or detector), `capture`, and `pipeline` which joins them into event
  payloads. Body tracking is on by default and drawn on the feed. Scene procedures get
  every object in view; presentation procedures every object held up close (TRD §6.2). A
  detector trained on the device runs *beside* the stock one, never instead of it.
- **runtime** - SQLite store with migrations, hash-chained telemetry and verifier,
  `session.LiveSession` (the composition root), `voice` (Piper, pre-synthesized,
  pre-emptible), `videoout` (crash-safe fragmented H.264 mp4 + RTSP, both fed off the capture thread), `training` (on-device classifier or
  detector), `boxes` (stock-detector box proposals for detector training),
  `experiments` (builder experiments saved as procedure files in `data/experiments/`).
- **server** - one FastAPI app; live endpoints degrade to 503 without a session.
- **ui** - React console: Mission, Procedures (saved experiments, built-in library,
  builder with objects and body actions), Models (Classes →
  Capture → Train → Test → Deploy), Archive (history + chain verification). Flat
  engineering-console style, three themes (Graphite default, Slate, Daylight); Geist and
  Lucide bundled, nothing fetched at run time. Camera display mirrored by default.
- **training** - perception draws its overlay on a copy, so training captures are clean;
  validation is held out; the test stage uses the live acceptance rule; the last model
  survives a restart. Background-class advice is surfaced before training. *Objects*
  mode trains a detector from proposed boxes with background photos as negatives, so
  a hand or arm is background by construction (`tests/test_boxes.py`).
- **procedures** - `drink_water.yaml`: five steps from scene-state classes, one class
  serving two steps; `drink_water_body.yaml`: the same five from contact, gestures and a
  home region, no training (`tests/test_drink_water.py`). `bench_sample.yaml`: the PS
  sample experiment worked continuously at a bench, colour-found boxes moved between
  taped zones, judged by where things end up and a hand having handled them; nothing
  held up, no gesture signals, no training (`tests/test_bench_sample.py`).
- **lifecycle** - Ready → Live → Complete. The camera belongs to a run: off in Ready,
  powered by New run, released by End run (`tests/test_session.py`).

Verify with `uv run orbital-har demo proc_a_skip_s4`, then `uv run pytest`.

**PROC-A - the ISRO sample experiment - cannot run live yet.** Its perception-to-engine
seam is proven (`tests/test_pipeline.py`: real rack localisation and contact inference
satisfy its `contact` and `near` steps with no camera and no detector in the loop), but
the stock detector knows none of its nine classes, so live it never gets past step 1
(12 attempts). It runs live once its model is trained.

**The remaining deliverable is the trained model.** Detection is still a pretrained
stand-in (COCO / YOLO-World). Props to footage to labels to a trained 11-class BAS model
is the critical path, and nothing downstream of it can start until the prop kit exists.
`datagen/` (Blender synthetic pipeline) is not built and may be descoped in favour of
real footage - decide before committing to it.

## Working notes

- Ultralytics installs its own top-level `tests` package into site-packages. A script run
  from outside the repo root that does `from tests.conftest import ...` gets theirs and fails;
  run such scripts from the repo root (pytest is unaffected).
- Prop kit is the critical path. Footage blocks labelling, which blocks training, which
  blocks every differentiator.
- Dev machine has **150 GB free** - respect the storage budget in TRD §1.1. JPEG renders,
  prune weekly.
- Edge hardware (Jetson) is not yet purchased. Decision gate is 15 November. Until then,
  target DEV and keep the export path clean.
- When behind schedule, follow the descope ladder in the implementation plan §12. Do not
  improvise cuts.
