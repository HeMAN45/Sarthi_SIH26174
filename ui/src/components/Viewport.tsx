import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bell, CameraOff, Hand, MirrorRectangular, PackageX, PersonStanding, Play, RefreshCw, ShieldAlert,
  TriangleAlert,
} from "lucide-react";
import { api } from "../lib/api";
import type { LiveState } from "../lib/api";
import { fmtClock } from "../lib/format";
import type { Mode } from "../lib/modes";
import { detectorLabel, MODE_HINT, MODE_OPTIONS } from "../lib/modes";
import { Mark } from "./Mark";
import { Stream } from "./Stream";
import { Seg } from "./ui";

/* ------------------------------------------------------------------ frame */

export function Viewport({ children }: { children: ReactNode }) {
  return <div className="viewport">{children}</div>;
}

/** Selfie-style display. The camera faces the operator, so unmirrored video
 *  moves the wrong way; mirrored, it behaves like a mirror. Display only. */
export function MirrorToggle({ s }: { s: LiveState }) {
  const on = s.camera.mirror;
  return (
    <button className={`hud-tag${on ? " on" : ""}`} onClick={() => api.mirror(!on)}
            title={on ? "Mirrored like a selfie - click for the camera's true view"
                      : "True camera view - click to mirror it like a selfie"}>
      <MirrorRectangular size={15} /> {on ? "Mirror on" : "Mirror off"}
    </button>
  );
}

/** The live feed with its heads-up display. Mounted only while the camera is
 *  on, so a stopped camera holds no stream open. */
export function Feed({ s }: { s: LiveState }) {
  const p = s.perception;
  const live = s.phase === "live";
  const recording = live && !!s.session && !s.session.closed;
  const camLive = s.camera.state === "live";
  return (
    <>
      <Stream className="feed" alt="Live payload camera with detection overlays" />
      <div className="shade" />
      <span className="corner tl" /><span className="corner tr" />
      <span className="corner bl" /><span className="corner br" />

      <div className="hud top">
        {recording ? (
          <span className="hud-tag rec"><i />Rec <b>{fmtClock(s.session!.elapsed_s)}</b></span>
        ) : s.phase === "complete" ? (
          <span className="hud-tag ok">Run sealed</span>
        ) : live ? (
          // Started, but no frame yet: nothing is judged or logged until one arrives.
          <span className="hud-tag">Arming</span>
        ) : (
          <span className="hud-tag">Preview · not judged</span>
        )}
        {s.session?.rtsp && <span className="hud-tag">RTSP</span>}
        <span className="spacer" />
        <span className="hud-tag">Cam {s.camera.index}</span>
        {camLive && <span className="hud-tag"><b>{s.fps.toFixed(1)}</b> fps</span>}
      </div>

      <div className="hud bottom">
        {live && <span className="hud-tag">{s.mode} mode</span>}
        <span className="hud-tag">{detectorLabel(p.detector, p.trained)}</span>
        <span className="spacer" />
        {p.rack_required && (
          <span className={`hud-tag ${p.rack_locked ? "ok" : "bad"}`}>
            Rack {p.rack_locked ? "locked" : "lost"}
          </span>
        )}
        {p.pose_required && !p.pose_ok && <span className="hud-tag bad">Pose off</span>}
        <button className={`hud-tag${p.body.enabled ? " on" : ""}`} disabled={p.body.forced}
                onClick={() => api.body(!p.body.enabled)}
                title={p.body.forced ? "This procedure's steps need body tracking"
                  : p.body.enabled ? "Body tracking on - click to save CPU" : "Body tracking off - click to turn on"}>
          <PersonStanding size={15} /> Body {p.body.enabled ? "on" : "off"}
        </button>
        <MirrorToggle s={s} />
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- overlays */

type PfTone = "ok" | "warn" | "bad" | "";

/** One pre-flight line, read like a checklist: [ok] DETECTOR stand-in. */
function Preflight({ label, value, tone = "" }: { label: string; value: string; tone?: PfTone }) {
  const mark = tone === "ok" ? "[ok]" : tone === "warn" ? "[!!]" : tone === "bad" ? "[xx]" : "[--]";
  return (
    <div className={`pf ${tone}`}>
      <span className="pf-mark">{mark}</span>
      <span className="pf-label">{label}</span>
      <span className="pf-value">{value}</span>
    </div>
  );
}

/** Ready: the camera is off. Says what will happen, and what is and is not
 *  working, before anything is switched on. */
export function Standby({
  s, mode, setMode, onStart,
}: { s: LiveState; mode: Mode; setMode: (m: Mode) => void; onStart: () => void }) {
  const p = s.perception;
  const preview = s.camera.on;
  return (
    <div className="overlay standby">
      <div className="standby-body fade-in">
        <Mark size={52} />
        <div>
          <div className="bracket">[ {preview ? "preview · camera on" : "standby · camera off"} ]</div>
          <h2 className="standby-title">Ready for a run</h2>
        </div>
        <p className="standby-text">
          <b style={{ color: "var(--ink)" }}>New run</b> turns the camera on, opens a sealed
          telemetry log and starts judging <b style={{ color: "var(--ink)" }}>{s.procedure}</b>.
        </p>

        <div className="preflight">
          <Preflight label="Procedure" tone="ok" value={`${s.steps.length} steps · ${s.procedure_id}`} />
          <Preflight label="Detector" tone="ok" value={detectorLabel(p.detector, p.trained)} />
          <Preflight label="Rack frame" value={p.rack_required ? "Required - locks when live" : "Not needed"} />
          <Preflight label="Body tracking" tone={!p.body.enabled ? "" : p.pose_ok ? "ok" : "bad"}
                     value={!p.body.enabled ? "Off - pose, hands and gestures paused"
                       : p.pose_ok ? `YOLO11-pose · hands · gestures${p.body.forced ? " · needed by this procedure" : ""}`
                       : `Offline: ${p.pose_error ?? "unavailable"}`} />
          <Preflight label="Voice" tone={s.voice.available ? "ok" : "warn"}
                     value={s.voice.available ? `On-device · ${s.voice.model}` : "Browser fallback"} />
          <Preflight label="Camera"
                     value={`Device ${s.camera.index} · ${preview ? "preview" : "off"} · mirror ${s.camera.mirror ? "on" : "off"}`} />
        </div>

        <div className="startbar">
          <Seg value={mode} onChange={setMode} options={MODE_OPTIONS} />
          <button className="btn btn-primary btn-lg" onClick={onStart}>
            <Play size={17} fill="currentColor" /> New run
          </button>
        </div>
        <div className="faint" style={{ fontSize: 14 }}>{MODE_HINT[mode]}</div>
      </div>
    </div>
  );
}

export function Starting() {
  return (
    <div className="overlay dim">
      <div className="col fade-in" style={{ alignItems: "center", gap: 14 }}>
        <div className="spinner" />
        <div className="bracket">[ powering the camera ]</div>
      </div>
    </div>
  );
}

export function Unavailable({
  s, onRetry, onStop,
}: { s: LiveState; onRetry: () => void; onStop: () => void }) {
  return (
    <div className="overlay standby">
      <div className="standby-body fade-in">
        <div className="empty-icon" style={{ color: "var(--alert)", borderColor: "var(--alert-line)",
                                             background: "var(--alert-soft)", width: 58, height: 58 }}>
          <CameraOff size={26} />
        </div>
        <div>
          <div className="bracket" style={{ color: "var(--alert)" }}>[ camera unavailable ]</div>
          <h2 className="standby-title">No frames from device {s.camera.index}</h2>
        </div>
        <p className="standby-text">
          {sentence(s.camera.detail ?? "The camera stopped delivering frames")} Nothing is being judged
          and no run is logged until frames arrive. Check the cable, or close any app holding the camera.
        </p>
        <div className="startbar">
          <button className="btn btn-primary" onClick={onRetry}><RefreshCw size={16} /> Try again</button>
          <button className="btn" onClick={onStop}>Turn camera off</button>
        </div>
      </div>
    </div>
  );
}

/** "camera 99 unavailable" -> "Camera 99 unavailable." */
function sentence(text: string): string {
  const t = text.trim();
  return t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".");
}

/* ------------------------------------------------------------------ alerts */

const KIND_ICON: Record<string, LucideIcon> = {
  skip: ShieldAlert,
  out_of_order: ShieldAlert,
  wrong_object: PackageX,
  wrong_hand: Hand,
};

/** An alert takes over the top of the feed and stays until acknowledged.
 *  It appears instantly: nothing urgent gets an entrance animation. */
export function AlertBanner({
  alert, onAck,
}: { alert: NonNullable<LiveState["alert"]>; onAck: () => void }) {
  const sev = alert.severity === "high" ? "high" : alert.severity === "medium" ? "medium" : "low";
  const Icon = KIND_ICON[alert.kind] ?? (sev === "low" ? Bell : TriangleAlert);
  return (
    <div className={`alert-banner ${sev}`} role="alert" aria-live="assertive">
      <span className="ab-icon"><Icon size={28} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="ab-kind">{alert.kind.replace(/_/g, " ")}</div>
        <div className="ab-msg">{alert.message}</div>
      </div>
      <button className="btn" onClick={onAck} title="Acknowledge (Esc)">Acknowledge</button>
    </div>
  );
}
