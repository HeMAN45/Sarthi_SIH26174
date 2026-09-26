# UI/UX Brief

**Project:** ORBITAL-HAR · SIH26174 · Team Hashira
**Version:** 1.2 · 2026-09-26 (as-built: engineering-console restyle, three themes, mirror)

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

**As built (2026-09-26):**

```
/            → Mission     (live console: next instruction + evidence + telemetry)
/procedures  → Procedures  (library + quick-sequence builder; hot-swap, App flow §9)
/models      → Models      (on-device classifier: Classes → Capture → Train → Deploy)
/archive     → Archive     (history, downloads, telemetry verification)
```

The section switch is an underlined tab strip in the top bar (keys `1`–`4`), beside the
run pill (phase · procedure · mission-elapsed clock), the subsystem strip (CAM · RACK ·
POSE · VOICE · LINK), mute, the theme menu and shutdown. The bar sheds detail as the
window narrows — key hints, then the procedure name, then labels — and never overflows.
Old routes (`/experiment`, `/train`, `/sessions`) redirect.

The camera is displayed **mirrored** by default, because a webcam facing the operator
otherwise moves the wrong way; a HUD toggle and `--no-mirror` give the true view. Only the
picture flips: perception, telemetry and the recording's evidence value are unaffected, and
the overlay is drawn after the flip so labels stay readable.

**Deferred:** the separate crew-only HUD (§3) and the replay player. The Mission console
serves both audiences for now — the current instruction is its hero element and the
evidence sits one glance below it. The original plan follows:

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

Tokens live in `ui/src/styles/theme.css`; this table mirrors the default theme.

### Colour — three themes, one token set

**Graphite** (default) is charcoal, not black: dark enough for a dim hall, light enough to
read at a glance. **Slate** is a cooler blue-grey with a cyan accent. **Daylight** is for
bright rooms and washed-out projectors. The camera stays a dark island in every theme.
The choice is per machine (browser storage) and applies before first paint.

| Token (Graphite) | Value | Use |
|---|---|---|
| `--bg` / `--bg-deep` | `#1A1C1F` / `#141618` | Page / top bar, insets |
| `--panel` | `#212428` | Panel |
| `--panel-2` / `--panel-3` | `#292D32` / `#30353B` | Raised, selected |
| `--well` | `#0F1113` | Camera |
| `--line` / `--line-2` | `#33383E` / `#40464D` | Rules — visible, not hairline-faint |
| `--ink` / `--ink-2` / `--ink-3` | `#F1EFE9` / `#C6C1B6` / `#9A958A` | Text, warm off-white |
| `--accent` | `#F39A2E` | Saffron — ISRO's colour. Active step, focus, primary action |
| `--ok` | `#5CC68E` | Complete, healthy |
| `--caution` | `#E8C64F` | Unverified, stalled, degraded |
| `--alert` | `#F06A63` | Skipped, out of order, fault |
| `--info` | `#B9A0F3` | Overridden, crew-attributed |

Contrast floor: 4.5:1 for all text against its background; 7:1 for the crew instruction.

### State language — colour is never the only cue

| State | Colour | Icon | Fill |
|---|---|---|---|
| Pending | `--ink-3` | step number | outline |
| Active | `--accent` | step number | solid, with a glow ring |
| Complete | `--ok` | check | tinted |
| Skipped | `--alert` | ✕ | tinted |
| Out of order | `--alert` | ⇄ | tinted |
| Unverified | `--caution` | ? | tinted |
| Stalled | `--caution` | hourglass | tinted |
| Overridden | `--info` | hand | tinted |

Every state also carries its word ("Skipped", "Interrupted" for the step a crew-ended run
stopped on), so the interface reads in greyscale, under projector colour shift, and for
colour-blind viewers. Step nodes are square-cornered tiles, not glowing dots. Icons are
Lucide, bundled; the mark is a monoline chariot wheel — a *sarthi* is a charioteer.

### Typography

**Geist** for instructions and prose; **Geist Mono** for headings, labels and all
numerics — FPS, confidence, durations, byte counts, clocks, hashes. Section titles are mono
capitals behind a square accent tick. Monospaced digits stop the layout jittering as values
update. Both fonts are bundled through `@fontsource` — never a CDN (§9).

Scale: 30 (current step, meters) / 28 (page titles) / 15 (body) / 12–13.5 mono uppercase
(labels). Nothing below 11.5px. Weights 400–700.

### Spacing and shape

4px base unit. Panel padding 18px, gaps 16px — 12px on screens under 820px tall, so a
720p projector still shows every panel without scrolling. Radius 8px panels, 6px controls,
4px chips. Borders 1px and visible. Flat surfaces: no glows, no gradients, no gradient
text. Emphasis is a 3px accent rule — the current step, the active tab, the debrief.

---

## 7. Alerts

| Severity | Treatment | Voice | Dismissal |
|---|---|---|---|
| High — skip, out of order | Full-width takeover, danger | Attention tone + urgent | Explicit acknowledge |
| Medium — unverified, stall | Banner above content, warn | Spoken once | Acknowledge or auto-resolve |
| Low — free float, degraded | Inline chip near the relevant panel | Spoken once, quiet | Auto-clears on recovery |

Never stack more than one high alert. A newer high alert replaces the older, and the older
remains in the ops timeline.

**As built:** alerts take over the top of the camera feed rather than the instruction zone.
High (red) and medium (amber) persist until acknowledged — button or `Esc`; low (blue)
clears itself after six seconds. Every alert remains in the Archive for that run.

---

## 8. Accessibility

Keyboard-operable throughout; `Space` confirms, `O` overrides, `M` mutes, `Esc`
acknowledges. (As built: `M`, `Esc` and `1`–`4` for sections; confirm and override wait
on their endpoints.) Visible 2px focus ring in `--accent`. All state conveyed by glyph and text as
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
scroll continuously. Nothing decorative moves, and all of it stops under
`prefers-reduced-motion`.

---

**Related:** [PRD](01-PRD.md) · [TRD](02-TRD.md) · [App flow](03-APP-FLOW.md) ·
[Backend schema](05-BACKEND-SCHEMA.md) · [Implementation plan](06-IMPLEMENTATION-PLAN.md)
