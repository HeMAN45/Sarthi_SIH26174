# ORBITAL-HAR

On-board procedure supervision for BAS experiments.
Smart India Hackathon 2026 · **SIH26174** · ISRO / Department of Space · Team Hashira.

A fixed payload camera watches an astronaut execute a scientific experiment. The system
knows the expected step sequence, recognises what is actually being done, prompts the next
action, raises a voice alert on a skipped or out-of-order step, and emits a few kilobytes
of verified telemetry instead of a video downlink.

Everything runs offline on an edge device. No cloud, no network, no exceptions.

## Status

**M0 — reasoning spine complete.** Procedure schema, event bus, predicate evaluators, step
state machine, scenario simulator and golden replay corpus all work end to end, with no
camera and no models. Perception lands in M2. See the
[implementation plan](docs/06-IMPLEMENTATION-PLAN.md).

## Quickstart

```bash
uv sync --group dev
```

Validate a procedure definition:

```bash
uv run orbital-har validate proc_a
```

Watch the engine supervise a run where the operator skips a step:

```bash
uv run orbital-har demo proc_a_skip_s4
```

Other scenarios: `proc_a_clean`, `proc_a_unverified_s4`, `proc_a_rack_lost`, `proc_b_clean`.
List them with `uv run orbital-har fixture list`.

Run the suite:

```bash
uv run pytest
```

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

## Layout

| Path | Contents |
|---|---|
| `docs/` | PRD, TRD, app flow, UI/UX brief, backend schema, implementation plan |
| `procedures/` | Procedure definitions (PROC-A, PROC-B) |
| `src/orbital_har/core/` | Event types and the bus |
| `src/orbital_har/reasoning/` | Schema, predicates, window, engine |
| `src/orbital_har/simkit/` | Scenario simulator and golden fixtures |
| `src/orbital_har/perception/` | Detection, hands, pose, rack frame (M2) |
| `tests/` | Unit tests and the golden replay corpus |

Start with [CLAUDE.md](CLAUDE.md) for the invariants, then [docs/](docs/README.md).
