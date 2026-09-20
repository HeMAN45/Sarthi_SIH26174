# PRD — Product Requirements Document

**Project:** ORBITAL-HAR — On-board procedure supervision for BAS experiments
**Problem statement:** SIH26174 (ISRO / Department of Space)
**Team:** Hashira
**Version:** 1.0 · 2026-09-20
**Status:** Baseline

---

## 1. Context

On the Bharatiya Antariksh Station (BAS), an astronaut executing a scientific experiment
cannot rely on real-time ground support. Communication delay and a hard downlink bandwidth
budget make live supervision from Bengaluru impossible.

ORBITAL-HAR is an on-board, fully offline visual supervisor. A fixed payload camera watches
the crew member work. The system knows the experiment's expected step sequence, recognises
what is actually being done, prompts the next action, raises a voice alert when a step is
skipped or performed out of order, and emits a tiny structured log that ground control can
actually afford to receive.

The design intent in one line: **replace a video downlink with a kilobyte of verified
procedure telemetry.**

---

## 2. Goals

| # | Goal |
|---|---|
| G1 | Validate an experiment's step sequence in real time from a single fixed camera |
| G2 | Operate with zero network dependency on a power-constrained edge device |
| G3 | Reduce what ground control must receive by orders of magnitude versus raw video |
| G4 | Generalise to a new experiment by editing a config file, with no retraining |
| G5 | Remain correct when the operator has no fixed "up" (microgravity orientation) |
| G6 | Justify every decision it makes — no unexplainable black-box verdicts |

## 3. Non-goals

- Controlling or actuating any payload hardware. The system observes; it never commands.
- Replacing crew judgement. It advises and flags; the crew always has override authority.
- Multi-crew tracking. v1 supervises one operator in frame.
- Natural-language conversation. Voice input, if enabled, is a fixed command vocabulary.
- Flight qualification. This is a functional prototype, not flight software.

---

## 4. Personas

**P1 — Crew operator (primary).** Executing the procedure. Hands occupied, possibly gloved,
possibly inverted. Needs the next instruction legible at a glance and an alert they cannot
miss. Has zero tolerance for false alarms — an assistant that cries wolf gets muted, and a
muted assistant has no value.

**P2 — Ground operations analyst (secondary).** Receives telemetry after the fact, not live
video. Needs to reconstruct exactly what happened, in what order, with what confidence, and
to trust that the record was not altered.

**P3 — Evaluation jury (SIH-specific, real).** ISRO engineers assessing the system in short
rotating visits. Needs to grasp the mission rationale within ninety seconds, see evidence
of edge feasibility, and have every claim demonstrable live. This persona is explicitly in
scope: several requirements below exist to serve it.

---

## 5. User stories

### Crew operator

**US-01** — As a crew operator, I see the next step displayed prominently before I begin, so
I never have to consult a paper checklist mid-procedure.
*Accepts when:* on session start the first step is visible within 2 s and spoken once.

**US-02** — As a crew operator, I am told the next step as soon as I complete the current
one, so the procedure flows without me asking.
*Accepts when:* step completion triggers visual and voice advance within 1.5 s of the
completing evidence being stable.

**US-03** — As a crew operator, I am alerted by voice when I skip a step, so I can correct
before the error propagates.
*Accepts when:* a skipped step produces a distinct urgent voice alert plus a persistent
on-screen banner, and the banner does not clear until acknowledged or corrected.

**US-04** — As a crew operator, I am alerted when I perform a step out of order.
*Accepts when:* evidence for a step whose preconditions are unmet raises an out-of-order
alert naming both the observed and the expected step.

**US-05** — As a crew operator, I am told when the system is unsure rather than being given a
confident wrong answer.
*Accepts when:* step confidence below threshold for longer than the dwell window produces an
explicit "cannot verify — confirm manually" state, never a silent guess.

**US-06** — As a crew operator, I can override the system when I know better.
*Accepts when:* an override control marks the current step complete, is written to the log
as crew-attributed, and the procedure advances.

**US-07** — As a crew operator, the system keeps working when I am sideways or inverted.
*Accepts when:* step recognition accuracy at ±90° and 180° operator roll is within 10
percentage points of the upright baseline.

### Ground operations analyst

**US-08** — As a ground analyst, I receive a compact structured record of the session.
*Accepts when:* a completed session yields a machine-readable log whose size is under 50 KB
per procedure hour.

**US-09** — As a ground analyst, I can verify the record was not altered.
*Accepts when:* the log is hash-chained and a verifier tool reports a pass or names the first
broken link.

**US-10** — As a ground analyst, I can review the video when bandwidth allows.
*Accepts when:* the full session is stored locally in segments and streamed live to a
configured IP endpoint.

### Jury / operator of the demo

**US-11** — As an evaluator, I can load a procedure the system has never run and see it work.
*Accepts when:* a second procedure YAML using the same object vocabulary is loaded at runtime
without restarting the perception stack or retraining any model.

**US-12** — As an evaluator, I can see live evidence that this runs within an edge budget.
*Accepts when:* the dashboard displays live FPS, memory, and inference latency.

---

## 6. Functional requirements

Every requirement traces to an ISRO expected-solution bullet (see §10).

### 6.1 Capture and perception

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Continuously ingest frames from a fixed camera at ≥15 FPS sustained | Must |
| FR-02 | Detect procedure-relevant objects and their discrete states (open/closed, present/absent) | Must |
| FR-03 | Track operator hand landmarks and derive hand–object contact | Must |
| FR-04 | Estimate operator body pose expressed in the payload-rack coordinate frame | Must |
| FR-05 | Recover payload-rack 6-DoF pose from fiducial markers every frame | Must |
| FR-06 | Canonicalize the input frame into rack orientation before inference | Must |
| FR-07 | Degrade gracefully and declare reduced confidence when markers or the operator are occluded | Must |
| FR-08 | Publish all perception output as a timestamped, replayable event stream | Must |

### 6.2 Procedure reasoning

| ID | Requirement | Priority |
|---|---|---|
| FR-10 | Load a procedure definition from an external config file at runtime | Must |
| FR-11 | Validate the procedure file on load and refuse to start on a schema violation, naming the offending field | Must |
| FR-12 | Evaluate declarative step predicates against the perception stream | Must |
| FR-13 | Require evidence stability over a configurable dwell window before any state transition | Must |
| FR-14 | Maintain per-step state: pending, active, complete, skipped, out-of-order, unverified | Must |
| FR-15 | Support steps declared order-independent within a group | Must |
| FR-16 | Support per-step timeouts and raise a stall condition when exceeded | Should |
| FR-17 | Emit a calibrated confidence with every step verdict | Must |
| FR-18 | Abstain explicitly rather than guess when confidence is below threshold | Must |
| FR-19 | Accept crew override for any step, recorded with attribution | Must |
| FR-20 | Detect unexpected free-floating objects and raise a non-blocking advisory | Could |

### 6.3 Alerting and voice

| ID | Requirement | Priority |
|---|---|---|
| FR-30 | Announce the next step by voice on session start and after each completion | Must |
| FR-31 | Raise a voice alert on skipped step, acoustically distinct from routine prompts | Must |
| FR-32 | Raise a voice alert on out-of-order execution | Must |
| FR-33 | Synthesize all speech fully offline | Must |
| FR-34 | Pre-synthesize fixed procedure prompts at load time to keep alert latency bounded | Should |
| FR-35 | Accept a fixed offline voice command vocabulary: repeat, next, mark anomaly, override | Could |

### 6.4 Telemetry and recording

| ID | Requirement | Priority |
|---|---|---|
| FR-40 | Write an append-only, timestamped, structured event log | Must |
| FR-41 | Hash-chain each log record to the previous one | Must |
| FR-42 | Provide a verifier that validates the chain and reports the first break | Must |
| FR-43 | Keep the log under 50 KB per procedure hour | Must |
| FR-44 | Record the full session video locally in crash-safe segments | Must |
| FR-45 | Stream live video to a configurable IP endpoint | Must |
| FR-46 | Record every session's event stream in replayable form | Must |
| FR-47 | Report live telemetry volume against an equivalent raw-video baseline | Should |

### 6.5 Interface

| ID | Requirement | Priority |
|---|---|---|
| FR-50 | Provide a crew view optimised for glanceability | Must |
| FR-51 | Provide a ground-operations view with video, overlays, timeline, and confidence traces | Must |
| FR-52 | Display live system health: FPS, latency, memory, device | Must |
| FR-53 | Update the interface from live state without polling | Must |
| FR-54 | Serve the interface entirely from local assets with no external requests | Must |
| FR-55 | Provide session replay driven by a recorded event stream | Must |
| FR-56 | Provide start, stop, override, and mute controls | Must |

### 6.6 Deployment

| ID | Requirement | Priority |
|---|---|---|
| FR-60 | Run with no network connectivity of any kind | Must |
| FR-61 | Package as a single launchable application with bundled model weights | Must |
| FR-62 | Start from cold to ready in under 30 s | Should |
| FR-63 | Run on an edge-class device within a documented power and memory budget | Should |

---

## 7. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | Sustained pipeline throughput | ≥15 FPS at 1280×720 on the dev GPU |
| NFR-02 | Step-completion detection latency | ≤1.5 s from stable evidence |
| NFR-03 | Alert latency (skip / out-of-order) | ≤1.0 s from stable evidence |
| NFR-04 | False alert rate | ≤1 per 10 completed procedure runs |
| NFR-05 | Step recognition accuracy, upright | ≥95% on held-out real runs |
| NFR-06 | Step recognition accuracy, rotated ±90°/180° | ≥85% |
| NFR-07 | Telemetry log size | ≤50 KB per procedure hour |
| NFR-08 | Cold start to ready | ≤30 s |
| NFR-09 | Continuous operation without degradation | ≥2 h |
| NFR-10 | External network requests at runtime | Exactly zero |
| NFR-11 | Procedure swap time, no restart of perception | ≤10 s |

---

## 8. Differentiator features

These exist to win the evaluation, not merely to satisfy the brief. Each maps to a scripted
demo moment.

| ID | Feature | Demo moment |
|---|---|---|
| D-01 | Rack-relative orientation invariance | Operator performs the procedure lying down and inverted; camera is rotated 180° mid-run; tracking never breaks |
| D-02 | Procedure-as-config | Jury picks the second procedure; it is loaded live; it runs with no retraining |
| D-03 | Synthetic data generator delivered as a tool | Show the Blender pipeline producing labelled frames on demand |
| D-04 | Quantified downlink saving | Live byte counter beside the equivalent raw-video figure |
| D-05 | Tamper-evident telemetry | Alter one byte of the log in front of the jury; the verifier names the broken link |
| D-06 | Calibrated abstention | System says "cannot verify" under deliberate occlusion instead of guessing |
| D-07 | Free-float object advisory | Release an object; system flags it as unsecured |
| D-08 | Offline voice commands | Hands stay on the payload throughout the run |

---

## 9. Success metrics

**Product.** All Must requirements implemented and demonstrable. NFR targets met on the dev
machine, with measured edge figures published even if the edge target slips.

**Evaluation.** All eight differentiator demo moments executable end-to-end, in any order, on
demand, without a network connection, within a fifteen-minute jury window.

**Robustness.** Ten consecutive full procedure runs with zero false alerts and zero crashes,
recorded as evidence before the finale.

---

## 10. Traceability to the ISRO brief

| ISRO expected solution | Requirements |
|---|---|
| Continuously process local video feeds to track experiment sequence | FR-01, FR-02, FR-03, FR-08, FR-12 |
| Suggest the next step at start and after each step | FR-30, FR-50, US-01, US-02 |
| Voice alert on skipped or out-of-sequence step | FR-31, FR-32, FR-33 |
| Timestamped structured lightweight log with outcomes | FR-40 – FR-43, FR-47 |
| Stream video to a specific IP and store locally | FR-44, FR-45 |
| GUI for monitoring | FR-50 – FR-56 |
| Trained model runs on an offline standalone system | FR-60 – FR-63, NFR-10 |
| *Optional:* orientation-agnostic rack-relative tracking | FR-04, FR-05, FR-06, D-01 |
| Dataset generation for detection, pose, hand-object interaction | D-03, and §5 of the implementation plan |

No brief item is unmapped.

---

## 11. Out of scope for v1

Multi-crew tracking · payload actuation · natural-language dialogue · procedure authoring UI
(YAML is hand-edited) · cloud sync of any kind · mobile clients · multi-camera fusion
(single camera primary; second camera is a stretch) · flight qualification.

---

## 12. Open risks

| Risk | Impact | Mitigation |
|---|---|---|
| False alerts erode crew trust | Fatal to the product thesis | Dwell windows, calibrated thresholds, explicit abstention (FR-18) |
| Edge device not procured in time | Weakens the feasibility claim | Publish measured laptop figures with a documented extrapolation; buy decision gated early (see implementation plan) |
| Dataset collection underestimated | Everything downstream slips | Start footage collection in week 0, before any modelling |
| Orientation invariance proves harder than expected | Loses the headline differentiator | It is architecturally isolated in the canonicalizer; the system degrades to upright-only without redesign |
| 150 GB storage ceiling | Blocks synthetic scale-up | Explicit storage budget in the TRD; JPEG renders, segment rotation |

---

**Related:** [TRD](02-TRD.md) · [App flow](03-APP-FLOW.md) · [UI/UX brief](04-UIUX-BRIEF.md) ·
[Backend schema](05-BACKEND-SCHEMA.md) · [Implementation plan](06-IMPLEMENTATION-PLAN.md)
