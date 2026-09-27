import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight, Check, CircleDashed, Clock3, Link2, ListChecks, Mic, PersonStanding, Play, RadioTower,
  RotateCcw, SkipForward, Square,
} from "lucide-react";
import { api } from "../lib/api";
import type { LiveState, Step } from "../lib/api";
import type { Traces } from "../lib/useLive";
import { useAlert } from "../lib/useLive";
import { fmtBytes, fmtClock, fmtRatio, shortHash } from "../lib/format";
import Debrief from "../components/Debrief";
import { gestureLabel } from "../lib/gestures";
import { meta } from "../lib/states";
import { Timeline } from "../components/steps";
import { Badge, Note, PanelHead, Seg } from "../components/ui";
import type { Mode } from "../lib/modes";
import { MODE_HINT, MODE_OPTIONS } from "../lib/modes";
import { AlertBanner, Feed, Standby, Starting, Unavailable, Viewport } from "../components/Viewport";
import { Bar, Ring, Sparkline } from "../components/viz";

export default function Mission({
  state, traces, resetSpeech,
}: { state: LiveState | null; traces: Traces; resetSpeech: () => void }) {
  const s = state;
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("clean");
  const [hidden, setHidden] = useState<string | null>(null);
  const { visible: alertOn, acknowledge } = useAlert(s?.alert);

  // Esc acknowledges, as the UI brief specifies (§8).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && alertOn) acknowledge(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [alertOn, acknowledge]);

  if (!s) {
    return (
      <div className="mission" style={{ placeItems: "center", display: "grid", gridTemplateColumns: "1fr" }}>
        <div className="col" style={{ alignItems: "center" }}>
          <div className="spinner" />
          <div className="eyebrow">Connecting to the supervisor</div>
        </div>
      </div>
    );
  }

  const start = (m: Mode = mode) => { resetSpeech(); setHidden(null); api.start(m); };
  const end = () => { api.stop(); };

  const phase = s.phase;
  const cam = s.camera.state;
  const steps = s.steps;
  const total = steps.length;
  const resolved = steps.filter((x) => meta(x.state).done).length;
  const active = steps.find((x) => x.state === "active");
  const upNext = s.next ? steps.find((x) => x.id === s.next!.id) : undefined;
  const focus: Step | undefined = active ?? upNext;
  const focusIdx = focus ? steps.indexOf(focus) : -1;
  const sess = s.session;
  const sealed = !!sess?.closed;
  const debrief = sealed && hidden !== sess!.id && (phase === "complete" || phase === "ready");

  return (
    <div className="mission">
      {/* ========================================================== feed */}
      <div className="mission-main">
        <Viewport>
          {s.camera.on && <Feed s={s} />}
          {phase === "live" && alertOn && s.alert && <AlertBanner alert={s.alert} onAck={acknowledge} />}

          {debrief ? (
            <Debrief
              s={s}
              onNewRun={() => start()}
              onEnd={phase === "complete" ? end : undefined}
              onHide={() => setHidden(sess!.id)}
            />
          ) : cam === "starting" ? (
            <Starting />
          ) : cam === "unavailable" ? (
            <Unavailable s={s} onRetry={() => start()} onStop={end} />
          ) : phase === "ready" && !s.camera.on ? (
            <Standby s={s} mode={mode} setMode={setMode} onStart={() => start()} />
          ) : null}
        </Viewport>

        <Metrics s={s} resolved={resolved} total={total} />
      </div>

      {/* ========================================================= state */}
      <div className="mission-side">
        <Hero s={s} focus={focus} focusIdx={focusIdx} resolved={resolved} total={total}
              trace={traces.confidence} alertOn={alertOn} />

        <div className="panel timeline-panel">
          <PanelHead
            icon={ListChecks}
            title="Procedure"
            right={<span className="mono faint" style={{ fontSize: 13 }}>{resolved}/{total} resolved</span>}
          />
          <Timeline steps={steps} live={phase === "live"} />
        </div>

        <div className="panel dock">
          {phase === "live" ? (
            <>
              <div className="dock-grid">
                <button className="btn" onClick={() => api.skip()} title="Mark the current step skipped">
                  <SkipForward size={15} /> Skip step
                </button>
                <button className="btn" onClick={() => start(s.mode === "strict" ? "strict" : "clean")}
                        title="Seal this run and start a fresh one">
                  <RotateCcw size={15} /> Restart
                </button>
              </div>
              <button className="btn btn-danger btn-block" onClick={end}
                      title="Seal the hash chain and turn the camera off">
                <Square size={13} fill="currentColor" /> End run
              </button>
            </>
          ) : phase === "complete" ? (
            <div className="dock-grid">
              <button className="btn btn-primary" onClick={() => start()}>
                <Play size={14} fill="currentColor" /> New run
              </button>
              <button className="btn" onClick={end} title="Turn the camera off">
                <Square size={13} fill="currentColor" /> End run
              </button>
            </div>
          ) : (
            <>
              <Seg value={mode} onChange={setMode} options={MODE_OPTIONS} block />
              <button className="btn btn-primary btn-block" onClick={() => start()}>
                <Play size={14} fill="currentColor" /> New run
              </button>
              <div className="row faint" style={{ fontSize: 13, gap: 6 }}>
                <span className="ellipsis" style={{ flex: 1 }}>{MODE_HINT[mode]}</span>
                <button className="btn btn-xs btn-ghost" onClick={() => nav("/procedures")}>
                  Change procedure <ArrowRight size={12} />
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ================================================================== hero */

function Hero({
  s, focus, focusIdx, resolved, total, trace, alertOn,
}: {
  s: LiveState; focus?: Step; focusIdx: number; resolved: number; total: number;
  trace: number[]; alertOn: boolean;
}) {
  const phase = s.phase;
  const p = s.perception;
  const complete = phase === "complete" || s.complete;
  const frac = total ? resolved / total : 0;

  // Degradation is announced, never silent (invariant #10).
  const degraded: string[] = [];
  if (phase === "live" && s.camera.state === "live") {
    if (p.rack_required && !p.rack_locked) degraded.push("Rack not locked - positions unavailable");
    if (p.pose_required && !p.pose_ok) degraded.push("Pose offline - contact steps cannot verify");
  }

  const eyebrow = complete ? "Procedure complete"
    : phase === "live" && focus ? `Step ${String(focusIdx + 1).padStart(2, "0")} of ${String(total).padStart(2, "0")}`
    : s.session?.closed ? "Run ended" : "Standing by";

  const title = complete
    ? (s.summary.skipped || s.summary.out_of_order ? "Completed with deviations" : "All steps verified")
    : phase === "live" ? focus?.name ?? "Waiting for the first step"
    : s.procedure;

  const conf = focus?.state === "active" ? focus.confidence : null;
  const th = s.thresholds;
  const confColor = conf == null ? "var(--ink-3)"
    : conf >= th.complete ? "var(--ok)" : conf >= th.abstain ? "var(--accent)" : "var(--caution)";

  return (
    <div className={`panel hero${alertOn ? " is-alert" : complete ? " is-done" : ""}`}>
      <div className="hero-top">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className="eyebrow" style={phase === "live" ? { color: "var(--accent-2)" } : undefined}>{eyebrow}</span>
            {phase === "live" && focus && focus.state !== "active" && focus.state !== "pending" && (
              <Badge tone="caution">{meta(focus.state).label}</Badge>
            )}
          </div>
          <h2 className={`hero-name${phase === "live" || complete ? "" : " quiet"}`}>{title}</h2>
        </div>
        <Ring value={frac} size={62} stroke={5} color={complete ? "var(--ok)" : "var(--accent)"}>
          <div className="mono" style={{ fontSize: 14.5, fontWeight: 600 }}>{Math.round(frac * 100)}%</div>
        </Ring>
      </div>

      {phase === "live" && focus?.voice && (
        <div className="hero-voice"><Mic size={14} /><span>“{focus.voice}”</span></div>
      )}
      {phase !== "live" && !complete && (
        <div className="hero-voice">
          <span>
            {total} steps{s.steps[0] ? <> · first: <b className="muted">{s.steps[0].name}</b></> : null}.
            {" "}Press <b className="muted">New run</b> to begin.
          </span>
        </div>
      )}

      {phase === "live" && (
        <div className="trace">
          <div className="trace-head">
            <span className="eyebrow">Evidence confidence</span>
            <span className="spacer" />
            <span className="trace-value" style={{ color: confColor }}>
              {conf == null ? "-" : conf.toFixed(2)}
            </span>
          </div>
          <Sparkline values={trace} height={52} color={confColor}
                     rules={[{ y: th.complete, color: "var(--ok)" }, { y: th.abstain, color: "var(--caution)" }]} />
          <div className="trace-legend">
            <span style={{ color: "var(--ok)" }}><i />τ complete {th.complete.toFixed(2)}</span>
            <span style={{ color: "var(--caution)" }}><i />τ abstain {th.abstain.toFixed(2)}</span>
          </div>
          <div className="evidence">
            {focus && focus.evidence.length > 0
              ? focus.evidence.map((raw) => {
                  const ev = parseEvidence(raw);
                  return (
                    <div key={raw} className={`ev${ev.ok ? " ok" : ""}`}>
                      {ev.ok ? <Check size={13} strokeWidth={2.6} /> : <CircleDashed size={13} />}
                      <span className="ev-label">{ev.label}</span>
                      <span className="ev-detail ellipsis">{ev.ok ? "" : ev.detail}</span>
                      <span className="ev-conf">{ev.conf == null ? "" : ev.conf.toFixed(2)}</span>
                    </div>
                  );
                })
              : <div className="ev"><CircleDashed size={13} /><span className="ev-label">{focus?.reason || "awaiting evidence"}</span></div>}
          </div>
        </div>
      )}

      {degraded.length > 0 && (
        <Note tone="caution" style={{ marginTop: 12 }}>{degraded.join(" · ")}</Note>
      )}
    </div>
  );
}

/** Engine evidence reads ``detect:bottle=no:bottle not held@0.42``
 *  (predicates.describe). Shown as a checklist, not as a raw string. */
function parseEvidence(raw: string): { label: string; ok: boolean; detail: string; conf: number | null } {
  const m = raw.match(/^(.*)=(ok|no):(.*)@([\d.]+)$/);
  if (!m) return { label: raw, ok: false, detail: "", conf: null };
  return { label: m[1].replace(/:/g, " · "), ok: m[2] === "ok", detail: m[3], conf: Number(m[4]) };
}

/* ================================================================== body */

/** What the body tracker sees: whether anyone is there, the actions it reads,
 *  and what each hand is touching. The PS asks for pose and hand-object
 *  interaction; this is where they are visible. */
function BodyCard({ s, camLive }: { s: LiveState; camLive: boolean }) {
  const b = s.perception.body;
  const state = !b.enabled ? "Off" : !b.ready ? "Unavailable" : !camLive ? "Standby" : b.tracked ? "Tracking" : "No one in view";
  const on = b.enabled && b.ready && camLive && b.tracked;
  return (
    <div className={`panel metric${on ? "" : " metric-idle"}`}>
      <div className="metric-head">
        <PersonStanding size={14} />
        <span className="eyebrow">Body</span>
        <span className="spacer" />
        {b.forced
          ? <Badge tone="accent">Needed</Badge>
          : <button className="btn btn-xs btn-ghost" onClick={() => api.body(!b.enabled)}
                    title={b.enabled ? "Turn body tracking off to save CPU" : "Turn body tracking on"}>
              {b.enabled ? "Turn off" : "Turn on"}
            </button>}
      </div>
      <div className="metric-value" style={{ fontSize: 22, fontFamily: "var(--sans)", color: on ? "var(--ink)" : undefined }}>
        {state}
      </div>
      <div className="push" style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: 25 }}>
        {on && b.gestures.length === 0 && b.contacts.length === 0 && (
          <span className="faint" style={{ fontSize: 13 }}>No gesture · hands free</span>
        )}
        {on && b.gestures.map((g) => (
          <Badge key={`${g.name}-${g.side}`} tone="accent">
            {gestureLabel(g.name)}{g.side === "both" ? "" : ` · ${g.side[0].toUpperCase()}`}
          </Badge>
        ))}
        {on && b.contacts.map((c) => (
          <Badge key={`${c.side}-${c.object}`} tone="ok">{c.side[0].toUpperCase()} hand → {c.object}</Badge>
        ))}
        {!b.enabled && <span className="faint" style={{ fontSize: 13 }}>Pose, hands and gestures paused</span>}
      </div>
    </div>
  );
}

/* =============================================================== metrics */

function Metrics({
  s, resolved, total,
}: { s: LiveState; resolved: number; total: number }) {
  const sess = s.session;
  const bytes = sess?.bytes ?? 0;
  const video = sess?.video_bytes ?? 0;
  const ratio = bytes > 0 && video > 0 ? Math.round(video / bytes) : 0;
  const [head, tail] = shortHash(sess?.head);
  const camLive = s.camera.state === "live";

  return (
    <div className="metrics">
      {/* The downlink argument, measured rather than estimated: bytes of
          telemetry against the bytes of video it replaces. */}
      <div className={`panel metric${ratio ? "" : " metric-idle"}`}>
        <div className="metric-head"><RadioTower size={14} /><span className="eyebrow">Downlink saved</span></div>
        <div className={`metric-value${ratio ? " accent" : ""}`}>
          {ratio ? <>{fmtRatio(ratio)}<small>×</small></> : sess ? "measuring" : "no run"}
        </div>
        <div className="compare">
          <div className="compare-row">
            <span>VIDEO</span>
            <div className="compare-bar"><i style={{ width: video ? "100%" : 0, background: "var(--ink-4)" }} /></div>
            <span className="v">{fmtBytes(video)}</span>
          </div>
          <div className="compare-row">
            <span>TELEM</span>
            <div className="compare-bar">
              <i style={{ width: video ? `${Math.min(100, Math.max(1.5, (bytes / video) * 100))}%` : 0, background: "var(--accent)" }} />
            </div>
            <span className="v">{fmtBytes(bytes)}</span>
          </div>
        </div>
      </div>

      <div className={`panel metric${bytes ? "" : " metric-idle"}`}>
        <div className="metric-head">
          <Link2 size={14} /><span className="eyebrow">Telemetry</span>
          <span className="spacer" />
          {sess && (sess.closed ? <Badge tone="ok">Sealed</Badge> : <Badge tone="accent">Writing</Badge>)}
        </div>
        <div className="metric-value mono">{fmtBytes(bytes)}</div>
        <div className="metric-sub">{sess ? `${sess.records} records · SHA-256 chained` : "Opens when a run starts"}</div>
        <div className="push mono faint" style={{ fontSize: 12.5 }}>
          {head ? <>head <span className="muted">{head}</span>…{tail}</> : " "}
        </div>
      </div>

      <div className={`panel metric${sess ? "" : " metric-idle"}`}>
        <div className="metric-head"><Clock3 size={14} /><span className="eyebrow">Mission clock</span></div>
        <div className="metric-value mono">{sess ? fmtClock(sess.elapsed_s) : "00:00:00"}</div>
        <div className="metric-sub">{resolved} of {total} steps resolved</div>
        <div className="push"><Bar value={total ? resolved / total : 0}
                                   color={s.complete ? "var(--ok)" : "var(--accent)"} /></div>
      </div>

      <BodyCard s={s} camLive={camLive} />
    </div>
  );
}
