// Gap 10: "What the plan needs" — every variable the pipeline uses and whether it is mapped.
// Never blocks saving or Ask; only the demand-plan action is gated (same rule as the run).
import { useEffect, useState } from "react";
import { api, type Json } from "../api";
import { Chip } from "./common";

export function useReadiness(sessionId: string, refreshKey: unknown): [Json | null, () => void] {
  const [r, setR] = useState<Json | null>(null);
  const load = () => { api.readiness(sessionId).then(setR).catch(() => setR(null)); };
  useEffect(load, [sessionId, JSON.stringify(refreshKey)]); // eslint-disable-line
  return [r, load];
}

const GROUPS: { key: "plan" | "factor" | "questions"; title: string; hint: string }[] = [
  { key: "plan", title: "Required for a demand plan", hint: "A plan cannot run until these are mapped." },
  { key: "factor", title: "Improves the plan", hint: "Each one switches a factor on; the plan runs without them." },
  { key: "questions", title: "Used for questions only", hint: "Stored so you can ask about them; the plan never reads them." },
];

const FACTOR_LABEL: Record<string, string> = {
  base: "Ordered-quantity base", abc: "ABC value ranking", crosswalk: "SKU name matching", fill_rate: "Fill rate",
  open_po: "Open PO netting", stock_cover: "Stock cover", sell_through: "Sell-through", osa_signal: "OSA signal",
  market_ceiling: "Market ceiling", discount_signal: "Discount signal", netting: "Case size and MOQ",
  category_batches: "Category planning batches",
};

export function ReadinessPanel({ readiness, onMapNow, compact }: {
  readiness: Json | null;
  onMapNow?: (variable: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(!compact);
  if (!readiness) return null;
  const vars: Json[] = readiness.variables ?? [];
  const group = (k: string) => vars.filter((v) =>
    k === "plan" ? v.needed_for === "plan" : k === "factor" ? String(v.needed_for).startsWith("factor:") : v.needed_for === "questions");
  const missingPlan = group("plan").filter((v) => v.status !== "mapped");
  return (
    <div className="card pad">
      <div className="row between wrap">
        <div>
          <h2>What the plan needs</h2>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {readiness.plan_ready
              ? "Ready: every field a demand plan needs is mapped."
              : `A demand plan needs: ${(readiness.blocking_display ?? []).join(", ") || "a sales-order file"}.`}
            {" "}Saving and asking about your data are never blocked.
          </p>
        </div>
        <div className="row">
          <Chip tone={readiness.plan_ready ? "ok" : "bad"}>{readiness.plan_ready ? "Plan ready" : `${missingPlan.length} required missing`}</Chip>
          <button className="btn ghost small" onClick={() => setOpen(!open)}>{open ? "Hide fields" : "Show fields"}</button>
        </div>
      </div>
      {open && GROUPS.map((g) => {
        const rows = group(g.key);
        if (!rows.length) return null;
        return (
          <div key={g.key} style={{ marginTop: 14 }}>
            <h3 style={{ margin: "0 0 2px", fontSize: 14 }}>{g.title}</h3>
            <p className="muted" style={{ margin: "0 0 6px", fontSize: 12.5 }}>{g.hint}</p>
            <table className="grid">
              <tbody>
                {rows.map((v) => (
                  <tr key={v.name}>
                    <td style={{ width: 240 }}><strong>{v.display_name}</strong>{v.description && <div className="muted" style={{ fontSize: 12 }}>{v.description}</div>}</td>
                    <td style={{ width: 220 }}><StatusChip v={v} group={g.key} /></td>
                    <td className="muted">
                      {v.mapped_from ?? v.proposed_from ?? ""}
                      {g.key === "plan" && v.status !== "mapped" && onMapNow && (
                        <button className="btn small" style={{ marginLeft: 8 }} onClick={() => onMapNow(v.name)}>Map now</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function StatusChip({ v, group }: { v: Json; group: string }) {
  if (v.status === "mapped") return <Chip tone="ok">Mapped</Chip>;
  if (v.status === "proposed") return <Chip tone="info">Proposed · not yet confirmed</Chip>;
  if (v.status === "needs_decision") return <Chip tone="warn">Needs a decision</Chip>;
  if (group === "plan") return <Chip tone="bad">Missing</Chip>;
  if (group === "factor") {
    const f = String(v.needed_for).split(":")[1];
    return <Chip>Off: {FACTOR_LABEL[f] ?? f} won't run</Chip>;
  }
  return <Chip>Not mapped</Chip>;
}
