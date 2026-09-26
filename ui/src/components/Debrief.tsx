import { useNavigate } from "react-router-dom";
import { ArrowUpRight, EyeOff, Link2, Play, RadioTower, Square } from "lucide-react";
import type { LiveState } from "../lib/api";
import { fmtBytes, fmtDuration, fmtRatio, fmtStep, shortHash } from "../lib/format";
import { meta } from "../lib/states";
import { StepNode } from "./steps";
import { Badge } from "./ui";
import { Ring } from "./viz";

type Verdict = { tone: "ok" | "caution" | "neutral"; title: string; sub: string };

function verdictOf(s: LiveState): Verdict {
  const total = s.steps.length;
  const count = (st: string) => s.steps.filter((x) => x.state === st).length;
  const skipped = count("skipped");
  const ooo = count("out_of_order");
  if (s.complete) {
    if (!skipped && !ooo) {
      return { tone: "ok", title: "Procedure verified",
               sub: `All ${total} steps were verified, in order, from the camera alone.` };
    }
    const parts = [skipped && `${skipped} skipped`, ooo && `${ooo} out of sequence`].filter(Boolean);
    return { tone: "caution", title: "Completed with deviations",
             sub: `${parts.join(" · ")}. Each one raised an alert and is on the record.` };
  }
  const at = s.steps.findIndex((x) => !meta(x.state).done);
  const where = at >= 0 ? `Stopped at step ${at + 1} of ${total} — ${s.steps[at].name}.` : "";
  return { tone: "neutral", title: "Run ended early",
           sub: `${where} The telemetry up to that point is sealed and verifiable.` };
}

const TONE_COLOR = { ok: "var(--ok)", caution: "var(--caution)", neutral: "var(--accent)" } as const;

/** The completion screen: what happened, how long each step took, and the
 *  evidence of record — a sealed hash chain and the downlink it saved. */
export default function Debrief({
  s, onNewRun, onEnd, onHide,
}: { s: LiveState; onNewRun: () => void; onEnd?: () => void; onHide: () => void }) {
  const nav = useNavigate();
  const v = verdictOf(s);
  const color = TONE_COLOR[v.tone];
  const total = s.steps.length;
  const verified = s.steps.filter((x) => x.state === "complete" || x.state === "overridden").length;
  const count = (st: string) => s.steps.filter((x) => x.state === st).length;
  const sess = s.session;
  const longest = Math.max(1, ...s.steps.map((x) => x.duration_s ?? 0));
  const [head, tail] = shortHash(sess?.head);
  const ratio = sess && sess.bytes > 0 && sess.video_bytes > 0 ? Math.round(sess.video_bytes / sess.bytes) : 0;

  return (
    <div className="overlay dim">
      <div className={`debrief ${v.tone === "neutral" ? "" : v.tone} fade-in`} role="dialog" aria-label="Run debrief">
        <div className="debrief-head">
          <span className="eyebrow">Mission debrief</span>
          <Badge mono>{s.procedure_id}</Badge>
          <span className="spacer" />
          {sess && <span className="mono faint" style={{ fontSize: 13 }}>{sess.id}</span>}
        </div>

        <div className="debrief-top">
          <Ring value={total ? verified / total : 0} size={118} stroke={8} color={color}>
            <div>
              <div className="mono" style={{ fontSize: 28, fontWeight: 560, letterSpacing: "-.03em" }}>
                {verified}<span className="faint" style={{ fontSize: 16 }}>/{total}</span>
              </div>
              <div className="eyebrow" style={{ fontSize: 11, marginTop: 3 }}>verified</div>
            </div>
          </Ring>
          <div>
            <Badge tone={v.tone === "neutral" ? undefined : v.tone}>
              {s.complete ? "Chain sealed · complete" : "Chain sealed · ended by crew"}
            </Badge>
            <h2 className="verdict" style={{ color: v.tone === "neutral" ? undefined : color }}>{v.title}</h2>
            <div className="verdict-sub">{v.sub}</div>
          </div>
        </div>

        <div className="statgrid">
          <div><div className="stat-label">Duration</div>
               <div className="stat-value">{fmtDuration(sess?.elapsed_s)}</div></div>
          <div><div className="stat-label">Verified</div>
               <div className="stat-value" style={{ color: "var(--ok)" }}>{verified}/{total}</div></div>
          <div><div className="stat-label">Skipped</div>
               <div className="stat-value" style={{ color: count("skipped") ? "var(--alert)" : undefined }}>
                 {count("skipped")}</div></div>
          <div><div className="stat-label">Out of sequence</div>
               <div className="stat-value" style={{ color: count("out_of_order") ? "var(--alert)" : undefined }}>
                 {count("out_of_order")}</div></div>
          <div><div className="stat-label">Alerts</div>
               <div className="stat-value" style={{ color: s.summary.alerts ? "var(--caution)" : undefined }}>
                 {s.summary.alerts}</div></div>
        </div>

        <div className="gantt">
          {s.steps.map((st, i) => {
            const m = meta(st.state);
            const d = st.duration_s;
            const label = st.state === "active" ? "Interrupted" : m.label;
            return (
              <div key={st.id} className="gantt-row">
                <StepNode state={st.state === "active" ? "pending" : st.state} index={i} size={22} />
                <span className="ellipsis" style={{ color: m.done ? "var(--ink)" : "var(--ink-3)" }}>{st.name}</span>
                <div className="gantt-track">
                  {d != null
                    ? <i style={{ width: `${(d / longest) * 100}%`, background: m.color }} />
                    : <i className="none" />}
                </div>
                <span className="mono" style={{ fontSize: 13, textAlign: "right", whiteSpace: "nowrap",
                                                color: d != null ? "var(--ink-2)" : st.state === "active" ? "var(--caution)" : m.color }}>
                  {d != null ? fmtStep(d) : label}
                </span>
              </div>
            );
          })}
        </div>

        <div className="seal">
          <div className="seal-box">
            <div className="row" style={{ gap: 7, marginBottom: 6 }}>
              <Link2 size={14} className="ok-ink" />
              <span className="stat-label">SHA-256 chain head</span>
            </div>
            <div className="hash">{head}<span> ··· </span>{tail}</div>
            <div className="faint" style={{ fontSize: 13, marginTop: 4 }}>
              {sess?.records ?? 0} records · {fmtBytes(sess?.bytes ?? 0)} · verify any time in Archive
            </div>
          </div>
          <div className="seal-box">
            <div className="row" style={{ gap: 7, marginBottom: 6 }}>
              <RadioTower size={14} className="accent-ink" />
              <span className="stat-label">Downlink saved</span>
            </div>
            <div className="mono accent-ink" style={{ fontSize: 22, fontWeight: 650 }}>
              {ratio ? `${fmtRatio(ratio)}× smaller` : "Telemetry only"}
            </div>
            <div className="faint" style={{ fontSize: 13, marginTop: 4 }}>
              {ratio
                ? `${fmtBytes(sess!.bytes)} of telemetry stands in for ${fmtBytes(sess!.video_bytes)} of video`
                : "No recording to compare against in this run"}
            </div>
          </div>
        </div>

        <div className="debrief-actions">
          <button className="btn btn-primary" onClick={onNewRun}>
            <Play size={14} fill="currentColor" /> New run
          </button>
          {onEnd && (
            <button className="btn" onClick={onEnd}><Square size={13} fill="currentColor" /> End run</button>
          )}
          {sess && (
            <button className="btn" onClick={() => nav(`/archive?id=${encodeURIComponent(sess.id)}`)}>
              <ArrowUpRight size={15} /> Open in Archive
            </button>
          )}
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onHide}><EyeOff size={15} /> Hide</button>
        </div>
      </div>
    </div>
  );
}
