import { api, fmtBytes } from "../lib/api";
import type { LiveState } from "../lib/api";
import { useAlertBanner } from "../lib/useLive";
import { Chip, Empty, SectionTitle, StepRow, Tile } from "../components/Bits";

export default function Mission({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const showAlert = useAlertBanner(state?.alert?.seq, state?.alert?.message);
  const s = state;

  const act = async (fn: () => Promise<unknown>) => { resetSpeech(); await fn(); };

  // The downlink argument, measured rather than estimated: bytes of telemetry
  // against the bytes of video we did not have to send. This is the headline
  // number of the whole project, so it goes where people actually look.
  const bytes = s?.session?.bytes ?? 0;
  const records = s?.session?.records ?? 0;
  const frames = s?.session?.frames ?? 0;
  const videoBytes = s?.session?.video_bytes ?? 0;
  const ratio = bytes > 0 && videoBytes > 0 ? Math.round(videoBytes / bytes) : 0;

  const running = !!s?.session;
  const p = s?.perception;
  const done = s?.steps.filter((x) => x.state === "complete").length ?? 0;
  const total = s?.steps.length ?? 0;

  // Degradation is announced, never silent (invariant #10).
  const degraded: string[] = [];
  if (p?.rack_required && !p.rack_locked) degraded.push("rack not locked — positions unavailable");
  if (p?.pose_required && !p.pose_ok) degraded.push("pose unavailable — contact steps cannot verify");
  if (s && !s.voice.available) degraded.push("no on-device voice — alerts speak in this browser only");

  return (
    <div className="hud">
      {/* ======================================================= left: video */}
      <div className="hud-left">
        <div className="viewport">
          {showAlert && s?.alert && (
            <div className="alert-toast fade-in">
              <span style={{ fontSize: 17 }}>⚠</span>
              <span style={{ flex: 1 }}>{s.alert.message}</span>
              <span className="mono" style={{ fontSize: 11, opacity: 0.8 }}>
                {s.alert.kind.replace(/_/g, " ")}
              </span>
            </div>
          )}
          <img src="/video" alt="live camera feed" />
          <div className="viewport-tag">
            detector: {p?.detector ?? "stand-in"}
            {p?.rack_required && ` · rack ${p.rack_locked ? "locked" : "lost"}`}
            {p?.pose_required && ` · pose ${p.pose_ok ? "on" : "off"}`}
            {s?.voice && ` · voice ${s.voice.available ? s.voice.model : "browser"}`}
          </div>
        </div>

        {/* Idle is a state with a name. An em-dash reads as broken. */}
        <div className="tiles">
          <Tile
            label="Downlink saved"
            value={ratio ? `${ratio}×` : running ? "measuring" : "no run"}
            sub={videoBytes ? `vs ${fmtBytes(videoBytes)} of video` : "telemetry replaces video"}
            tone="ok"
            idle={!ratio}
          />
          <Tile
            label="Telemetry"
            value={bytes ? fmtBytes(bytes) : "0 B"}
            sub={running ? `${records} records${s?.session?.closed ? " · sealed" : ""}` : "no run"}
            tone="info"
            idle={!bytes}
          />
          <Tile
            label="Recording"
            value={frames ? `${frames} f` : "not recording"}
            sub={s?.session?.rtsp ? "local mp4 + RTSP" : running ? "local mp4" : "starts with the run"}
            idle={!frames}
          />
          <Tile
            label="Progress"
            value={total ? `${done} / ${total}` : "no procedure"}
            sub={s?.mode ? `${s.mode} mode` : ""}
            tone={s?.complete ? "ok" : undefined}
            idle={!total}
          />
        </div>
      </div>

      {/* ====================================================== right: state */}
      <div className="hud-right">
        {/* The single most important string on the screen. It is never below
            the fold, and it is never smaller than anything around it. */}
        <div className="panel panel-pad nextstep">
          <div className="label">{s?.complete ? "Run complete" : "Next step"}</div>
          <div
            className="nextstep-text"
            style={{ color: s?.complete ? "var(--accent)" : undefined }}
          >
            {s?.complete
              ? "✓ Procedure complete"
              : s?.next?.name ?? <span style={{ color: "var(--faint)" }}>waiting for a procedure…</span>}
          </div>
          {!s?.complete && s?.next?.voice && (
            <div className="nextstep-voice">“{s.next.voice}”</div>
          )}
          {degraded.length > 0 && (
            <div className="banner-warn" style={{ marginTop: 11 }}>
              <span>⚠</span>
              <span>{degraded.join(" · ")}</span>
            </div>
          )}
        </div>

        <div className="panel panel-pad" style={{ display: "grid", gridTemplateRows: "auto 1fr", minHeight: 0 }}>
          <SectionTitle
            right={
              <span className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>
                {s?.procedure ?? ""}
              </span>
            }
          >
            Procedure
          </SectionTitle>
          <div className="steps">
            {s && s.steps.length > 0 ? (
              s.steps.map((st, i) => <StepRow key={st.id} step={st} index={i} />)
            ) : (
              <Empty>
                No procedure loaded.
                <br />
                Open <strong>Experiment</strong> to build one.
              </Empty>
            )}
          </div>
        </div>

        {s?.complete ? (
          <div className="panel panel-pad fade-in">
            <SectionTitle right={<Chip tone="ok">SEALED</Chip>}>Run summary</SectionTitle>
            <div className="kv"><span>Complete</span><span style={{ color: "var(--accent)" }}>{s.summary.complete}</span></div>
            <div className="kv"><span>Skipped</span><span style={{ color: s.summary.skipped ? "var(--alert)" : undefined }}>{s.summary.skipped}</span></div>
            <div className="kv"><span>Out of sequence</span><span style={{ color: s.summary.out_of_order ? "var(--alert)" : undefined }}>{s.summary.out_of_order}</span></div>
            <div className="kv"><span>Alerts raised</span><span>{s.summary.alerts}</span></div>
            <div className="kv"><span>Duration</span><span>{s.summary.duration}s</span></div>
            <button className="btn btn-primary" style={{ width: "100%", marginTop: 11 }}
                    onClick={() => act(() => api.restart("clean"))}>
              ↻ Start a new run
            </button>
          </div>
        ) : (
          <div className="controls">
            <button className="btn btn-primary" onClick={() => act(() => api.restart("clean"))}>
              ↻ New run
            </button>
            <button className="btn btn-caution" onClick={() => act(api.skip)}>
              ⤼ Skip step
            </button>
            <button className="btn" onClick={() => act(() => api.restart("strict"))}>
              ⚠ Strict mode
            </button>
            <button className="btn btn-alert" onClick={() => act(api.endRun)}
                    title="seal this run's telemetry and start fresh">
              ■ End run
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
