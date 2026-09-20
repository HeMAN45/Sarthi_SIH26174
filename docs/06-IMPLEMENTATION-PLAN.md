# Implementation Plan

**Project:** ORBITAL-HAR · SIH26174 · Team Hashira
**Version:** 1.0 · 2026-09-20
**Horizon:** today → grand finale

---

## 1. Roles

Six members, balanced across ML, backend, and frontend.

| Role | Owns | Primary modules |
|---|---|---|
| **R1** Perception ML | Detection, pose, rack frame, model export | `perception/*` |
| **R2** Data & training | Prop kit, footage, labelling, Blender, calibration | `datagen/*`, datasets |
| **R3** Core engine | Bus, procedure schema, predicates, state machine | `core/*`, `reasoning/*` |
| **R4** Runtime services | Voice, telemetry, video I/O, store, API | `runtime/*`, `server/*` |
| **R5** Frontend | Both views, replay UI, overlays | `ui/*` |
| **R6** Integration lead | Demo, docs, submission, CI, cross-cutting | repo-wide |

R6 floats to whichever workstream is blocking. R6 does not own a critical-path module by
design — the integrator must stay available.

---

## 2. Milestones

| ID | Milestone | Window |
|---|---|---|
| **M0** | Idea submitted + technical spike proven | Sep 20 – 30 |
| **M1** | Foundations: schema, bus, state machine, replay | Oct 1 – 14 |
| **M2** | Perception v1 integrated, PROC-A runs end-to-end | Oct 15 – 28 |
| **M3** | Voice, telemetry, video I/O, both dashboard views | Oct 29 – Nov 11 |
| **M4** | Differentiators: orientation, hot-swap, synthetic data | Nov 12 – 25 |
| **M5** | Edge port, hardening, calibration, soak | Nov 26 – Dec 9 |
| **M6** | Demo rehearsal and buffer | Dec 10 → finale |

The SIH internal hackathon and national shortlist fall inside this window on dates not yet
published. M2 is deliberately a demoable state so an early internal round can be served
without disrupting the plan.

---

## 3. M0 — Idea submission and spike · Sep 20–30

**Goal.** Get shortlisted, and prove the riskiest assumption cheaply.

Two parallel tracks. Do not serialise these — the deadline is hard.

### Track A — submission (R6 lead, R1 support)

| ID | Task | Owner |
|---|---|---|
| A-01 | Draft idea write-up from PRD §1–2 and differentiators D-01…D-08 | R6 |
| A-02 | Produce architecture diagram for the submission | R6 |
| A-03 | Write the downlink-saving argument with worked numbers | R1 |
| A-04 | Internal review, revise | all |
| A-05 | **Submit before 30 Sep** | R6 |

### Track B — spike (R3 lead)

| ID | Task | Owner | Depends |
|---|---|---|---|
| B-01 | Repo init, `uv`, ruff, pytest, CI skeleton | R3 | — |
| B-02 | Pydantic procedure schema, PROC-A encoded | R3 | B-01 |
| B-03 | Hand-written `events.jsonl` fixture | R3 | B-02 |
| B-04 | Predicate evaluators: `detect`, `contact`, `near` | R3 | B-02 |
| B-05 | State machine: pending → active → complete + skip | R3 | B-04 |
| B-06 | CLI replay printing verdicts to terminal | R3 | B-05 |
| B-07 | Buy prop kit, build rack board, print ArUco | R2 | — |
| B-08 | Record first 10 PROC-A runs | R2 | B-07 |

**Definition of done.** Idea submitted. `orbital-har replay --fixture` prints
`step 2 complete → next: remove the red box` and `step 4 SKIPPED` from a hand-written event
file. Ten runs recorded. No camera, no models, no ML involved in the spike.

---

## 4. M1 — Foundations · Oct 1–14

**Goal.** The entire reasoning half of the system, complete and tested, before perception
exists.

| ID | Task | Owner | Depends |
|---|---|---|---|
| C-01 | Event bus: publish, subscribe, persist, replay | R3 | B-01 |
| C-02 | Remaining predicates: `absent`, `moved`, `dwell`, `count` | R3 | B-04 |
| C-03 | Full state machine incl. out-of-order, unverified, stalled, overridden | R3 | B-05 |
| C-04 | Order-independent groups | R3 | C-03 |
| C-05 | Hysteresis and confidence aggregation | R3 | C-03 |
| C-06 | SQLite schema + migrations | R4 | B-01 |
| C-07 | Store layer, session lifecycle writes | R4 | C-06 |
| C-08 | Hash-chained telemetry writer + verifier CLI | R4 | C-06 |
| C-09 | FastAPI skeleton, WebSocket fan-out, health endpoint | R4 | C-01 |
| C-10 | UI shell, routing, design tokens, WebSocket client | R5 | C-09 |
| C-11 | `StepTimeline`, `StepPips`, `AlertBanner` against mock data | R5 | C-10 |
| C-12 | Encode PROC-B | R3 | C-04 |
| C-13 | **Golden corpus: 10 labelled sessions** | R2 | B-08 |
| C-14 | Golden replay test suite | R3 | C-13 |
| C-15 | Blender scene: props, materials, rack | R2 | B-07 |
| C-16 | Collect 40 more real runs incl. failures and inverted | R2 | B-08 |
| C-17 | Import-linter boundary rule + offline CI check | R6 | B-01 |

**Definition of done.** All ten golden sessions replay to their expected verdict sequences.
Telemetry verifier passes and correctly identifies a deliberately corrupted file. Dashboard
renders live state from a replayed session. **A full PROC-A run is demoable end-to-end with
no camera and no ML.**

---

## 5. M2 — Perception v1 · Oct 15–28

**Goal.** Replace the mocked stream with real vision.

| ID | Task | Owner | Depends |
|---|---|---|---|
| D-01 | Capture module, timestamping, back-pressure | R1 | C-01 |
| D-02 | Labelling: auto-prelabel, correct in CVAT, v1 dataset | R2 | C-16 |
| D-03 | Train YOLO11s on the 11-class vocabulary | R1 | D-02 |
| D-04 | Detector module + ByteTrack tracking | R1 | D-03 |
| D-05 | MediaPipe hands + contact inference | R1 | D-01 |
| D-06 | YOLO11-pose integration | R1 | D-01 |
| D-07 | Wire perception into the bus | R1 | D-04, D-05 |
| D-08 | Tune `hold_frames` and thresholds against real runs | R3 | D-07 |
| D-09 | MJPEG preview with overlays | R4 | D-07 |
| D-10 | `VideoPane`, `EvidencePanel`, `ConfidenceTrace` | R5 | D-09 |
| D-11 | Measure mAP, publish per-class metrics | R1 | D-03 |
| D-12 | Synthetic render pipeline producing labelled frames | R2 | C-15 |

**Definition of done.** A live PROC-A run from camera to verdict, end to end. Detection mAP50
≥0.85 on held-out real footage. False alerts ≤1 in 10 runs. This is the state that can serve
an internal hackathon round.

---

## 6. M3 — Runtime and dashboard · Oct 29 – Nov 11

**Goal.** Every PS deliverable satisfied. No differentiators yet.

| ID | Task | Owner | Depends |
|---|---|---|---|
| E-01 | Piper TTS, prompt pre-synthesis, queue, pre-emption | R4 | C-03 |
| E-02 | Alert voice styling and attention tone | R4 | E-01 |
| E-03 | FFmpeg segmented local recording | R4 | D-01 |
| E-04 | MediaMTX RTSP publish to configured IP | R4 | E-03 |
| E-05 | Telemetry size instrumentation + raw-video equivalent | R4 | C-08 |
| E-06 | Crew HUD, complete | R5 | C-11 |
| E-07 | Ground ops view, complete | R5 | D-10 |
| E-08 | Health strip: FPS, latency, memory, subsystem dots | R5 | C-09 |
| E-09 | Session history, download, verify UI | R5 | C-08 |
| E-10 | Replay player UI | R5 | C-01 |
| E-11 | Crew override, acknowledge, mute | R4, R5 | C-03 |
| E-12 | Process supervision, auto-restart for P-MEDIA and P-SERVE | R4 | E-04 |
| E-13 | Scale synthetic set to ~2,000 frames, retrain | R2, R1 | D-12 |

**Definition of done.** Every ISRO expected-solution bullet demonstrable. PRD traceability
matrix (§10) fully green. Telemetry under 50 KB/hour, measured.

---

## 7. M4 — Differentiators · Nov 12–25

**Goal.** The things that win, not merely satisfy.

| ID | Task | Owner | Depends | Diff |
|---|---|---|---|---|
| F-01 | ArUco detection, rack pose, `solvePnP` | R1 | D-01 | D-01 |
| F-02 | Input-frame canonicalization ahead of inference | R1 | F-01 | D-01 |
| F-03 | Rotation augmentation, retrain, validate at ±90°/180° | R1, R2 | F-02 | D-01 |
| F-04 | Raw/canonical toggle in the video pane | R5 | F-02 | D-01 |
| F-05 | Marker-loss degradation and recovery | R1 | F-01 | — |
| F-06 | Procedure hot-swap endpoint + vocabulary check | R3 | C-12 | D-02 |
| F-07 | Procedure loader UI with validation errors | R5 | F-06 | D-02 |
| F-08 | Validate PROC-B live with zero retraining | all | F-06 | D-02 |
| F-09 | Package Blender pipeline as a documented CLI | R2 | E-13 | D-03 |
| F-10 | Downlink meter panel | R5 | E-05 | D-04 |
| F-11 | Tamper demo: corrupt-and-verify flow in UI | R4, R5 | C-08 | D-05 |
| F-12 | Temperature scaling on the calibration set | R1 | D-11 | D-06 |
| F-13 | Abstention path end-to-end incl. voice and UI | R3, R4 | F-12 | D-06 |
| F-14 | Free-float advisory | R3 | D-04 | D-07 |
| F-15 | Vosk ASR, four-command grammar | R4 | E-01 | D-08 |

**Definition of done.** All eight differentiator demo moments executable on demand, in any
order, offline.

---

## 8. M5 — Edge and hardening · Nov 26 – Dec 9

| ID | Task | Owner |
|---|---|---|
| G-01 | **Edge hardware buy decision — gate, see §9** | R6 |
| G-02 | ONNX export, TensorRT FP16 engine build | R1 |
| G-03 | Edge port, dependency resolution, camera on ARM | R1, R4 |
| G-04 | Measure FPS, RAM, watts on edge; publish figures | R1 |
| G-05 | Implement and verify the degradation ladder | R1, R3 |
| G-06 | 2-hour soak, memory and FPS traced | R6 |
| G-07 | 10 consecutive clean runs recorded as evidence | R2, R6 |
| G-08 | Crash-recovery verification | R4 |
| G-09 | Single-command packaging, cold start ≤30 s | R4 |
| G-10 | Offline CI gate enforced on every commit | R6 |
| G-11 | Storage prune script and backup of raw footage | R2 |

**Definition of done.** Runs on target hardware with published numbers, or runs on DEV with
published numbers plus a documented extrapolation. Ten clean runs on record. Zero network
calls proven by test.

---

## 9. Hardware gate (G-01)

**Decide by 15 November.** Later than that and the port cannot be de-risked in time.

| Item | Purpose | Buy by |
|---|---|---|
| Prop kit: boxes, vial, tweezers, tether clips, board | PROC-A and PROC-B | **Sep 25** |
| ArUco marker prints, rigid mounting | Rack frame | **Sep 25** |
| USB webcam 1080p30, tripod or clamp | Capture | **Sep 25** |
| Jetson Orin Nano 8 GB dev kit + PSU + NVMe | Edge demo | Nov 15 |
| USB speaker | Voice alerts audible in a hall | Nov 15 |
| Second display | Dual-view demo | Nov 15 |

The prop kit is the true critical path. Everything downstream — labelling, training,
golden corpus, every differentiator — waits on footage, and footage waits on props. **Buy
them this week.**

If the Jetson is not purchased, M5 still runs on DEV. The architecture does not change; only
the export target does.

---

## 10. Working agreements

Trunk-based with short-lived branches: `feat/<area>-<slug>`. Every PR runs ruff, pytest,
the import-boundary rule, and the offline check. Golden replay tests must pass before any
merge that touches `reasoning/`.

Conventional commits. Weekly demo every Friday — something runs, or the week is flagged red.
Blockers raised same-day, never held to the weekly.

Docs in `docs/` are the contract. **A code change that contradicts a doc must update the
doc in the same PR.** These documents exist to keep the build aligned; a doc that drifts is
worse than no doc.

---

## 11. Risk register

| Risk | P | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Footage collection slips | High | Blocks everything downstream | Props by Sep 25; 10 runs in M0; 50 by end of M1 | R2 |
| False alerts above target | Med | Undermines the product thesis | Hysteresis tuning is a named M2 task; abstention path in M4 | R3 |
| Jetson not procured or won't port | Med | Weakens feasibility claim | Gate at Nov 15; DEV fallback with published numbers | R6 |
| MediaPipe fails on ARM | Med | Hands pipeline breaks on edge | Pose already on YOLO11-pose; hands fall back to a YOLO keypoint model | R1 |
| 150 GB storage exhausted | Med | Blocks synthetic scale-up | Budget in TRD §1.1; prune script; JPEG renders | R2 |
| Orientation invariance underperforms | Med | Loses the headline differentiator | Isolated in the canonicalizer; degrades to upright-only cleanly | R1 |
| Integration left too late | High | Classic hackathon failure | Weekly demo; M2 is end-to-end by design | R6 |
| Frontend polish consumes M4 | Med | Differentiators slip | Views frozen at end of M3; M4 adds panels only | R5 |
| Key member unavailable near finale | Med | Single-point failure | Every module has a named second reader | R6 |

---

## 12. Descope ladder

If behind schedule, cut in this order. Never cut upward.

1. ASR voice commands (F-15)
2. Free-float advisory (F-14)
3. Replay player UI — keep the CLI (E-10)
4. Session history UI — keep the API (E-09)
5. PROC-B live load — keep the architecture and explain it (F-08)
6. Edge port — publish DEV numbers instead (G-02…G-04)

**Never cut:** the state machine, abstention, telemetry hash chain, voice alerts, the crew
HUD, or orientation canonicalization. Those are the product.

---

## 13. Demo plan

### The 90-second version, for a rotating judge

1. **Framing (15 s)** — communication delay and downlink budget make ground supervision
   impossible. The system supervises the procedure on board.
2. **Live run (40 s)** — operator performs PROC-A. Deliberately skip step 4. The voice
   alert fires and the banner appears.
3. **The inversion (20 s)** — operator turns upside down, camera rotates. Toggle raw versus
   canonical. Tracking never breaks.
4. **The number (15 s)** — point at the downlink meter. State the ratio.

### The full fifteen-minute version

Adds: load PROC-B live at the jury's choosing · corrupt the telemetry and run the verifier ·
force an occlusion and show abstention rather than a guess · display live edge metrics ·
show the Blender pipeline generating labelled frames on demand.

### Rehearsal

Three full rehearsals in M6, at least one on unfamiliar hardware and one with the network
physically disconnected. Every team member must be able to deliver the 90-second version
alone — judges arrive unannounced and whoever is standing there has to carry it.

---

**Related:** [PRD](01-PRD.md) · [TRD](02-TRD.md) · [App flow](03-APP-FLOW.md) ·
[UI/UX brief](04-UIUX-BRIEF.md) · [Backend schema](05-BACKEND-SCHEMA.md)
