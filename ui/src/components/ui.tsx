import type { CSSProperties, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { CircleAlert, RefreshCw } from "lucide-react";

export type Tone = "ok" | "alert" | "caution" | "info" | "accent";

export function Badge({
  tone, mono, icon: Icon, children, title,
}: { tone?: Tone; mono?: boolean; icon?: LucideIcon; children: ReactNode; title?: string }) {
  return (
    <span className={`badge${tone ? ` ${tone}` : ""}${mono ? " mono" : ""}`} title={title}>
      {Icon && <Icon size={12} strokeWidth={2.2} />}
      {children}
    </span>
  );
}

export function PanelHead({
  icon: Icon, title, right,
}: { icon?: LucideIcon; title: ReactNode; right?: ReactNode }) {
  return (
    <div className="panel-head">
      <div className="panel-title">
        {Icon && <Icon size={15} />}
        {title}
      </div>
      <span className="spacer" />
      {right}
    </div>
  );
}

export function Note({
  tone, icon: Icon = CircleAlert, children, style,
}: { tone?: Tone; icon?: LucideIcon; children: ReactNode; style?: CSSProperties }) {
  return (
    <div className={`note${tone ? ` ${tone}` : ""}`} style={style}>
      <Icon size={15} />
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function Empty({
  icon: Icon, title, children,
}: { icon?: LucideIcon; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {Icon && <div className="empty-icon"><Icon size={20} /></div>}
      <div>
        {title && <strong>{title}</strong>}
        {title && children && <br />}
        {children}
      </div>
    </div>
  );
}

/** Errors are shown, never swallowed. A blank panel with no explanation looks
 *  like a feature that does not exist rather than a request that failed. */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Note tone="alert" style={{ marginBottom: 12, alignItems: "center" }}>
      <div className="row">
        <span style={{ flex: 1 }}>{message}</span>
        {onRetry && (
          <button className="btn btn-sm" onClick={onRetry}>
            <RefreshCw size={13} /> Retry
          </button>
        )}
      </div>
    </Note>
  );
}

export function Seg<T extends string>({
  value, options, onChange, block,
}: {
  value: T;
  options: { value: T; label: ReactNode; icon?: LucideIcon }[];
  onChange: (v: T) => void;
  block?: boolean;
}) {
  return (
    <div className={`seg${block ? " block" : ""}`} role="radiogroup">
      {options.map(({ value: v, label, icon: Icon }) => (
        <button
          key={v}
          role="radio"
          aria-checked={v === value}
          className={v === value ? "on" : ""}
          onClick={() => onChange(v)}
        >
          {Icon && <Icon size={13} />}
          {label}
        </button>
      ))}
    </div>
  );
}
