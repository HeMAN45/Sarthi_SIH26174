import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Check, Palette, Power, Volume2, VolumeX } from "lucide-react";
import type { LiveState } from "../lib/api";
import { fmtClock } from "../lib/format";
import { NAV } from "../lib/nav";
import type { Theme } from "../lib/theme";
import { THEMES } from "../lib/theme";
import { Mark } from "./Mark";

type Sys = { key: string; label: string; tone: "ok" | "warn" | "bad" | "off" | "busy"; title: string };

/** Subsystem health lives in the bar, so a degraded run is never a surprise
 *  discovered halfway down a page (invariant #10). */
function systems(s: LiveState | null, connected: boolean): Sys[] {
  const out: Sys[] = [];
  const cam = s?.camera;
  out.push({
    key: "cam",
    label: "CAM",
    tone: !cam || cam.state === "off" ? "off"
      : cam.state === "starting" ? "busy"
      : cam.state === "live" ? "ok" : "bad",
    title: !cam ? "Camera" : cam.state === "unavailable" ? `Camera: ${cam.detail}` : `Camera ${cam.index}: ${cam.state}`,
  });
  const p = s?.perception;
  const camLive = cam?.state === "live";
  if (p?.rack_required) {
    out.push({
      key: "rack", label: "RACK",
      tone: !camLive ? "off" : p.rack_locked ? "ok" : "bad",
      title: p.rack_locked ? "Rack frame locked" : "Rack not locked - no rack-frame positions",
    });
  }
  if (p?.pose_required) {
    out.push({
      key: "pose", label: "POSE",
      tone: !p.pose_ok ? "bad" : camLive ? "ok" : "off",
      title: p.pose_ok ? "Pose estimation online" : `Pose offline: ${p.pose_error ?? "unavailable"}`,
    });
  }
  out.push({
    key: "voice", label: "VOICE",
    tone: !s ? "off" : s.voice.available ? "ok" : "warn",
    title: s?.voice.available
      ? `On-device voice (${s.voice.model})`
      : `No on-device voice - this browser speaks instead${s?.voice.reason ? ` (${s.voice.reason})` : ""}`,
  });
  out.push({
    key: "link", label: "LINK",
    tone: connected ? "ok" : "bad",
    title: connected ? "Connected to the supervisor" : "Supervisor unreachable - reconnecting",
  });
  return out;
}

/** A small popover that closes on outside click or Escape. */
function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return [open, setOpen, ref] as const;
}

export default function TopBar({
  state, connected, muted, onMute, onShutdown, theme, onTheme,
}: {
  state: LiveState | null;
  connected: boolean;
  muted: boolean;
  onMute: () => void;
  onShutdown: () => void;
  theme: Theme;
  onTheme: (t: Theme) => void;
}) {
  const [powerOpen, setPowerOpen, powerRef] = usePopover();
  const [paletteOpen, setPaletteOpen, paletteRef] = usePopover();

  const phase = state?.phase ?? "ready";
  const sealed = !!state?.session?.closed;
  const clock = state?.session ? fmtClock(state.session.elapsed_s) : "--:--:--";
  const phaseLabel = phase === "live" ? "Live" : phase === "complete" ? "Complete" : sealed ? "Ended" : "Ready";

  return (
    <header className="topbar">
      <NavLink to="/" className="brand" aria-label="SARTHI home">
        <Mark size={32} />
        <div className="brand-text">
          <div className="brand-word">SARTHI</div>
          <div className="brand-sub">SIH26174 · Hashira</div>
        </div>
      </NavLink>

      <nav className="nav" aria-label="Sections">
        {NAV.map(({ to, label, icon: Icon, end }, i) => (
          <NavLink key={to} to={to} end={end} title={`${label} (${i + 1})`}
                   className={({ isActive }) => (isActive ? "active" : "")}>
            <Icon size={17} />
            <span>{label}</span>
            <kbd>{i + 1}</kbd>
          </NavLink>
        ))}
      </nav>

      <span className="spacer" />

      <div className="topbar-right">
        <div className={`runpill ${phase}`} title={state?.procedure}>
          <span className="phase"><i />{phaseLabel}</span>
          <span className="proc ellipsis">{state?.procedure ?? "Connecting…"}</span>
          <span className="met" title="Mission elapsed time">T+ {clock}</span>
        </div>

        <div className="systems" aria-label="Subsystems">
          {systems(state, connected).map((x) => (
            <span key={x.key} className={`sys ${x.tone}`} title={x.title}>
              <i />{x.label}
            </span>
          ))}
        </div>

        <button className={`icon-btn${muted ? " on" : ""}`} onClick={onMute}
                title={muted ? "Unmute voice (M)" : "Mute voice (M)"} aria-pressed={muted}>
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>

        <div className="menu-wrap" ref={paletteRef}>
          <button className="icon-btn" onClick={() => setPaletteOpen((o) => !o)}
                  title="Theme" aria-expanded={paletteOpen}>
            <Palette size={18} />
          </button>
          {paletteOpen && (
            <div className="menu fade-in" role="dialog" aria-label="Theme">
              <div className="eyebrow" style={{ marginBottom: 8 }}>Theme</div>
              {THEMES.map((t) => (
                <button key={t.id} className={`theme-opt${theme === t.id ? " on" : ""}`}
                        onClick={() => { onTheme(t.id); setPaletteOpen(false); }}>
                  <span className="swatch">
                    <i style={{ background: t.swatch[0] }} />
                    <i style={{ background: t.swatch[2] }} />
                  </span>
                  <span>
                    <span style={{ display: "block", fontWeight: 650 }}>{t.label}</span>
                    <span className="faint" style={{ fontSize: 13 }}>{t.hint}</span>
                  </span>
                  {theme === t.id ? <Check size={17} className="accent-ink" /> : <span />}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Shutting down exits the process. It is the one irreversible control,
            so it sits behind a menu and asks first. */}
        <div className="menu-wrap" ref={powerRef}>
          <button className="icon-btn" onClick={() => setPowerOpen((o) => !o)}
                  title="Shut down SARTHI" aria-expanded={powerOpen}>
            <Power size={18} />
          </button>
          {powerOpen && (
            <div className="menu fade-in" role="dialog" aria-label="Shut down">
              <div className="panel-title" style={{ marginBottom: 8 }}>Shut down SARTHI?</div>
              <div className="faint" style={{ fontSize: 14, lineHeight: 1.55, marginBottom: 12 }}>
                Seals any open run, releases the camera and exits the server.
                To stop the camera but keep the console, use <b className="muted">End run</b>.
              </div>
              <div className="row">
                <button className="btn btn-sm btn-ghost" onClick={() => setPowerOpen(false)}>Cancel</button>
                <span className="spacer" />
                <button className="btn btn-sm btn-danger" onClick={() => { setPowerOpen(false); onShutdown(); }}>
                  <Power size={14} /> Shut down
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
