# ORBITAL-HAR — project instructions

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
| [docs/SIH26174-problem-statement.md](docs/SIH26174-problem-statement.md) | The verbatim brief |

**These docs are the contract.** A code change that contradicts a doc must update the doc
in the same change. Docs that drift are worse than no docs.

---

## Invariants — never violate these

1. **Zero network at runtime.** No cloud inference, no hosted TTS, no CDN fonts or assets,
   no telemetry upload. Only loopback and the configured RTSP endpoint. CI enforces this.
2. **Perception and reasoning never import each other.** They communicate only through
   `core.bus`. Enforced by import-linter.
3. **The engine never guesses.** Below `τ_abstain` it publishes `UNVERIFIED` and says so.
   A confidently wrong verdict is worse than no verdict.
4. **Canonicalize the input frame, not the output pose.** Rotating model output does not
   work — the model never detected the inverted operator in the first place. See TRD §6.1.
5. **Procedures are config, never code.** Nothing experiment-specific belongs in the engine.
   Adding a procedure means adding a YAML file.
6. **Telemetry is append-only and hash-chained.** Never rewrite a record. Never skip a link.
7. **SQLite is never on the per-frame path.** Transitions and health timers only.
8. **Object states are detector classes** (`red_box_open`), not a downstream classifier.
9. **Losing video output must never stop supervision.** P-MEDIA is fire-and-forget.
10. **Never degrade silently.** Every degradation is logged, surfaced in the UI, and given a
    reason.
11. **Ambient state is not evidence.** Only steps near the frontier are judged. "Close both
    boxes" is trivially true before anyone opened them — without the lookahead guard in
    `Engine._eligible`, far-future steps fire on frame one. See TRD §7.5.
12. **`min_conf` is a detection floor, not a decision threshold.** Raising it above
    `τ_abstain` makes the "cannot verify" path unreachable. Decisions belong to the engine's
    τ values. See TRD §5.2.

---

## Structure

```
core/        bus, config, types              reasoning/  procedure, predicates, engine
perception/  capture, rackframe, detect,     runtime/    voice, telemetry, videoout, store
             hands, pose                     server/     FastAPI, WebSocket
ui/          React dashboard                 datagen/    Blender synthetic pipeline
procedures/  PROC-A, PROC-B YAML             migrations/ numbered SQL
tests/       unit, golden replay corpus      scripts/    prune, verify, export
```

## Conventions

Python 3.11, `uv`, ruff, type hints on public functions, pytest. Frontend React + TypeScript
+ Vite, built to static assets served by FastAPI — **never a dev server in any demo-facing
configuration**.

Config via `config/runtime.yaml` with env overrides. Never hardcode paths, thresholds, IPs,
or model filenames.

Branches `feat/<area>-<slug>`, conventional commits. Golden replay tests must pass before
merging anything touching `reasoning/`.

## Testing

Predicates, state transitions, hash chain, and schema validation are unit tested. The
**golden replay corpus** in `tests/fixtures/sessions/` is the primary regression suite —
recorded event streams with expected verdict sequences, covering clean runs, skips,
out-of-order, occlusion, inversion, and stalls. Every case passes before any release tag.

The reasoning layer is developed and tested entirely without a camera or a model. If you
find yourself needing perception to test the engine, the seam has been broken.

---

## Current state

**M0 Track B is done.** The reasoning spine works end to end with no camera and no models:
procedure schema with strict validation, event bus with replay, seven predicate evaluators,
the step state machine, a scenario simulator, five golden fixtures, and a CLI. 73 tests
pass; ruff, the import-boundary contract and the offline guard are all green.

Verify with `uv run orbital-har demo proc_a_skip_s4`.

Next: **M1** — the remaining foundations (SQLite store, hash-chained telemetry writer and
verifier, FastAPI skeleton with WebSocket, UI shell). Then footage collection gates M2.

Not yet built: everything under `perception/`, `runtime/`, `server/`, `ui/`, `datagen/`.

## Working notes

- Prop kit is the critical path. Footage blocks labelling, which blocks training, which
  blocks every differentiator.
- Dev machine has **150 GB free** — respect the storage budget in TRD §1.1. JPEG renders,
  prune weekly.
- Edge hardware (Jetson) is not yet purchased. Decision gate is 15 November. Until then,
  target DEV and keep the export path clean.
- When behind schedule, follow the descope ladder in the implementation plan §12. Do not
  improvise cuts.
