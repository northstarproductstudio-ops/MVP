import type { ReactNode } from "react";

export type Stage = "import" | "map" | "ask";

export function StepHeader({ stage, onGo }: { stage: Stage; onGo?: (s: Stage) => void }) {
  const steps: [Stage, string][] = [
    ["import", "Import sources"],
    ["map", "Map fields"],
    ["ask", "Ask"],
  ];
  const idx = steps.findIndex(([s]) => s === stage);
  return (
    <nav className="steps" aria-label="Progress">
      {steps.map(([s, label], i) => (
        <span key={s} className="row" style={{ gap: 8 }}>
          {i > 0 && <span className="sep" />}
          <button
            className={`step ${i === idx ? "active" : i < idx ? "done" : ""}`}
            style={{ border: 0, background: i === idx ? undefined : "transparent" }}
            onClick={() => onGo?.(s)}
            disabled={!onGo}
          >
            <span className="n">{i < idx ? "✓" : i + 1}</span>
            {label}
          </button>
        </span>
      ))}
    </nav>
  );
}

export const fmt = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "–"
    : Number(n).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });

export const bytes = (n?: number) =>
  !n ? "" : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

export function Chip({ tone, children }: { tone?: "ok" | "warn" | "bad" | "info" | "new"; children: ReactNode }) {
  return <span className={`chip ${tone ?? ""}`}>{children}</span>;
}

export const STATE_LABEL: Record<string, [string, "ok" | "warn" | "bad" | "info" | "new" | undefined]> = {
  recognised: ["Recognised", "ok"],
  proposed: ["Proposed", "info"],
  new_category: ["New category", "new"],
  needs_decision: ["Needs a decision", "warn"],
  not_stored: ["Not stored", undefined],
  confirmed: ["Confirmed", "ok"],
  custom: ["Custom · added by you", "info"],
};
