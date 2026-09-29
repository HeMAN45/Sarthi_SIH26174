import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  Archive as ArchiveIcon, Bell, Check, CircleDashed, Clock3, Download, FileJson, FolderOpen,
  Link2, RefreshCw, ShieldCheck, ShieldX, Square, TriangleAlert, Video, Zap,
} from "lucide-react";
import { api } from "../lib/api";
import type { SessionDetail, SessionRow } from "../lib/api";
import { fmtBytes, fmtDuration, fmtWhen, plural, shortHash } from "../lib/format";
import { meta } from "../lib/states";
import { StepNode } from "../components/steps";
import { Badge, Empty, ErrorNote, PanelHead, Seg } from "../components/ui";
import type { Tone } from "../components/ui";
import { Bar } from "../components/viz";

const STATUS: Record<string, { label: string; tone?: Tone; icon: LucideIcon; color: string }> = {
  complete: { label: "Complete", tone: "ok", icon: Check, color: "var(--ok)" },
  aborted: { label: "Ended early", tone: undefined, icon: Square, color: "var(--ink-2)" },
  crashed: { label: "Recovered", tone: "caution", icon: Zap, color: "var(--caution)" },
  running: { label: "Running", tone: "accent", icon: CircleDashed, color: "var(--accent-2)" },
};
const statusOf = (s: string) => STATUS[s] ?? { label: s, icon: CircleDashed, color: "var(--ink-3)" };

type Filter = "all" | "complete" | "aborted" | "crashed";
type Check = { state: "idle" | "checking" | "ok" | "bad"; records?: number; seq?: number | null; error?: string | null };

/** Every supervised run, its outcome, and the tamper check. */
export default function Archive() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<SessionDetail | null>(null);
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const wanted = params.get("id");

  const load = useCallback(() => {
    api.sessions()
      .then((r) => { setRows(r); setErr(""); })
      .catch((e) => setErr(`Could not read the archive: ${e.message}`));
  }, []);
  useEffect(load, [load]);

  const open = useCallback((id: string) => {
    api.session(id)
      .then((d) => { setSel(d); setCheck({ state: "idle" }); })
      .catch((e) => setErr(`Could not open ${id}: ${e.message}`));
  }, []);

  // Deep link from the debrief (?id=…), else the most recent run.
  useEffect(() => {
    if (!rows?.length) return;
    open(wanted && rows.some((r) => r.id === wanted) ? wanted : rows[0].id);
  }, [rows, wanted, open]);

  const verify = async (id: string) => {
    setCheck({ state: "checking" });
    try {
      const [r] = await Promise.all([api.verify(id), new Promise((res) => setTimeout(res, 450))]);
      setCheck({ state: r.ok ? "ok" : "bad", records: r.record_count, seq: r.first_bad_seq, error: r.error });
    } catch (e) {
      setCheck({ state: "bad", error: (e as Error).message });
    }
  };

  const shown = (rows ?? []).filter((r) => filter === "all" || r.status === filter);
  const count = (s: string) => (rows ?? []).filter((r) => r.status === s).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Archive</h1>
          <p className="page-desc">
            Every supervised run is an append-only log, each record chained to the last by SHA-256.
            Change one byte after the fact and verification names the exact record that was altered.
          </p>
        </div>
        <span className="spacer" />
        <div className="row" style={{ gap: 10 }}>
          <Stat label="Runs" value={rows?.length ?? 0} />
          <Stat label="Complete" value={count("complete")} color="var(--ok)" />
          <Stat label="Ended early" value={count("aborted")} />
          <Stat label="Recovered" value={count("crashed")} color={count("crashed") ? "var(--caution)" : undefined} />
        </div>
      </div>

      {err && <ErrorNote message={err} onRetry={load} />}

      <div style={{ display: "grid", gridTemplateColumns: "400px minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
        {/* ------------------------------------------------------------ list */}
        <div className="panel panel-pad" style={{ position: "sticky", top: 76 }}>
          <PanelHead icon={ArchiveIcon} title="Runs"
                     right={<button className="btn btn-xs btn-ghost btn-icon" onClick={load} aria-label="Refresh"><RefreshCw size={13} /></button>} />
          <Seg value={filter} onChange={setFilter} block options={[
            { value: "all", label: "All" }, { value: "complete", label: "Complete" },
            { value: "aborted", label: "Ended" }, { value: "crashed", label: "Recovered" },
          ]} />
          <div className="list" style={{ gap: 6, marginTop: 12, maxHeight: "calc(100dvh - 300px)", overflowY: "auto", paddingRight: 2 }}>
            {rows && shown.length === 0 && <Empty icon={ArchiveIcon}>{rows.length ? "No runs match." : "No runs recorded yet."}</Empty>}
            {shown.map((r) => {
              const st = statusOf(r.status);
              const Icon = st.icon;
              const total = r.steps_total ?? 0;
              const done = r.steps_complete ?? 0;
              return (
                <button key={r.id} className={`item${sel?.session.id === r.id ? " sel" : ""}`}
                        style={{ gridTemplateColumns: "30px minmax(0, 1fr) auto" }}
                        onClick={() => setParams({ id: r.id }, { replace: true })}>
                  <span className="tl-node" style={{ color: st.color, borderColor: "var(--line-2)", width: 30, height: 30 }}>
                    <Icon size={14} strokeWidth={2.4} />
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span style={{ fontSize: 14.5, fontWeight: 580 }}>{fmtWhen(r.started_at)}</span>
                      <span className="mono faint ellipsis" style={{ fontSize: 12.5 }}>{r.procedure_id}</span>
                    </div>
                    <div className="row" style={{ gap: 8, marginTop: 6 }}>
                      <div style={{ flex: 1 }}><Bar value={total ? done / total : 0} color={st.color} /></div>
                      <span className="mono faint" style={{ fontSize: 12.5 }}>{done}/{total}</span>
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div className="mono" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                      {r.duration_ms != null ? fmtDuration(r.duration_ms / 1000) : "-"}
                    </div>
                    {!!r.alert_count && (
                      <div className="mono" style={{ fontSize: 12.5, color: "var(--caution)", marginTop: 4 }}>
                        {plural(r.alert_count, "alert")}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ---------------------------------------------------------- detail */}
        <div className="col" style={{ gap: 16 }}>
          {!sel ? (
            <div className="panel"><Empty icon={FolderOpen} title="Select a run">Its telemetry, steps and integrity check appear here.</Empty></div>
          ) : (
            <Detail d={sel} check={check} onVerify={() => verify(sel.session.id)} />
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="card" style={{ padding: "9px 14px", minWidth: 92 }}>
      <div className="eyebrow" style={{ fontSize: 11 }}>{label}</div>
      <div className="mono" style={{ fontSize: 22, fontWeight: 560, color, marginTop: 1 }}>{value}</div>
    </div>
  );
}

function Detail({ d, check, onVerify }: { d: SessionDetail; check: Check; onVerify: () => void }) {
  const s = d.session;
  const st = statusOf(s.status);
  const total = s.steps_total ?? 0;
  const [head, tail] = shortHash(d.chain?.last_hash);

  return (
    <>
      <div className="panel panel-pad fade-in" key={s.id}>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 8 }}>
              <Badge tone={st.tone} icon={st.icon}>{st.label}</Badge>
              <span className="mono faint" style={{ fontSize: 13 }}>{s.id}</span>
            </div>
            <h2 style={{ margin: "8px 0 0", fontSize: 24, fontWeight: 650, letterSpacing: "-.02em" }}>
              {s.procedure_id}
            </h2>
            <div className="faint" style={{ fontSize: 14, marginTop: 2 }}>
              Started {fmtWhen(s.started_at)}{s.ended_at ? ` · ended ${fmtWhen(s.ended_at)}` : ""}
            </div>
            {s.notes && (
              <div style={{ fontSize: 14, marginTop: 4, color: "var(--caution)" }}>{s.notes}</div>
            )}
          </div>
          <span className="spacer" />
          <div className="row" style={{ gap: 8 }}>
            <a className="btn btn-sm" href={`/api/sessions/${s.id}/telemetry`}><FileJson size={14} /> telemetry.jsonl</a>
            <a className="btn btn-sm" href={`/api/sessions/${s.id}/video`}><Video size={14} /> run.mp4</a>
          </div>
        </div>

        <div className="statgrid" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          <div><div className="stat-label"><Clock3 size={11} style={{ verticalAlign: "-1px" }} /> Duration</div>
               <div className="stat-value">{s.duration_ms != null ? fmtDuration(s.duration_ms / 1000) : "-"}</div></div>
          <div><div className="stat-label">Verified</div>
               <div className="stat-value" style={{ color: "var(--ok)" }}>{s.steps_complete ?? 0}/{total}</div></div>
          <div><div className="stat-label">Skipped · out of order</div>
               <div className="stat-value" style={{ color: (s.steps_skipped || s.steps_out_of_order) ? "var(--alert)" : undefined }}>
                 {s.steps_skipped ?? 0} · {s.steps_out_of_order ?? 0}</div></div>
          <div><div className="stat-label">Telemetry</div>
               <div className="stat-value">{d.chain ? fmtBytes(d.chain.bytes_written) : "-"}</div></div>
        </div>
      </div>

      {/* ------------------------------------------------------- integrity */}
      <div className={`panel panel-pad${check.state === "ok" ? " ok-glow" : check.state === "bad" ? " alert-glow" : ""}`}>
        <div className="row" style={{ gap: 16, alignItems: "center" }}>
          <div className="empty-icon" style={{
            width: 52, height: 52, flex: "none",
            color: check.state === "ok" ? "var(--ok)" : check.state === "bad" ? "var(--alert)" : "var(--accent-2)",
            background: check.state === "ok" ? "var(--ok-soft)" : check.state === "bad" ? "var(--alert-soft)" : "var(--accent-soft)",
            borderColor: check.state === "ok" ? "var(--ok-line)" : check.state === "bad" ? "var(--alert-line)" : "var(--accent-line)",
          }}>
            {check.state === "bad" ? <ShieldX size={24} /> : <ShieldCheck size={24} />}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 620, fontSize: 16 }}>
              {check.state === "ok" ? "Chain intact - nothing was altered"
                : check.state === "bad" ? (check.seq != null ? `Tampered - record #${check.seq} was altered` : "Verification failed")
                : check.state === "checking" ? "Re-walking the hash chain…"
                : "Tamper-evident telemetry"}
            </div>
            <div className="faint" style={{ fontSize: 14, marginTop: 3 }}>
              {check.state === "ok" ? `${check.records} records re-hashed from genesis; every link matches.`
                : check.state === "bad" ? (check.error ?? "Every record after the altered one is now unverifiable.")
                : d.chain ? `${d.chain.record_count} records · head `
                : s.status === "running" ? "Still being written - the chain is sealed when the run ends."
                : "No chain was sealed for this run."}
              {check.state !== "ok" && check.state !== "bad" && d.chain && (
                <span className="mono muted">{head}…{tail}</span>
              )}
            </div>
          </div>
          <button className={`btn ${check.state === "ok" ? "" : "btn-primary"}`} onClick={onVerify}
                  disabled={check.state === "checking" || !d.chain}>
            {check.state === "checking" ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <Link2 size={15} />}
            {check.state === "ok" || check.state === "bad" ? "Verify again" : "Verify chain"}
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.3fr) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
        <div className="panel panel-pad">
          <PanelHead title="Steps" right={<span className="mono faint" style={{ fontSize: 13 }}>{d.steps.length}</span>} />
          {d.steps.length === 0 && <Empty>No step verdicts were recorded.</Empty>}
          <div className="list" style={{ gap: 4 }}>
            {d.steps.map((x) => {
              const m = meta(x.state);
              return (
                <div key={x.step_id} className="row" style={{ padding: "6px 2px", gap: 12 }}>
                  <StepNode state={x.state} index={x.ordinal} size={24} />
                  <span className="mono" style={{ fontSize: 14, flex: 1 }}>{x.step_id}</span>
                  {x.confidence != null && <span className="mono faint" style={{ fontSize: 13 }}>{x.confidence.toFixed(2)}</span>}
                  <span style={{ fontSize: 13.5, color: m.color, minWidth: 92, textAlign: "right" }}>{m.label}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="panel panel-pad">
          <PanelHead icon={Bell} title="Alerts" right={<span className="mono faint" style={{ fontSize: 13 }}>{d.alerts.length}</span>} />
          {d.alerts.length === 0 ? (
            <Empty icon={Check}>No alerts. The crew did it by the book.</Empty>
          ) : (
            <div className="list" style={{ gap: 8 }}>
              {d.alerts.map((a, i) => (
                <div key={i} className="note" style={a.severity === "high" ? { borderColor: "var(--alert-line)" } : undefined}>
                  <TriangleAlert size={15} style={{ color: a.severity === "high" ? "var(--alert)" : "var(--caution)" }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ color: "var(--ink)" }}>{a.message}</div>
                    <div className="mono faint" style={{ fontSize: 12, marginTop: 2, letterSpacing: ".04em" }}>
                      {a.kind.replace(/_/g, " ").toUpperCase()} · {fmtWhen(a.raised_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="faint" style={{ fontSize: 13, marginTop: 12 }}>
            <Download size={12} style={{ verticalAlign: "-2px" }} /> Full evidence, every verdict and its reason,
            is in <span className="mono">telemetry.jsonl</span>.
          </div>
        </div>
      </div>
    </>
  );
}
