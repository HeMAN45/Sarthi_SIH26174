import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { PowerOff } from "lucide-react";
import { useLive } from "./lib/useLive";
import type { Theme } from "./lib/theme";
import { applyTheme, savedTheme } from "./lib/theme";
import { api } from "./lib/api";
import TopBar from "./components/TopBar";
import { NAV } from "./lib/nav";
import { Mark } from "./components/Mark";
import Mission from "./pages/Mission";
import Procedures from "./pages/Procedures";
import Models from "./pages/Models";
import Archive from "./pages/Archive";

export default function App() {
  const { state, connected, muted, setMuted, resetSpeech, traces } = useLive();
  const [stopped, setStopped] = useState(false);
  const [theme, setTheme] = useState<Theme>(savedTheme);
  const nav = useNavigate();
  const pickTheme = (t: Theme) => { setTheme(t); applyTheme(t); };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    window.speechSynthesis?.cancel();
    // Mute the device too, or this silences the wrong voice.
    api.mute(next).catch(() => { /* browser mute still applied */ });
  };

  const shutdown = async () => {
    setStopped(true);
    try { await api.shutdown(); } catch { /* the server is going away; expected */ }
  };

  // 1-4 switch sections, M mutes. Never while typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.ctrlKey || e.metaKey || e.altKey || t.closest("input, textarea, select, [contenteditable]")) return;
      const i = Number(e.key) - 1;
      if (i >= 0 && i < NAV.length) nav(NAV[i].to);
      else if (e.key === "m" || e.key === "M") toggleMute();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (stopped) {
    return (
      <div className="shell" style={{ display: "grid", placeItems: "center", minHeight: "100dvh" }}>
        <div className="panel panel-pad fade-in" style={{ maxWidth: 480, textAlign: "center", padding: 32 }}>
          <div style={{ display: "grid", placeItems: "center", marginBottom: 14 }}><Mark size={44} /></div>
          <div className="row" style={{ justifyContent: "center", gap: 8 }}>
            <PowerOff size={16} className="faint" />
            <span className="eyebrow">Supervisor stopped</span>
          </div>
          <h2 style={{ margin: "10px 0 8px", fontSize: 24, letterSpacing: "-.02em" }}>SARTHI has shut down</h2>
          <p className="muted" style={{ fontSize: 15, lineHeight: 1.65, margin: 0 }}>
            Any open run was sealed, its hash chain closed and the camera released. Telemetry and
            recordings remain on disk under <span className="mono">data/sessions/</span>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <TopBar state={state} connected={connected} muted={muted}
              onMute={toggleMute} onShutdown={shutdown} theme={theme} onTheme={pickTheme} />
      <Routes>
        <Route path="/" element={<Mission state={state} traces={traces} resetSpeech={resetSpeech} />} />
        <Route path="/procedures" element={<Procedures state={state} resetSpeech={resetSpeech} />} />
        <Route path="/models" element={<Models state={state} resetSpeech={resetSpeech} />} />
        <Route path="/archive" element={<Archive />} />
        {/* Old names, kept so bookmarks and muscle memory still land. */}
        <Route path="/experiment" element={<Navigate to="/procedures" replace />} />
        <Route path="/build" element={<Navigate to="/procedures" replace />} />
        <Route path="/train" element={<Navigate to="/models" replace />} />
        <Route path="/sessions" element={<Navigate to="/archive" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
