import { useEffect, useState } from "react";
import { api, fmtBytes } from "../lib/api";
import type { SessionDetail, SessionRow } from "../lib/api";
import { Empty, SectionTitle } from "../components/Bits";

/** Audit view: every supervised run, its outcome, and the tamper check. */
export default function Sessions() {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [sel, setSel] = useState<SessionDetail | null>(null);
  const [verdict, setVerdict] = useState<string>("");

  const load = () => api.sessions().then(setRows).catch(() => {});
  useEffect(() => { load(); }, []);

  const open = async (id: string) => {
    setVerdict("");
    setSel(await api.session(id));
  };

  const runVerify = async (id: string) => {
    setVerdict("checking…");
    const r = await api.verify(id);
    setVerdict(
      r.ok
        ? `✓ Chain intact — ${r.record_count} records verified`
        : `✗ TAMPERED — first bad record at seq ${r.first_bad_seq}`
    );
  };

  return (
    <div className="page">
      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, .8fr) 1.2fr", gap: 18 }}>
        <div className="panel panel-pad">
          <SectionTitle right={<button className="btn" style={{ padding: "5px 10px" }} onClick={load}>↻</button>}>
            Sessions
          </SectionTitle>
          {rows.length === 0 && <Empty>No runs recorded yet.</Empty>}
          <div style={{ maxHeight: "70vh", overflow: "auto" }}>
            {rows.map((r) => (
              <button key={r.id} onClick={() => open(r.id)}
                style={{
                  width: "100%", textAlign: "left", cursor: "pointer",
                  background: sel?.session.id === r.id ? "rgba(34,211,238,.08)" : "#0d141d",
                  border: `1px solid ${sel?.session.id === r.id ? "#0e7490" : "var(--line)"}`,
                  borderRadius: "var(--r-sm)", padding: "10px 12px", marginBottom: 7, color: "var(--txt)",
                }}>
                <div className="mono" style={{ fontSize: 12 }}>{r.id}</div>
                <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                  <span className={`chip ${r.status === "complete" ? "chip-ok" : "chip-idle"}`}>{r.status}</span>
                  {!!r.steps_skipped && <span className="chip chip-alert">{r.steps_skipped} skipped</span>}
                  {!!r.alert_count && <span className="chip chip-idle">{r.alert_count} alerts</span>}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="panel panel-pad">
          {!sel && <Empty>Select a session to inspect its telemetry.</Empty>}
          {sel && (
            <div className="fade-in">
              <SectionTitle>Session · {sel.session.id}</SectionTitle>

              <div className="tiles" style={{ marginTop: 0, marginBottom: 16 }}>
                <div className="panel panel-pad" style={{ padding: 12 }}>
                  <div className="label" style={{ color: "var(--dim)" }}>Telemetry</div>
                  <div className="mono" style={{ fontSize: 18, marginTop: 4 }}>
                    {sel.chain ? fmtBytes(sel.chain.bytes_written) : "—"}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--faint)" }}>
                    {sel.chain?.record_count ?? 0} records
                  </div>
                </div>
                <div className="panel panel-pad" style={{ padding: 12 }}>
                  <div className="label" style={{ color: "var(--dim)" }}>Outcome</div>
                  <div className="mono" style={{ fontSize: 18, marginTop: 4 }}>
                    {sel.session.steps_complete ?? 0}/{sel.session.steps_total ?? 0}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--faint)" }}>steps complete</div>
                </div>
              </div>

              <div style={{ display: "flex", gap: 9, flexWrap: "wrap", marginBottom: 14 }}>
                <button className="btn btn-primary" onClick={() => runVerify(sel.session.id)}>
                  🔐 Verify hash chain
                </button>
                <a className="btn" href={`/api/sessions/${sel.session.id}/telemetry`}>
                  ⭳ telemetry.jsonl
                </a>
                <a className="btn" href={`/api/sessions/${sel.session.id}/video`}>⭳ run.mp4</a>
              </div>

              {verdict && (
                <div className="fade-in mono"
                  style={{
                    padding: "11px 13px", borderRadius: "var(--r-sm)", marginBottom: 16, fontSize: 13,
                    background: verdict.startsWith("✓") ? "rgba(34,197,94,.1)"
                              : verdict.startsWith("✗") ? "rgba(239,68,68,.12)" : "#0d141d",
                    border: `1px solid ${verdict.startsWith("✓") ? "var(--accent)"
                              : verdict.startsWith("✗") ? "var(--alert)" : "var(--line)"}`,
                    color: verdict.startsWith("✓") ? "var(--accent)"
                          : verdict.startsWith("✗") ? "var(--alert)" : "var(--dim)",
                  }}>
                  {verdict}
                </div>
              )}

              <SectionTitle>Steps</SectionTitle>
              {sel.steps.map((s) => (
                <div key={s.step_id} style={{
                  display: "flex", gap: 10, padding: "7px 0",
                  borderBottom: "1px dashed var(--line-soft)", fontSize: 13,
                }}>
                  <span className="mono" style={{ color: "var(--faint)", width: 28 }}>{s.ordinal + 1}</span>
                  <span style={{ flex: 1 }}>{s.step_id}</span>
                  <span className="mono" style={{
                    color: s.state === "complete" ? "var(--accent)"
                          : s.state === "skipped" || s.state === "out_of_order" ? "var(--alert)"
                          : "var(--dim)",
                  }}>{s.state}</span>
                </div>
              ))}

              {sel.alerts.length > 0 && (
                <>
                  <SectionTitle>Alerts</SectionTitle>
                  {sel.alerts.map((a, i) => (
                    <div key={i} style={{ fontSize: 12.5, padding: "6px 0", color: "var(--alert)" }}>
                      ⚠ {a.message}
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
