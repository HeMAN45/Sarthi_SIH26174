import type { ReactNode } from "react";

/** Radial progress. ``value`` is 0..1. */
export function Ring({
  value, size = 64, stroke = 5, color = "var(--accent)", children,
}: { value: number; size?: number; stroke?: number; color?: string; children?: ReactNode }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <div style={{ position: "relative", width: size, height: size, flex: "none" }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                stroke="var(--tint-3)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                stroke={color} strokeWidth={stroke} strokeLinecap="butt"
                strokeDasharray={c} strokeDashoffset={c * (1 - v)}
                style={{ transition: "stroke-dashoffset .5s ease, stroke .3s" }} />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center",
                    textAlign: "center", lineHeight: 1.1 }}>
        {children}
      </div>
    </div>
  );
}

export interface Rule { y: number; color: string }

/** A rolling trace. Stretches to its container; strokes stay crisp. */
export function Sparkline({
  values, height = 56, min = 0, max = 1, color = "var(--accent)", rules = [], fill = true,
}: {
  values: number[]; height?: number; min?: number; max?: number;
  color?: string; rules?: Rule[]; fill?: boolean;
}) {
  const W = 300;
  const H = height;
  const pad = 3;
  const span = max - min || 1;
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (Math.max(min, Math.min(max, v)) - min) / span);
  const n = values.length;
  const x = (i: number) => (n <= 1 ? W : (i / (n - 1)) * W);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const gid = `g${color.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H}
         style={{ display: "block", overflow: "visible" }} aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {rules.map((r) => (
        <line key={r.y} x1="0" x2={W} y1={y(r.y)} y2={y(r.y)} stroke={r.color}
              strokeWidth="1" strokeDasharray="4 4" opacity=".75"
              vectorEffect="non-scaling-stroke" />
      ))}
      {n > 1 && fill && (
        <path d={`${line}L${W},${H}L0,${H}Z`} fill={`url(#${gid})`} stroke="none" />
      )}
      {n > 1 && (
        <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round"
              strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      )}
      {n > 0 && (
        <circle cx={x(n - 1)} cy={y(values[n - 1])} r="3" fill={color}
                vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

export function Bar({ value, color }: { value: number; color?: string }) {
  return (
    <div className="bar">
      <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%`, background: color }} />
    </div>
  );
}
