# UI/UX Brief

**Project:** ORBITAL-HAR · SIH26174 · Team Hashira
**Version:** 1.0 · 2026-09-20

---

## 1. Design principles

**Glanceability over density.** The crew operator has their hands inside a payload rack and
perhaps two seconds of spare attention. The primary view answers one question — *what do I
do next* — at a size readable from across a room.

**Never hide a degraded state.** Every subsystem failure, confidence drop, or dropped-frame
condition is visible. A system that looks healthy while running blind is worse than one that
looks broken.

**Two audiences, two views.** The crew needs less. Ground operations needs everything.
Trying to serve both in one screen fails both.

**Mission console, not consumer app.** Dark, flat, high-contrast, monospaced numerics. This
is not decoration — the finale is judged in a large, often dimly lit hall, frequently via a
projector, and a light-themed dashboard washes out badly under those conditions.

**Demonstrable, not just functional.** Every differentiator claim has a visible on-screen
counterpart. If the jury cannot see it, it did not happen.

---

## 2. Information architecture

```
/            → Crew HUD          (default; full screen)
/ops         → Ground Operations (the jury-facing view)
/sessions    → Session history + telemetry verification
/replay/:id  → Replay player
```

A persistent view-switch lives in the top-right of every screen. During a demo both views
run simultaneously on two displays where available — the contrast between them *is* the
mission-realism argument.

---

## 3. Crew HUD

Single purpose: the next action, and whether anything is wrong.

```
┌──────────────────────────────────────────────────────────────┐
│  PROC-A · Nested sample retrieval          ● LIVE    ⏻ ♪ ⚙   │  56px
├──────────────────────────────────────────────────────────────┤
│                                                               │
│   STEP 4 OF 6                                                 │
│                                                               │
│   Open the red box                                            │  72px
│                                                               │
│   ████████████████████░░░░░░░░░░░░                            │
│                                                               │
├──────────────────────────────────────────────────────────────┤
│  ✓ 1  ✓ 2  ✓ 3  ● 4  ○ 5  ○ 6                                │  88px
├──────────────────────────────────────────────────────────────┤
│  [ Confirm step ]        [ Override ]        [ Mark anomaly ] │  80px
└──────────────────────────────────────────────────────────────┘
```

### Specification

| Element | Spec |
|---|---|
| Step counter | 20px, muted, uppercase, letter-spaced |
| **Instruction text** | **72px, weight 500, primary — the single most important element on screen** |
| Progress bar | 8px tall, full width, accent fill |
| Step pips | 88px row, one pip per step, state-coloured with a state glyph |
| Actions | 56px tall, minimum 200px wide — assume gloved or imprecise input |
| Live indicator | Pulsing dot; turns amber when degraded, red when stopped |

If the instruction exceeds two lines at 72px, it scales down to a 48px floor, then
truncates with the full text available on the ops view. Procedure prompts should be written
short enough that this never triggers.

### Alert state

An alert takes over the instruction zone entirely — it does not appear as a toast or a
corner notification. The crew must not be able to miss it.

```
┌──────────────────────────────────────────────────────────────┐
│  ⚠  STEP SKIPPED                                              │  red bg
│                                                               │
│  Step 4 was not completed                                     │  48px
│  Open the red box                                             │  64px
│                                                               │
│  [ Acknowledge ]            [ Go back to step 4 ]             │
└──────────────────────────────────────────────────────────────┘
```

Persists until acknowledged or until the underlying condition resolves. No auto-dismiss —
an alert that disappears on its own may as well not have fired.

---

## 4. Ground Operations view

The jury-facing screen. Everything, densely, without becoming unreadable.

```
┌───────────────────────────────────────────────────────────────────────┐
│ ORBITAL-HAR   PROC-A   SESSION 2026-09-20T13:24   ● LIVE    [Crew ▸]  │
├────────────────────────────────────┬──────────────────────────────────┤
│                                    │  PROCEDURE                       │
│      LIVE VIDEO + OVERLAYS         │  ✓ 1 Open outer container  12.4s │
│                                    │  ✓ 2 Remove red box        18.1s │
│      bboxes · hand skeleton        │  ✓ 3 Remove yellow box     15.7s │
│      pose · rack axes              │  ● 4 Open the red box      04.2s │
│                                    │  ○ 5 Transfer vial               │
│                                    │  ○ 6 Close and secure            │
│  [ raw | canonical ]  [overlays ▾] ├──────────────────────────────────┤
├────────────────────────────────────┤  EVIDENCE — step 4               │
│  CONFIDENCE                        │  detect red_box_open      0.91 ✓ │
│  ▁▂▄▆█▇▆▅▆▇█▇▆  0.91               │  contact hand,red_box     0.84 ✓ │
│  ─────────────── τ complete        │  hold 12/12 frames             ✓ │
│  ─────────────── τ abstain         ├──────────────────────────────────┤
├────────────────────────────────────┤  TELEMETRY                       │
│  HEALTH                            │  written        11.4 KB          │
│  22 FPS · 41ms · 3.1GB · RTX       │  raw video eq.  1.94 GB          │
│  rack ● 4/4   cam ●   media ●      │  ratio          170,000 : 1      │
│                                    │  chain          ✓ verified       │
└────────────────────────────────────┴──────────────────────────────────┘
```

### Panels

| Panel | Contents | Why it earns its space |
|---|---|---|
| Video | Live preview, toggleable overlays, raw/canonical toggle | The raw/canonical toggle *is* the orientation demo — the jury sees the frame straighten |
| Procedure | All steps, states, durations | The at-a-glance verdict record |
| Evidence | Live predicate values for the active step | Answers "why did it decide that?" instantly |
| Confidence | Rolling trace with threshold lines drawn | Makes abstention legible rather than mysterious |
| Telemetry | Bytes written, raw-video equivalent, ratio, chain status | The downlink argument, live, as a number |
| Health | FPS, latency, memory, device, per-subsystem dots | The edge-feasibility argument, live |

The evidence panel is the highest-value element for jury credibility and the one most
likely to get cut for time. Do not cut it.

---

## 5. Component inventory

| Component | Notes |
|---|---|
| `StepCard` | Crew HUD instruction zone; handles alert takeover |
| `StepPips` | Compact state row |
| `StepTimeline` | Ops list with durations and state glyphs |
| `EvidencePanel` | Predicate rows with live values |
| `ConfidenceTrace` | Sparkline with threshold rules |
| `VideoPane` | MJPEG preview, overlay toggles, raw/canonical switch |
| `TelemetryMeter` | Byte counter, ratio, chain badge |
| `HealthStrip` | FPS, latency, memory, subsystem dots |
| `AlertBanner` | Full-width takeover |
| `ControlBar` | Start, stop, confirm, override, mute |
| `ProcedureLoader` | Hot-swap picker with validation errors |
| `SessionTable` | History, download, verify |
| `ReplayControls` | Scrub, speed, step |

---

## 6. Design tokens

### Colour — dark base

| Token | Value | Use |
|---|---|---|
| `--bg-0` | `#0B0E11` | Page |
| `--bg-1` | `#141A1F` | Panel |
| `--bg-2` | `#1D252C` | Raised |
| `--border` | `#2A343D` | Hairlines |
| `--text-0` | `#E8EDF2` | Primary |
| `--text-1` | `#9AA7B4` | Secondary |
| `--text-2` | `#5F6D7A` | Muted |
| `--accent` | `#3B9EFF` | Active step, focus |
| `--ok` | `#35C98B` | Complete, healthy |
| `--warn` | `#F0A63C` | Unverified, stalled, degraded |
| `--danger` | `#F2545B` | Skipped, out of order, fault |
| `--info` | `#9B7DF0` | Overridden, crew-attributed |

Contrast floor: 4.5:1 for all text against its background; 7:1 for the crew instruction.

### State language — colour is never the only cue

| State | Colour | Glyph | Fill |
|---|---|---|---|
| Pending | `--text-2` | `○` | none |
| Active | `--accent` | `●` | solid |
| Complete | `--ok` | `✓` | solid |
| Skipped | `--danger` | `✕` | solid |
| Out of order | `--danger` | `⇄` | hatched |
| Unverified | `--warn` | `?` | hatched |
| Stalled | `--warn` | `⏱` | outline |
| Overridden | `--info` | `⊙` | outline |

Hatching and glyphs mean the interface remains readable in greyscale, under projector
colour shift, and for colour-blind viewers.

### Typography

Sans (Inter or system) for prose; **monospace for all numerics** — FPS, confidence,
durations, byte counts. Monospaced digits stop the layout jittering as values update, which
matters a great deal on a live dashboard being watched by a judge.

Scale: 72 / 48 / 32 / 20 / 16 / 14 / 12 px. Weights 400 and 500 only.

### Spacing and shape

4px base unit. Panel padding 20px, gaps 16px. Radius 8px panels, 6px controls. Borders
1px hairline. No shadows, no gradients.

---

## 7. Alerts

| Severity | Treatment | Voice | Dismissal |
|---|---|---|---|
| High — skip, out of order | Full-width takeover, danger | Attention tone + urgent | Explicit acknowledge |
| Medium — unverified, stall | Banner above content, warn | Spoken once | Acknowledge or auto-resolve |
| Low — free float, degraded | Inline chip near the relevant panel | Spoken once, quiet | Auto-clears on recovery |

Never stack more than one high alert. A newer high alert replaces the older, and the older
remains in the ops timeline.

---

## 8. Accessibility

Keyboard-operable throughout; `Space` confirms, `O` overrides, `M` mutes, `Esc`
acknowledges. Visible 2px focus ring in `--accent`. All state conveyed by glyph and text as
well as colour. Live regions announce step changes and alerts to screen readers. Motion
respects `prefers-reduced-motion` — pulses become static.

Targets are sized for gloved, imprecise input: 56px minimum height, 200px minimum width on
primary actions.

---

## 9. Demo conditions

Built for a hall, not a desk. Design at 1920×1080, verified legible at 3 m from a 24-inch
display and via projector. The crew HUD must be readable from the back of a demo booth.

All assets — fonts, icons, styles — are bundled and served locally. **A CDN font link is a
network call and invalidates the offline claim**; a judge who opens the network tab will
find it.

Sensible defaults on load: `/ops` for the jury, `/` for a crew-perspective demo. No
onboarding, no modals, no tooltips requiring hover — the demo operator's hands are busy
performing the procedure.

---

## 10. Motion

Purposeful only. Step transitions cross-fade at 160 ms. Alerts appear instantly — no
entrance animation on anything urgent. The live dot pulses at 1 Hz. Confidence traces
scroll continuously. Nothing else moves.

---

**Related:** [PRD](01-PRD.md) · [TRD](02-TRD.md) · [App flow](03-APP-FLOW.md) ·
[Backend schema](05-BACKEND-SCHEMA.md) · [Implementation plan](06-IMPLEMENTATION-PLAN.md)
