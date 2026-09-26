import type { Step } from "../lib/api";
import { fmtStep } from "../lib/format";
import { meta } from "../lib/states";

export function StepNode({ state, index, size = 28 }: { state: string; index: number; size?: number }) {
  const m = meta(state);
  const Icon = m.icon;
  const custom = state !== "pending" && state !== "active";
  return (
    <span
      className="tl-node"
      style={{
        width: size, height: size,
        ...(custom ? { color: m.color, background: m.soft, borderColor: m.line } : null),
      }}
    >
      {Icon ? <Icon size={Math.round(size * 0.5)} strokeWidth={2.6} /> : String(index + 1).padStart(2, "0")}
    </span>
  );
}

export function Timeline({ steps, live = true }: { steps: Step[]; live?: boolean }) {
  return (
    <div className="timeline">
      {steps.map((st, i) => {
        // Between runs nothing is being judged, so the step that was active
        // when the run ended is shown as interrupted, not as in progress.
        const interrupted = !live && st.state === "active";
        const state = interrupted ? "pending" : st.state;
        const m = meta(state);
        const prevDone = i > 0 && meta(steps[i - 1].state).done;
        const cls = `tl-row ${state}${prevDone ? " after-done" : ""}`;
        return (
          <div key={st.id} className={cls} title={st.reason || st.name}>
            <StepNode state={state} index={i} />
            <div className="tl-name">{st.name}</div>
            <div className="tl-meta" style={interrupted ? { color: "var(--caution)" }
              : state !== "pending" && state !== "active" ? { color: m.color } : undefined}>
              {interrupted ? "Interrupted"
                : state === "active" ? `${Math.round(st.confidence * 100)}%`
                : st.duration_s != null && state === "complete" ? fmtStep(st.duration_s)
                : state === "pending" ? "" : m.label}
            </div>
          </div>
        );
      })}
    </div>
  );
}
