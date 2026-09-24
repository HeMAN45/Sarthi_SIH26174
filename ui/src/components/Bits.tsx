import type { ReactNode } from "react";
import type { StepState } from "../lib/api";

/* ------------------------------------------------------------------ step UI */

export const STATE_META: Record<StepState, { label: string; color: string }> = {
  pending: { label: "pending", color: "var(--dim)" },
  active: { label: "active", color: "var(--accent)" },
  complete: { label: "complete", color: "var(--ok)" },
  skipped: { label: "skipped", color: "var(--bad)" },
  out_of_order: { label: "out of order", color: "var(--bad)" },
  unverified: { label: "unverified", color: "var(--warn)" },
  overridden: { label: "overridden", color: "var(--mag)" },
  stalled: { label: "stalled", color: "var(--warn)" },
};

export function StepRow({
  index, name, state, confidence,
}: { index: number; name: string; state: StepState; confidence: number }) {
  const meta = STATE_META[state] ?? STATE_META.pending;
  const isActive = state === "active";
  return (
    <div
      className="fade-in"
      style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "11px 13px", marginBottom: 8,
        background: isActive ? "rgba(34,211,238,.07)" : "#0d141d",
        border: `1px solid ${isActive ? "#0e7490" : "var(--line)"}`,
        borderRadius: "var(--r-sm)",
        transition: "background .2s, border-color .2s",
      }}
    >
      <span className="mono" style={{ color: "var(--faint)", fontSize: 11, width: 16 }}>
        {index + 1}
      </span>
      <span
        className={isActive ? "dot dot-live" : "dot"}
        style={{ background: meta.color }}
      />
      <span style={{ flex: 1, fontSize: 14 }}>{name}</span>
      {confidence > 0 && (
        <span className="mono" style={{ fontSize: 11, color: "var(--dim)" }}>
          {confidence.toFixed(2)}
        </span>
      )}
      <span
        className="mono"
        style={{
          fontSize: 10.5, letterSpacing: 1, textTransform: "uppercase",
          color: meta.color, border: `1px solid ${meta.color}44`,
          padding: "3px 9px", borderRadius: 20,
        }}
      >
        {meta.label}
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- stat tile */

export function Stat({
  label, value, sub, tone,
}: { label: string; value: ReactNode; sub?: string; tone?: "ok" | "bad" | "warn" | "accent" }) {
  const color =
    tone === "ok" ? "var(--ok)" : tone === "bad" ? "var(--bad)"
    : tone === "warn" ? "var(--warn)" : tone === "accent" ? "var(--accent)" : "var(--txt)";
  return (
    <div className="card card-pad" style={{ padding: 14 }}>
      <div className="eyebrow" style={{ color: "var(--dim)" }}>{label}</div>
      <div className="mono" style={{ fontSize: 22, fontWeight: 700, color, marginTop: 6 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

/* -------------------------------------------------------------- empty state */

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div style={{ color: "var(--dim)", fontSize: 13, padding: "18px 4px", textAlign: "center" }}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
      <div className="eyebrow">{children}</div>
      <div style={{ flex: 1 }} />
      {right}
    </div>
  );
}
