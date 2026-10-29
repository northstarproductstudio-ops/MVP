// Teal DI design-system parts as React components: buttons, fields, state chips, banners,
// the four blanks (loading, empty, no results, error), dialog, drawer and toast.
import { useEffect, useRef, type ReactNode } from "react";
import {
  ChevronDown, CircleCheck, Copy, EyeOff, Info, OctagonX, Pencil, Search, TriangleAlert, X, type LucideIcon,
} from "lucide-react";

export type Tone = "good" | "warn" | "crit" | "proposed" | "custom" | "ignored" | "reading" | "neutral";

type BtnProps = {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  // aria-disabled keeps the control focusable so the reason stays readable
  disabled?: boolean;
  reason?: string;
  busy?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
  children?: ReactNode;
  describedBy?: string;
};

export function Button({ variant = "secondary", size = "md", icon: I, iconRight: R, disabled, reason, busy, onClick, type = "button", className, children, describedBy }: BtnProps) {
  const off = disabled || busy;
  return (
    <button
      type={type}
      className={`btn ${variant === "secondary" ? "" : variant} ${size === "md" ? "" : size} ${className ?? ""}`}
      aria-disabled={off || undefined}
      aria-busy={busy || undefined}
      aria-describedby={describedBy}
      title={disabled ? reason : undefined}
      onClick={(e) => { if (off) { e.preventDefault(); return; } onClick?.(); }}
    >
      {busy ? <span className="spinner md" aria-hidden="true" /> : I && <I size={16} aria-hidden="true" />}
      {children}
      {R && <R size={R === ChevronDown ? 14 : 16} aria-hidden="true" />}
    </button>
  );
}

export function IconButton({ icon: I, label, size = "md", onClick, disabled }: {
  icon: LucideIcon; label: string; size?: "sm" | "md"; onClick?: () => void; disabled?: boolean;
}) {
  return (
    <button type="button" className={`iconbtn ${size === "sm" ? "sm" : ""}`} aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      <I size={16} aria-hidden="true" />
    </button>
  );
}

export function SearchField({ value, onChange, placeholder, width = 240, label }: {
  value: string; onChange: (v: string) => void; placeholder: string; width?: number | string; label?: string;
}) {
  return (
    <label className="field" style={{ width }}>
      <span className="sr-only">{label ?? placeholder}</span>
      <Search size={16} aria-hidden="true" />
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      {value && <button type="button" className="linkbtn" aria-label="Clear search" onClick={() => onChange("")}><X size={14} /></button>}
    </label>
  );
}

export function SelectField<T extends string>({ value, onChange, options, label, width = 180, icon: I, size }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string; width?: number | string;
  icon?: LucideIcon; size?: "sm";
}) {
  return (
    <label className={`field ${size ?? ""}`} style={{ width }}>
      <span className="sr-only">{label}</span>
      {I && <I size={16} aria-hidden="true" />}
      <select value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown className="chev" size={14} aria-hidden="true" />
    </label>
  );
}

// Good = ink check · warning = red outline + triangle · critical = red tint + octagon.
export function StateChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  switch (tone) {
    case "good": return <span className="st"><CircleCheck size={14} aria-hidden="true" />{children}</span>;
    case "warn": return <span className="st warn"><TriangleAlert size={14} aria-hidden="true" />{children}</span>;
    case "crit": return <span className="st crit"><OctagonX size={14} aria-hidden="true" />{children}</span>;
    case "proposed": return <span className="st" style={{ gap: 9 }}><span className="hollow" aria-hidden="true" />{children}</span>;
    case "custom": return <span className="st"><Pencil size={14} aria-hidden="true" />{children}</span>;
    case "ignored": return <span className="st muted"><EyeOff size={14} aria-hidden="true" />{children}</span>;
    case "reading": return <span className="st" style={{ gap: 8 }}><span className="spinner muted" aria-hidden="true" />{children}</span>;
    default: return <span className="st muted">{children}</span>;
  }
}

export function Banner({ tone, children, role }: { tone: "info" | "warn" | "crit"; children: ReactNode; role?: "status" | "alert" }) {
  const I = tone === "info" ? Info : tone === "warn" ? TriangleAlert : OctagonX;
  return <div className={`banner ${tone}`} role={role ?? (tone === "info" ? undefined : "status")}><I size={14} aria-hidden="true" /><div className="grow">{children}</div></div>;
}

export function Loading({ text, rows = 3 }: { text: string; rows?: number }) {
  return (
    <div className="stack g12" style={{ padding: 16 }}>
      <span className="skel" style={{ height: 10, width: "45%" }} aria-hidden="true" />
      {Array.from({ length: rows }, (_, i) => <span key={i} className="skel" style={{ height: 28, width: i === rows - 1 ? "80%" : undefined }} aria-hidden="true" />)}
      <span role="status" className="t-small muted" style={{ paddingTop: 8, borderTop: "1px solid var(--line)" }}>{text}</span>
    </div>
  );
}

export function Empty({ text, action, onAction }: { text: string; action?: string; onAction?: () => void }) {
  return <div className="blank"><span>{text}</span>{action && <Button variant="primary" size="sm" onClick={onAction}>{action}</Button>}</div>;
}

export function NoResults({ count, text, action, onAction }: { count: string; text: string; action: string; onAction: () => void }) {
  return (
    <div className="blank" style={{ gap: 8 }}>
      <span className="strong">{count}</span>
      <span className="t-small muted">{text}</span>
      <Button size="sm" onClick={onAction}>{action}</Button>
    </div>
  );
}

export function ErrorBlank({ text, action = "Retry", onAction, details }: { text: string; action?: string; onAction?: () => void; details?: string }) {
  return (
    <div className="blank error" role="alert" style={{ gap: 8 }}>
      <OctagonX size={16} aria-hidden="true" />
      <span className="t-small">{text}</span>
      <span className="row">
        {onAction && <Button variant="primary" size="sm" onClick={onAction}>{action}</Button>}
        {details && <Button size="sm" icon={Copy} onClick={() => navigator.clipboard?.writeText(details)}>Copy error details</Button>}
      </span>
    </div>
  );
}

function useEscape(onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
}

export function Dialog({ title, onClose, children, footer, width }: {
  title: string; onClose: () => void; children: ReactNode; footer: ReactNode; width?: number;
}) {
  useEscape(onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelector<HTMLElement>("[data-autofocus], button")?.focus(); }, []);
  return (
    <>
      <div className="scrim" aria-hidden="true" onClick={onClose} />
      <div className="dialog-wrap" onClick={onClose}>
        <div ref={ref} className="dialog" role="dialog" aria-modal="true" aria-label={title} style={width ? { width } : undefined} onClick={(e) => e.stopPropagation()}>
          <h2>{title}</h2>
          {children}
          <div className="row end" style={{ marginTop: 8 }}>{footer}</div>
        </div>
      </div>
    </>
  );
}

export function Drawer({ label, onClose, head, children, foot }: {
  label: string; onClose: () => void; head: ReactNode; children: ReactNode; foot?: ReactNode;
}) {
  useEscape(onClose);
  return (
    <>
      <div className="scrim" aria-hidden="true" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={label}>
        <div className="drawer-head">{head}<IconButton icon={X} label="Close" size="sm" onClick={onClose} /></div>
        <div className="drawer-body">{children}</div>
        {foot && <div className="drawer-foot">{foot}</div>}
      </aside>
    </>
  );
}

export function Toast({ children, onDone }: { children: ReactNode; onDone: () => void }) {
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => { const t = setTimeout(() => done.current(), 4000); return () => clearTimeout(t); }, []);
  return <div className="toast" role="status"><CircleCheck size={16} aria-hidden="true" /><span className="grow">{children}</span><IconButton icon={X} label="Dismiss" size="sm" onClick={onDone} /></div>;
}

export function Progress({ pct, thin, label }: { pct: number; thin?: boolean; label?: string }) {
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  return <span className={`progress ${thin ? "thin" : ""}`} role="progressbar" aria-label={label} aria-valuenow={v} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${v}%` }} /></span>;
}
