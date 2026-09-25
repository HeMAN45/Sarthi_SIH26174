import type { ReactNode } from "react";
import type { Step, StepState } from "../lib/api";

/* ------------------------------------------------------------------ states */

/** A glyph and a word accompany every colour. Projectors wash out hue and a
 *  colour-blind judge reads the same screen, so colour never carries a state
 *  on its own. */
export const STATE_META: Record<
  StepState,
  { label: string; color: string; glyph: string; done: boolean }
> = {
  pending:      { label: "pending",   color: "var(--faint)",   glyph: "○", done: false },
  active:       { label: "active",    color: "var(--accent)",  glyph: "▶", done: false },
  complete:     { label: "complete",  color: "var(--accent)",  glyph: "✓", done: true },
  skipped:      { label: "skipped",   color: "var(--alert)",   glyph: "✕", done: true },
  out_of_order: { label: "out of seq", color: "var(--alert)",  glyph: "⇄", done: true },
  unverified:   { label: "unverified", color: "var(--caution)", glyph: "?", done: false },
  overridden:   { label: "overridden", color: "var(--info)",   glyph: "✓", done: true },
  stalled:      { label: "stalled",   color: "var(--caution)", glyph: "‖", done: false },
};

export function StepRow({ step, index }: { step: Step; index: number }) {
  const meta = STATE_META[step.state] ?? STATE_META.pending;
  const isActive = step.state === "active";

  return (
    <div className={`step${isActive ? " step-active" : ""}${meta.done ? " step-done" : ""}`}>
      <span
        className="step-node"
        style={{
          borderColor: meta.done || isActive ? meta.color : "var(--line)",
          background: meta.done ? meta.color : "var(--panel)",
        }}
      />
      <span className="step-ord mono">{index + 1}</span>
      <span className="step-name" style={{ color: isActive ? "var(--txt)" : undefined }}>
        {step.name}
      </span>
      {step.duration_s != null && (
        <span className="step-conf">{step.duration_s}s</span>
      )}
      {step.confidence > 0 && !meta.done && (
        <span className="step-conf">{step.confidence.toFixed(2)}</span>
      )}
      <span className="step-state" style={{ color: meta.color }}>
        {meta.glyph} {meta.label}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------- tiles */

export function Tile({
  label, value, sub, tone, idle,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: "ok" | "alert" | "caution" | "info";
  idle?: boolean;
}) {
  const color =
    tone === "ok" ? "var(--accent)"
    : tone === "alert" ? "var(--alert)"
    : tone === "caution" ? "var(--caution)"
    : tone === "info" ? "var(--info)"
    : "var(--txt)";
  return (
    <div className={`tile${idle ? " tile-idle" : ""}`}>
      <div className="label">{label}</div>
      <div className="tile-value" style={idle ? undefined : { color }}>{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------- chips */

export function Chip({
  tone = "idle", pulse, children,
}: {
  tone?: "ok" | "alert" | "caution" | "idle";
  pulse?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={`chip chip-${tone}`}>
      <span className={`led${pulse ? " led-pulse" : ""}`} />
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------- misc */

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
      <div className="label">{children}</div>
      <div style={{ flex: 1 }} />
      {right}
    </div>
  );
}

/** Errors are shown, never swallowed. A blank panel with no explanation is
 *  the worst possible failure mode: it looks like a feature that does not
 *  exist rather than a request that failed. */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="banner-warn" style={{ marginBottom: 10 }}>
      <span>⚠</span>
      <span style={{ flex: 1 }}>{message}</span>
      {onRetry && (
        <button className="btn" style={{ padding: "4px 10px" }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
