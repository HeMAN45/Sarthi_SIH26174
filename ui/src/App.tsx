import { NavLink, Route, Routes } from "react-router-dom";
import { useLive } from "./lib/useLive";
import Mission from "./pages/Mission";
import Build from "./pages/Build";
import Train from "./pages/Train";
import Sessions from "./pages/Sessions";

export default function App() {
  const { state, connected, muted, setMuted, resetSpeech } = useLive();

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>SARTHI</h1>
          <span>procedure supervision · Team Hashira</span>
        </div>

        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>Mission</NavLink>
          <NavLink to="/build" className={({ isActive }) => (isActive ? "active" : "")}>Build</NavLink>
          <NavLink to="/train" className={({ isActive }) => (isActive ? "active" : "")}>Train</NavLink>
          <NavLink to="/sessions" className={({ isActive }) => (isActive ? "active" : "")}>Sessions</NavLink>
        </nav>

        <div className="spacer" />

        <div className="topbar-meta">
          {state?.open_vocab && <span className="pill pill-accent">open-vocab</span>}
          <span className="pill">{state?.fps ?? 0} fps</span>
          <button className="btn" style={{ padding: "6px 11px", fontSize: 12 }}
                  onClick={() => { setMuted(!muted); window.speechSynthesis?.cancel(); }}
                  title="mute voice">
            {muted ? "🔇" : "🔊"}
          </button>
          <span className={`pill ${connected ? "pill-ok" : "pill-bad"}`}
                style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span className={connected ? "dot dot-live" : "dot"} />
            {connected ? "live" : "offline"}
          </span>
        </div>
      </header>

      <Routes>
        <Route path="/" element={<Mission state={state} resetSpeech={resetSpeech} />} />
        <Route path="/build" element={<Build state={state} resetSpeech={resetSpeech} />} />
        <Route path="/train" element={<Train resetSpeech={resetSpeech} />} />
        <Route path="/sessions" element={<Sessions />} />
      </Routes>
    </div>
  );
}
