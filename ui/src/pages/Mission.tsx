import { api, fmtBytes } from "../lib/api";
import type { LiveState } from "../lib/api";
import { useAlertBanner } from "../lib/useLive";
import { Empty, SectionTitle, Stat, StepRow } from "../components/Bits";

export default function Mission({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const showAlert = useAlertBanner(state?.alert?.seq, state?.alert?.message);
  const s = state;

  const act = async (fn: () => Promise<unknown>) => { resetSpeech(); await fn(); };

  // Downlink argument: telemetry vs the video we did NOT have to send.
  const bytes = s?.session?.bytes ?? 0;
  const frames = s?.session?.frames ?? 0;
  const videoBytes = s?.session?.video_bytes ?? 0;
  const ratio = bytes > 0 && videoBytes > 0 ? Math.round(videoBytes / bytes) : 0;

  return (
    <div className="page">
      <div className="grid-mission">
        {/* ---------------------------------------------------------- video */}
        <div>
          <div className="card" style={{ overflow: "hidden", position: "relative" }}>
            {showAlert && s?.alert && (
              <div
                className="fade-in"
                style={{
                  position: "absolute", left: 0, right: 0, top: 0, zIndex: 5,
                  background: "linear-gradient(90deg,#b91c1c,#ef4444)",
                  color: "#fff", padding: "13px 18px", fontWeight: 700,
                  display: "flex", alignItems: "center", gap: 10,
                }}
              >
                <span style={{ fontSize: 17 }}>⚠</span>
                <span>{s.alert.message}</span>
                <span style={{ flex: 1 }} />
                <span className="mono" style={{ fontSize: 11, opacity: .85 }}>
                  {s.alert.kind.replace("_", " ")}
                </span>
              </div>
            )}
            <img src="/video" alt="live feed" style={{ width: "100%", display: "block", background: "#000" }} />
            <div
              className="mono"
              style={{
                position: "absolute", left: 12, bottom: 12, fontSize: 10.5,
                background: "rgba(0,0,0,.6)", padding: "5px 10px", borderRadius: 6,
                color: "var(--dim)",
              }}
            >
              detector: stand-in model · trained BAS model = next milestone
            </div>
          </div>

          <div className="stats-row">
            <Stat label="Telemetry" value={fmtBytes(bytes)} sub={`${s?.session?.records ?? 0} records`} tone="accent" />
            <Stat label="Downlink saved" value={ratio ? `${ratio}×` : "—"}
                  sub={videoBytes ? `vs ${fmtBytes(videoBytes)} of video` : "vs raw video"} tone="ok" />
            <Stat label="Recording" value={frames ? `${frames} f` : "—"}
                  sub={s?.session?.rtsp ? "＋ RTSP live" : "local mp4"} />
            <Stat label="Throughput" value={`${s?.fps ?? 0} fps`} sub={s?.mode ?? ""} />
          </div>
        </div>

        {/* ---------------------------------------------------------- panel */}
        <div>
          <div className="card card-pad">
            <SectionTitle>Next step</SectionTitle>
            <div style={{ fontSize: 19, lineHeight: 1.35, minHeight: 52 }}>
              {s?.complete
                ? <span style={{ color: "var(--ok)", fontWeight: 700 }}>✓ Procedure complete</span>
                : s?.next?.name ?? <span className="muted">waiting…</span>}
            </div>

            <div style={{ display: "flex", gap: 9, marginTop: 16, flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={() => act(() => api.restart("clean"))}>
                ↻ New run
              </button>
              <button className="btn btn-danger" onClick={() => act(api.skip)}>
                ⤼ Skip current step
              </button>
              <button className="btn" onClick={() => act(() => api.restart("strict"))}>
                ⚠ Out-of-order run
              </button>
            </div>
          </div>

          <div className="card card-pad" style={{ marginTop: 16 }}>
            <SectionTitle
              right={<span className="mono" style={{ fontSize: 11, color: "var(--dim)" }}>
                {s?.procedure ?? ""}
              </span>}
            >
              Procedure
            </SectionTitle>
            {s && s.steps.length > 0
              ? s.steps.map((st, i) => (
                  <StepRow key={st.id} index={i} name={st.name} state={st.state}
                           confidence={st.confidence} />
                ))
              : <Empty>No procedure loaded. Build one from the Build tab.</Empty>}
          </div>

          {s?.complete && (
            <div className="card card-pad fade-in"
                 style={{ marginTop: 16, borderColor: "var(--ok)" }}>
              <SectionTitle>Run summary</SectionTitle>
              {[
                ["Steps total", s.summary.total, undefined],
                ["Complete", s.summary.complete, "var(--ok)"],
                ["Skipped", s.summary.skipped, s.summary.skipped ? "var(--bad)" : undefined],
                ["Out of order", s.summary.out_of_order, s.summary.out_of_order ? "var(--bad)" : undefined],
                ["Alerts raised", s.summary.alerts, undefined],
                ["Duration", `${s.summary.duration}s`, undefined],
              ].map(([k, v, c]) => (
                <div key={String(k)}
                     style={{ display: "flex", justifyContent: "space-between",
                              padding: "7px 0", borderBottom: "1px dashed var(--line-soft)" }}>
                  <span className="muted" style={{ fontSize: 13 }}>{k}</span>
                  <span className="mono" style={{ fontWeight: 700, color: (c as string) ?? "var(--txt)" }}>
                    {v as number}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
