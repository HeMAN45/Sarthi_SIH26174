import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useLive } from "./lib/useLive";
import { api } from "./lib/api";
import { Chip } from "./components/Bits";
import Mission from "./pages/Mission";
import Experiment from "./pages/Experiment";
import Train from "./pages/Train";
import Sessions from "./pages/Sessions";

export default function App() {
  const { state, connected, muted, setMuted, resetSpeech } = useLive();
  const [confirming, setConfirming] = useState(false);
  const [stopped, setStopped] = useState(false);

  const stop = async () => {
    setStopped(true);
    try { await api.shutdown(); } catch { /* the server is going away; expected */ }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>SARTHI</h1>
          <span>procedure supervision · Team Hashira</span>
        </div>

        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Mission
          </NavLink>
          <NavLink to="/experiment" className={({ isActive }) => (isActive ? "active" : "")}>
            Experiment
          </NavLink>
          <NavLink to="/train" className={({ isActive }) => (isActive ? "active" : "")}>
            Train
          </NavLink>
          <NavLink to="/sessions" className={({ isActive }) => (isActive ? "active" : "")}>
            Sessions
          </NavLink>
        </nav>

        <div className="spacer" />

        <div className="topbar-meta">
          <Chip tone={connected ? "ok" : "alert"} pulse={connected}>
            {connected ? "LIVE" : "OFFLINE"}
          </Chip>
          <span className="chip chip-idle mono">{(state?.fps ?? 0).toFixed(0)} FPS</span>
          <button
            className="btn"
            style={{ padding: "7px 10px" }}
            onClick={() => {
              const next = !muted;
              setMuted(next);
              window.speechSynthesis?.cancel();
              // Mute the device too, or this silences the wrong voice.
              api.mute(next).catch(() => { /* browser mute still applied */ });
            }}
            title={`${muted ? "unmute" : "mute"} voice — ${
              state?.voice?.available ? `on-device (${state.voice.model})` : "browser fallback"
            }`}
          >
            {muted ? "🔇" : "🔊"}
          </button>

          {/* Stopping releases the camera and exits the process, so it asks
              first. Every other control is reversible; this one is not. */}
          {confirming ? (
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "var(--caution)" }}>Stop supervision?</span>
              <button className="btn btn-stop" style={{ padding: "7px 12px" }} onClick={stop}>
                Confirm
              </button>
              <button className="btn" style={{ padding: "7px 10px" }}
                      onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button className="btn btn-stop" onClick={() => setConfirming(true)}
                    disabled={stopped} title="finalize the run and exit">
              ■ STOP
            </button>
          )}
        </div>
      </header>

      {stopped ? (
        <div style={{ display: "grid", placeItems: "center", padding: 40 }}>
          <div className="panel panel-pad" style={{ textAlign: "center", maxWidth: 460 }}>
            <div className="label">Supervision stopped</div>
            <p style={{ color: "var(--dim)", fontSize: 13, lineHeight: 1.6 }}>
              The run was sealed, its hash chain closed and the camera released.
              Telemetry and recordings remain on disk under{" "}
              <span className="mono">data/sessions/</span>.
            </p>
          </div>
        </div>
      ) : (
        <Routes>
          <Route path="/" element={<Mission state={state} resetSpeech={resetSpeech} />} />
          <Route
            path="/experiment"
            element={<Experiment state={state} resetSpeech={resetSpeech} />}
          />
          {/* The tab was called "Build" and nobody found it, including us. */}
          <Route
            path="/build"
            element={<Experiment state={state} resetSpeech={resetSpeech} />}
          />
          <Route path="/train" element={<Train resetSpeech={resetSpeech} />} />
          <Route path="/sessions" element={<Sessions />} />
        </Routes>
      )}
    </div>
  );
}
