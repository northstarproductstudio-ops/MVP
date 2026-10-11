// Report views: demand plan (SKU × city) and reasoning report (every line), versions, undo, export.
import { useEffect, useState } from "react";
import { api, download, type Json } from "../api";
import { fmt } from "../components/common";

const PLAN_COLS: [string, string][] = [
  ["plan_category", "Category"], ["sku_code", "SKU"], ["item_name", "Product"], ["city", "City"],
  ["sb_class", "Pattern"], ["pipeline_dp", "Pipeline DP"], ["final_dp", "Final DP"], ["order_qty", "Order qty"],
];
const REASON_COLS: [string, string][] = [
  ["sku_code", "SKU"], ["city", "City"], ["platform", "Platform"], ["abc", "ABC"], ["sb_class", "Pattern"],
  ["base", "Base"], ["trend_units", "Trend"], ["sell_through_units", "Sell-through"], ["fill_rate_units", "Fill rate"],
  ["stock_cover_units", "Stock cover"], ["agent_factors", "Agent factors"], ["judgment_units", "Judgment"],
  ["reference_dp", "Reference"], ["pipeline_dp", "Pipeline DP"], ["user_change", "Your change"],
  ["final_dp", "Final DP"], ["reason", "Reason"], ["confidence", "Confidence"], ["flags", "Flags"],
  ["batch", "Agent batch"], ["sources_used", "Sources used"], ["factors_off", "Factors off"],
];

export function ReportView({ runId, initialTab, onClose }: { runId: string; initialTab: "plan" | "reasoning"; onClose: () => void }) {
  const [tab, setTab] = useState(initialTab);
  const [data, setData] = useState<Json | null>(null);
  const [versions, setVersions] = useState<Json[]>([]);
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [filter, setFilter] = useState("");

  const load = () => {
    (tab === "plan" ? api.plan(runId, 1, version) : api.reasoning(runId, 1, version)).then(setData);
    api.versions(runId).then(setVersions);
  };
  useEffect(load, [tab, version, runId]); // eslint-disable-line

  const cols = tab === "plan" ? PLAN_COLS : REASON_COLS;
  const rows: Json[] = (data?.rows ?? []).filter((r: Json) =>
    !filter || Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(filter.toLowerCase())));
  const current = versions.find((v) => v.is_current);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="card" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ padding: "14px 20px" }}>
          <div className="row">
            <h2>{tab === "plan" ? "Demand plan report" : "Reasoning report"}</h2>
            <select className="inline" value={version ?? ""} onChange={(e) => setVersion(e.target.value === "" ? undefined : Number(e.target.value))}>
              <option value="">Current (v{current?.version ?? 0})</option>
              {versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.note}</option>)}
            </select>
            <input className="inline" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div className="row">
            {current && current.version > 0 && <button className="btn" onClick={async () => { await api.undo(runId); setVersion(undefined); load(); }}>Undo v{current.version}</button>}
            <button className="btn" onClick={() => download(`/runs/${runId}/export/${tab}.xlsx`, `${tab}-v${current?.version ?? 0}.xlsx`)}>Export</button>
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="tabs">
          <button className={`tab ${tab === "plan" ? "active" : ""}`} onClick={() => setTab("plan")}>Plan (SKU × city)</button>
          <button className={`tab ${tab === "reasoning" ? "active" : ""}`} onClick={() => setTab("reasoning")}>Reasoning (every line)</button>
        </div>
        <div className="scroll" style={{ maxHeight: "none", flex: 1 }}>
          <table className="grid">
            <thead><tr>{cols.map(([k, l]) => <th key={k} className={typeof rows[0]?.[k] === "number" ? "num" : ""}>{l}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{cols.map(([k]) => (
                  <td key={k} className={typeof r[k] === "number" ? "num" : ""} style={k === "reason" ? { minWidth: 280 } : undefined}>
                    {typeof r[k] === "number" ? fmt(r[k], k === "base" ? 1 : 0) : (r[k] ?? "")}
                  </td>))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tab === "reasoning" && (data?.not_planned ?? []).length > 0 && (
          <div className="notice" style={{ margin: "8px 16px" }}>
            <strong>Not planned: no sales history</strong> — {data!.not_planned.join(", ")}
          </div>
        )}
        <div className="footer"><span className="muted">Showing {rows.length} of {fmt(data?.total)} rows · version {data?.version}</span></div>
      </div>
    </div>
  );
}
