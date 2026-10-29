// S-08 report viewer: Overall and per-city plan tables, City shares, Factors used and Reasoning, with the
// Why panel, version switcher and compare, undo and export. Every number is read from the run's lines.
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowLeft, Download, GitBranch, GitCompare, ListOrdered, Lock, Pencil, TriangleAlert, Undo2 } from "lucide-react";
import { api, download, type Json } from "../api";
import { dateLabel, fmt, monthLabel, pct, signed } from "../components/common";
import { Banner, Button, Dialog, Drawer, Empty, ErrorBlank, IconButton, Loading, NoResults, SearchField, SelectField } from "../components/ui";

type Tab = string; // "overall" | `city:${name}` | "shares" | "factors" | "reasoning"

const PATTERN: Record<string, string> = {
  smooth: "M1 7C4 5 6 5 8 6s4 1 7-1", erratic: "M1 9 4 3l2 5 3-6 2 8 4-6", intermittent: "M2 11V6M9 11V6M14 11V6",
  lumpy: "M2 11V8M8 11V1M13 11V5", new: "M1 6h5M12 3.5a2.5 2.5 0 1 1 0 5a2.5 2.5 0 1 1 0-5",
};
const FACTORS: [string, string, string | null][] = [
  ["trend", "Trend", "trend_m"], ["sell_through", "Sell-through", "sell_through_m"], ["fill_rate", "Fill rate", "fill_rate_m"],
  ["stock_cover", "Stock cover", null], ["judgment", "Judgment", "judgment_m"],
];
const flagsOf = (r: Json): string[] => (r.flags ? String(r.flags).split(",").map((s) => s.trim()).filter(Boolean) : []);
const qty = (r: Json) => r.order_qty ?? r.final_dp ?? 0;

async function allLines(runId: string, version?: number): Promise<Json> {
  const first = await api.reasoning(runId, 1, version);
  let rows: Json[] = first.rows ?? [];
  for (let p = 2; rows.length < (first.total ?? 0) && p < 100; p++) rows = rows.concat((await api.reasoning(runId, p, version)).rows ?? []);
  return { ...first, rows };
}

export function ReportView({ runId, session, onBack, onEditLine }: {
  runId: string; session: Json | null; onBack: () => void; onEditLine: (text: string) => void;
}) {
  const [run, setRun] = useState<Json | null>(null);
  const [data, setData] = useState<Json | null>(null);
  const [versions, setVersions] = useState<Json[]>([]);
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overall");
  const [q, setQ] = useState("");
  const [flag, setFlag] = useState<string | null>(null);
  const [why, setWhy] = useState<Json | null>(null);
  const [exporting, setExporting] = useState(false);
  const [compare, setCompare] = useState<Json[] | null>(null);
  const [limit, setLimit] = useState(300);

  const load = () => {
    setError(null);
    setData(null);
    api.run(runId).then(setRun).catch(() => undefined);
    api.versions(runId).then(setVersions).catch(() => undefined);
    allLines(runId, version).then(setData).catch((e) => setError(e.message));
  };
  useEffect(load, [runId, version]); // eslint-disable-line

  const rows: Json[] = data?.rows ?? [];
  const cities = useMemo(() => Array.from(new Set(rows.map((r) => r.city).filter(Boolean))).sort(), [rows]);
  const platforms = useMemo(() => Array.from(new Set(rows.map((r) => r.platform).filter(Boolean))).sort(), [rows]);
  const flagCounts = useMemo(() => {
    const m = new Map<string, number>();
    rows.forEach((r) => flagsOf(r).forEach((f) => m.set(f, (m.get(f) ?? 0) + 1)));
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [rows]);
  const flagged = rows.filter((r) => flagsOf(r).length).length;
  const current = versions.find((v) => v.is_current);
  const shownVersion = data?.version ?? current?.version ?? 0;
  const vLabel = (v: Json) => `Version ${v.version}${v.version === 0 ? " · pipeline original" : v.note ? ` · ${v.note}` : ""}`;

  const city = tab.startsWith("city:") ? tab.slice(5) : null;
  const filtered = rows.filter((r) => (!city || r.city === city) && (!flag || flagsOf(r).includes(flag))
    && (!q || `${r.sku_code} ${r.item_name}`.toLowerCase().includes(q.toLowerCase())));
  const clear = () => { setQ(""); setFlag(null); };

  const startCompare = async () => {
    const base = await allLines(runId, 0);
    const by = new Map(base.rows.map((r: Json) => [r.line_id, r]));
    setCompare(rows.map((r) => ({ ...r, before: (by.get(r.line_id) as Json | undefined)?.final_dp ?? null })));
  };

  const tabs: [Tab, string][] = [["overall", "Overall"], ...cities.map((c) => [`city:${c}`, c] as [Tab, string]),
    ["shares", "City shares"], ["factors", "Factors used"], ["reasoning", "Reasoning"]];

  return (
    <div className="rv">
      <div className="rv-head">
        <div className="r1">
          <IconButton icon={ArrowLeft} label="Back to chat" size="sm" onClick={onBack} />
          <div className="stack grow" style={{ gap: 0 }}>
            <span className="h-section">Order plan · {monthLabel(run?.target_month)}</span>
            <span className="t-caption muted">Run {runId.slice(0, 8)} · based on sales to {dateLabel(run?.as_of)}{platforms.length ? ` · ${platforms.join(" · ")}` : ""}</span>
          </div>
          {versions.length > 0 && (
            <SelectField label="Report version" icon={GitBranch} width={260} value={String(version ?? shownVersion)}
              onChange={(v) => { setCompare(null); setVersion(Number(v)); }} options={versions.map((v) => ({ value: String(v.version), label: vLabel(v) }))} />
          )}
          {shownVersion > 0 && !compare && <Button icon={GitCompare} onClick={startCompare}>Compare with version 0</Button>}
          {current && current.version > 0 && shownVersion === current.version && (
            <Button icon={Undo2} onClick={async () => { await api.undo(runId); setVersion(undefined); load(); }}>Undo version {current.version}</Button>
          )}
          <Button variant="primary" icon={Download} onClick={() => setExporting(true)}>Export</Button>
        </div>
        <div className="r2">
          {flagged > 0 && <span className="row strong" style={{ gap: 6, marginRight: 4 }}><TriangleAlert size={14} className="risk" aria-hidden="true" />{fmt(flagged)} lines need review</span>}
          {flagCounts.slice(0, 6).map(([f, n]) => (
            <button key={f} type="button" className="flagchip" aria-pressed={flag === f} onClick={() => setFlag(flag === f ? null : f)}>{(f.charAt(0).toUpperCase() + f.slice(1)).replace(/_/g, " ")} · {fmt(n)}</button>
          ))}
          <span className="grow" />
          <SearchField value={q} onChange={setQ} placeholder="Search SKU or product" width={260} />
        </div>
        <div className="r3">
          <div role="tablist" aria-label="Report sections" className="tabs">
            {tabs.map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={`tab ${id === "shares" ? "gap" : ""}`}
                onClick={() => { setTab(id); setLimit(300); }}>{label}</button>
            ))}
          </div>
        </div>
      </div>

      <main className="rv-body">
        {(run?.warnings ?? []).length > 0 && tab === "overall" && (
          <Banner tone="warn"><span className="stack"><span className="strong">{run!.warnings.length} validation warnings</span>{run!.warnings.map((w: string) => <span key={w}>{w}</span>)}</span></Banner>
        )}
        <section className="card">
          {error ? <ErrorBlank text={`This plan didn’t load. Nothing was changed. ${error}`} onAction={load} />
            : !data ? <Loading text={`Loading version ${version ?? shownVersion} of the plan.`} rows={8} />
            : compare ? <Compare rows={compare} version={shownVersion} onClose={() => setCompare(null)} />
            : tab === "shares" ? <Shares rows={filtered} cities={cities} />
            : tab === "factors" ? <Factors rows={filtered} />
            : tab === "reasoning" ? <Reasoning rows={filtered} total={rows.length} month={monthLabel(run?.target_month)} version={shownVersion} runId={runId} onWhy={setWhy} />
            : city && !rows.some((r) => r.city === city) ? <Empty text={`This plan has no lines for ${city}.`} action="View Overall" onAction={() => setTab("overall")} />
            : filtered.length === 0 ? <NoResults count={`0 of ${fmt(rows.length)} lines`} text="No lines match these filters." action="Clear filters" onAction={clear} />
            : (
              <>
                <div className="card-head" style={{ padding: "8px 12px 8px 16px" }}>
                  <span className="t-small muted">{city ?? "All cities"} · {platforms.length > 1 ? "all platforms" : platforms[0] ?? ""} · sorted by order, descending · read-only fields marked <Lock size={12} style={{ verticalAlign: -2 }} aria-label="locked" /></span>
                </div>
                <PlanTable rows={filtered.slice(0, limit)} onWhy={setWhy} />
                <div className="card-foot line">
                  <span>Showing {fmt(Math.min(limit, filtered.length))} of {fmt(filtered.length)} lines{filtered.length > limit && <> · <button type="button" className="linkbtn" onClick={() => setLimit(limit + 500)}>Show more</button></>}</span>
                  <span>Click an order quantity to see what decided it</span>
                </div>
              </>
            )}
        </section>
      </main>

      {why && <WhyPanel r={why} onClose={() => setWhy(null)}
        onReasoning={() => { setQ(why.sku_code); setTab("reasoning"); setWhy(null); }}
        onEdit={() => { onEditLine(`Change the plan for ${why.sku_code} (${why.item_name}) in ${why.city}${why.platform ? ` on ${why.platform}` : ""} from ${fmt(why.final_dp)} to [new units], because [reason].`); }} />}
      {exporting && <ExportDialog runId={runId} month={run?.target_month} version={shownVersion} label={vLabel(versions.find((v) => v.version === shownVersion) ?? { version: shownVersion })}
        flagged={flagged} onReview={() => { setExporting(false); setTab("overall"); setFlag(flagCounts[0]?.[0] ?? null); }} onClose={() => setExporting(false)} session={session} />}
    </div>
  );
}

function PatternChip({ cls }: { cls?: string | null }) {
  if (!cls) return <span className="muted">—</span>;
  const d = PATTERN[cls.toLowerCase()];
  return (
    <span className="tag soft">
      {d && <svg aria-hidden="true" width="16" height="12" viewBox="0 0 16 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>}
      {cls.charAt(0).toUpperCase() + cls.slice(1)}
    </span>
  );
}

function Source({ r }: { r: Json }) {
  if (r.user_change) return <span className="tag ink">Edited · v{r.report_version}</span>;
  if (r.agent_factors) return <span className="tag ai">✦ Agent</span>;
  return <span className="tag">Plan</span>;
}

function PlanTable({ rows, onWhy }: { rows: Json[]; onWhy: (r: Json) => void }) {
  const sorted = [...rows].sort((a, b) => qty(b) - qty(a));
  return (
    <div className="tblwrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
      <table className="tbl compact">
        <thead><tr>
          <th className="strong-line" style={{ borderRight: "1px solid var(--line)" }}>SKU code</th><th className="strong-line">Product</th>
          <th className="strong-line">Platform · city</th><th className="ctr strong-line">Pattern</th><th className="ctr strong-line">ABC</th>
          <th className="num strong-line">Pipeline DP, units</th>
          <th className="num strong-line"><span className="row end" style={{ gap: 4 }}>Reference DP, units<Lock size={12} aria-label="read-only" /></span></th>
          <th className="num strong-line">Final DP, units</th>
          <th className="num strong-line" style={{ color: "var(--text)" }}><span className="row end" style={{ gap: 4 }}>Order, units<ArrowDown size={12} aria-hidden="true" /></span></th>
          <th className="strong-line">Source</th><th className="ctr strong-line">Review</th>
        </tr></thead>
        <tbody>
          {sorted.map((r) => {
            const f = flagsOf(r);
            return (
              <tr key={r.line_id} className={r.user_change ? "sel" : undefined}>
                <td style={{ borderRight: "1px solid var(--line)" }}><span className="row" style={{ gap: 6 }}>
                  <span style={{ width: 14, display: "inline-flex", opacity: f.length ? 1 : 0 }} className="risk"><TriangleAlert size={14} aria-label={f.length ? "Flagged" : undefined} aria-hidden={!f.length} /></span>{r.sku_code}</span></td>
                <td className="ellipsis" style={{ maxWidth: 220 }}>{r.item_name ?? "—"}</td>
                <td className="muted" style={{ whiteSpace: "nowrap" }}>{[r.platform, r.city].filter(Boolean).join(" · ")}</td>
                <td className="ctr"><PatternChip cls={r.sb_class} /></td>
                <td className="ctr">{r.abc ? <span className={`abc ${r.abc}`}>{r.abc}</span> : "—"}</td>
                <td className="num">{fmt(r.pipeline_dp)}</td>
                <td className="num muted">{fmt(r.reference_dp)}</td>
                <td className="num">{fmt(r.final_dp)}</td>
                <td className="num"><button type="button" className="linkbtn dotted" onClick={() => onWhy(r)}>{fmt(r.order_qty ?? r.final_dp)}</button></td>
                <td><Source r={r} /></td>
                <td className="ctr muted">{f.length ? "Needs review" : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Shares({ rows, cities }: { rows: Json[]; cities: string[] }) {
  const by = new Map<string, { sku: string; name: string; total: number; c: Record<string, number> }>();
  rows.forEach((r) => {
    const e = by.get(r.sku_code) ?? { sku: r.sku_code, name: r.item_name, total: 0, c: {} as Record<string, number> };
    e.total += r.final_dp ?? 0;
    e.c[r.city] = (e.c[r.city] ?? 0) + (r.final_dp ?? 0);
    by.set(r.sku_code, e);
  });
  const list = Array.from(by.values()).sort((a, b) => b.total - a.total);
  return (
    <>
      <div className="card-head"><h2>City shares</h2><span className="t-small muted">Share of each SKU's final demand plan · each row sums to 100% · read-only</span></div>
      <div className="tblwrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
        <table className="tbl compact">
          <thead><tr><th style={{ borderRight: "1px solid var(--line)" }}>SKU code</th><th>Product</th><th className="num">Final DP, units</th>
            {cities.map((c) => <th key={c} className="num" style={{ minWidth: 72 }}>{c}, %</th>)}</tr></thead>
          <tbody>
            {list.map((e) => (
              <tr key={e.sku}>
                <td style={{ borderRight: "1px solid var(--line)", whiteSpace: "nowrap" }}>{e.sku}</td>
                <td className="ellipsis">{e.name}</td>
                <td className="num">{fmt(e.total)}</td>
                {cities.map((c) => <td key={c} className="num">{e.c[c] == null ? "—" : e.total ? fmt((e.c[c] / e.total) * 100, 1) : "—"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-foot line"><span>Showing {fmt(list.length)} of {fmt(list.length)} SKUs · city columns scroll if more cities are added</span></div>
    </>
  );
}

function Factors({ rows }: { rows: Json[] }) {
  const out = FACTORS.map(([key, label, m]) => {
    const applied = rows.filter((r) => (m ? r[m] != null : r[`${key}_units`]));
    const mean = m && applied.length ? applied.reduce((s, r) => s + r[m], 0) / applied.length : null;
    return { label, lines: applied.length, mean, units: rows.reduce((s, r) => s + (r[`${key}_units`] ?? 0), 0) };
  });
  const agent = rows.filter((r) => r.agent_factors).length;
  return (
    <>
      <div className="card-head" style={{ flexDirection: "column", alignItems: "stretch", gap: 2 }}>
        <span className="row between"><h2>Factors used</h2><span className="t-caption muted row" style={{ gap: 4 }}><Lock size={12} aria-hidden="true" />Reference · read-only</span></span>
        <span className="t-small muted">The forecast factors this run applied, summed over the lines shown.</span>
      </div>
      <table className="tbl compact">
        <thead><tr><th>Factor</th><th className="num">Lines applied</th><th className="num">Mean multiplier, %</th><th className="num">Total effect, units</th></tr></thead>
        <tbody>
          {out.map((f) => <tr key={f.label}><td>{f.label}</td><td className="num">{fmt(f.lines)}</td><td className="num">{pct(f.mean)}</td><td className="num">{signed(f.units)}</td></tr>)}
          <tr><td>✦ Agent factors</td><td className="num">{fmt(agent)}</td><td className="num muted">See Reasoning</td><td className="num muted">—</td></tr>
        </tbody>
      </table>
      <div className="banner info" style={{ borderRadius: 0, border: 0, borderTop: "1px solid var(--line)" }}>
        <span>Lead times, pricing and promotion values come from your source files and can't be edited here. This app has no Release or Commit action.</span>
      </div>
    </>
  );
}

function Reasoning({ rows, total, month, version, runId, onWhy }: { rows: Json[]; total: number; month: string; version: number; runId: string; onWhy: (r: Json) => void }) {
  const groups: [string, string, string][] = [["Trend", "trend_m", "trend_units"], ["Sell-through", "sell_through_m", "sell_through_units"], ["Fill rate", "fill_rate_m", "fill_rate_units"], ["Judgment", "judgment_m", "judgment_units"]];
  const shown = rows.slice(0, 500);
  return (
    <>
      <div className="card-head" style={{ padding: "10px 12px 10px 16px" }}>
        <span className="strong">Reasoning report · target {month} · report version {version} · run {runId.slice(0, 8)}</span>
        <Button size="sm" icon={Download} onClick={() => download(`/runs/${runId}/export/reasoning.xlsx`, `reasoning-v${version}.xlsx`)}>Export reasoning</Button>
      </div>
      <div className="tblwrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
        <table className="tbl compact">
          <thead>
            <tr>
              <th colSpan={4} style={{ height: 28 }} />
              {groups.map(([g]) => <th key={g} colSpan={2} className="ctr vline" style={{ height: 28, color: "var(--text)", fontWeight: 500 }}>{g}</th>)}
            </tr>
            <tr>
              <th className="strong-line">Product name</th><th className="strong-line">City · platform</th><th className="strong-line">Category</th><th className="num strong-line">Order quantity</th>
              {groups.map(([g]) => [<th key={`${g}m`} className="num vline strong-line">Multiplier, %</th>, <th key={`${g}u`} className="num strong-line">Effect, units</th>])}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.line_id} className="clickable" onClick={() => onWhy(r)}>
                <td style={{ whiteSpace: "nowrap" }}>{r.item_name ?? r.sku_code}</td>
                <td className="muted" style={{ whiteSpace: "nowrap" }}>{[r.city, r.platform].filter(Boolean).join(" · ")}</td>
                <td className="muted">{r.plan_category ?? "—"}</td>
                <td className="num strong">{fmt(r.order_qty ?? r.final_dp)}</td>
                {groups.map(([g, m, u]) => [<td key={`${g}m`} className="num vline">{pct(r[m])}</td>, <td key={`${g}u`} className="num">{r[m] == null && !r[u] ? "—" : signed(r[u])}</td>])}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-foot line"><span>Showing {fmt(shown.length)} of {fmt(rows.length)} rows{rows.length < total ? ` · ${fmt(total)} in this version` : ""} · export holds every row</span><span>Empty cells (—) are values the run left blank, not zero</span></div>
    </>
  );
}

function Compare({ rows, version, onClose }: { rows: Json[]; version: number; onClose: () => void }) {
  const [hide, setHide] = useState(true);
  const kind = (r: Json) => (r.before == null ? "Added" : r.before !== r.final_dp ? "Changed" : "Unchanged");
  const counts = { Added: 0, Changed: 0, Unchanged: 0 } as Record<string, number>;
  rows.forEach((r) => counts[kind(r)]++);
  const shown = rows.filter((r) => !hide || kind(r) !== "Unchanged");
  return (
    <>
      <div className="card-head">
        <span className="row wrap"><span className="strong">Compare version 0 · pipeline original with version {version}</span>
          <span className="tag">Added · {counts.Added}</span><span className="tag ink">Changed · {counts.Changed}</span><span className="tag dashed">Unchanged · {counts.Unchanged}</span></span>
        <span className="row"><label className="check t-small"><input type="checkbox" checked={hide} onChange={(e) => setHide(e.target.checked)} />Hide unchanged</label>
          <Button size="sm" onClick={onClose}>Close compare</Button></span>
      </div>
      {shown.length === 0 ? <Empty text="No line differs from version 0." /> : (
        <div className="tblwrap" style={{ maxHeight: "calc(100vh - 330px)" }}>
          <table className="tbl compact">
            <thead><tr><th>Change</th><th>SKU code</th><th>Platform · city</th><th className="num">Version 0, units</th><th className="num">Version {version}, units</th><th>Why</th></tr></thead>
            <tbody>
              {shown.map((r) => {
                const k = kind(r);
                return (
                  <tr key={r.line_id} className={k === "Added" ? "sel" : undefined}>
                    <td><span className={`tag ${k === "Changed" ? "ink" : k === "Unchanged" ? "dashed" : ""}`}>{k}</span></td>
                    <td>{r.sku_code}</td>
                    <td className="muted">{[r.platform, r.city].filter(Boolean).join(" · ")}</td>
                    <td className="num" style={{ textDecoration: k === "Changed" ? "line-through" : undefined }}>{fmt(r.before)}</td>
                    <td className={`num ${k === "Unchanged" ? "" : "strong"}`}>{fmt(r.final_dp)}</td>
                    <td className="muted">{k === "Unchanged" ? "—" : r.user_change ? `${signed(r.user_change)} units · edited` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="card-foot line"><span>Showing {fmt(shown.length)} of {fmt(rows.length)} lines</span><span>Version 0 is the pipeline's original plan.</span></div>
    </>
  );
}

function WhyPanel({ r, onClose, onReasoning, onEdit }: { r: Json; onClose: () => void; onReasoning: () => void; onEdit: () => void }) {
  const effects: [string, string, number | null, number | null][] = [
    ["Trend", "trend", r.trend_m, r.trend_units], ["Sell-through", "sell_through", r.sell_through_m, r.sell_through_units],
    ["Fill rate", "fill_rate", r.fill_rate_m, r.fill_rate_units], ["Stock cover", "stock_cover", null, r.stock_cover_units],
    ["Judgment", "judgment", r.judgment_m, r.judgment_units],
  ];
  const binding = effects.reduce<string | null>((best, e) => (e[3] && (!best || Math.abs(e[3]) > Math.abs(effects.find((x) => x[1] === best)![3] ?? 0)) ? e[1] : best), null);
  type Step = { key: string; label: string; value: string | null };
  const steps: Step[] = [
    { key: "base", label: "Base demand, units", value: r.base == null ? null : fmt(r.base, 1) },
    ...effects.map(([label, key, m, u]) => ({ key, label, value: m == null && !u ? null : `${m != null ? `${pct(m)}% · ` : ""}${signed(u)} units` })),
    { key: "agent", label: "✦ Agent factors", value: r.agent_factors ?? null },
    { key: "reference", label: "Reference DP, units", value: r.reference_dp == null ? null : fmt(r.reference_dp) },
    { key: "pipeline", label: "Pipeline DP, units", value: r.pipeline_dp == null ? null : fmt(r.pipeline_dp) },
    { key: "change", label: "Your change, units", value: r.user_change ? signed(r.user_change) : "None" },
    { key: "final", label: "Final DP, units", value: fmt(r.final_dp) },
    { key: "order", label: "Order quantity, units", value: r.order_qty == null ? null : fmt(r.order_qty) },
  ];
  return (
    <Drawer label={`Why ${r.sku_code} in ${r.city}`} onClose={onClose}
      head={
        <div className="stack" style={{ gap: 2 }}>
          <span className="t-caption muted">{[r.sku_code, r.item_name, r.city, r.platform].filter(Boolean).join(" · ")}</span>
          <span className="row" style={{ gap: 8, alignItems: "baseline" }}><h2 className="metric">{fmt(r.order_qty ?? r.final_dp)} units</h2>
            <span className="tag"><ListOrdered size={12} aria-hidden="true" />{r.order_qty == null ? "Final DP" : "Order quantity"}</span>
            {r.confidence && <span className="tag soft">Confidence · {r.confidence}</span>}</span>
        </div>
      }
      foot={<><button type="button" className="linkbtn t-small" onClick={onReasoning}>Open the matching reasoning row</button><Button size="sm" icon={Pencil} onClick={onEdit}>Edit this line</Button></>}>
      <div className="stack" style={{ padding: 12, background: "var(--soft)", borderRadius: 8 }}>
        <span className="t-caption muted strong">What decided this number</span>
        <span className="t-lg">{r.reason || "No reason recorded by this run."}</span>
        {flagsOf(r).length > 0 && <span className="t-small"><span className="risk" aria-hidden="true">▲ </span>Flagged: {flagsOf(r).join(", ")}</span>}
      </div>
      <ol className="ladder">
        {steps.map((s, i) => (
          <li key={s.key} className={s.key === binding ? "binding" : undefined}>
            <span className="n">{i + 1}</span>
            <span className="stack" style={{ gap: 0 }}><span className={s.key === binding ? "strong" : undefined}>{s.label}</span>{s.key === binding && <span className="t-caption strong">Largest adjustment</span>}</span>
            {s.value == null ? <span className="t-small muted" style={{ fontStyle: "italic" }}>not exposed by this run</span> : <span className="strong" style={{ textAlign: "right", maxWidth: 260 }}>{s.value}</span>}
          </li>
        ))}
      </ol>
      <p className="t-small muted">Each value carries the unit the run reports. A step the run did not expose is named, never shown as blank or zero.</p>
    </Drawer>
  );
}

function ExportDialog({ runId, month, version, label, flagged, onReview, onClose }: {
  runId: string; month?: string; version: number; label: string; flagged: number; onReview: () => void; onClose: () => void; session: Json | null;
}) {
  const [kind, setKind] = useState<"plan" | "reasoning" | "csv">("plan");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ym = (month ?? "").slice(0, 7);
  const file = kind === "plan" ? `demand-plan-${ym}-v${version}.xlsx` : kind === "reasoning" ? `reasoning-${ym}-v${version}.xlsx` : `reasoning-${ym}-v${version}.csv`;
  const path = kind === "plan" ? "plan.xlsx" : kind === "reasoning" ? "reasoning.xlsx" : "reasoning.csv";
  const go = async () => {
    setBusy(true);
    setErr(null);
    try { await download(`/runs/${runId}/export/${path}`, file); onClose(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Dialog title="Export plan" onClose={onClose} width={520}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon={Download} busy={busy} onClick={go}>Export</Button></>}>
      <fieldset className="stack g8" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t-small muted" style={{ marginBottom: 6 }}>Format</legend>
        {([["plan", "Demand plan workbook", ".xlsx · the plan template, one sheet per section"], ["reasoning", "Reasoning workbook", ".xlsx · every line with its factors"], ["csv", "Reasoning CSV", ".csv · every line, one sheet"]] as const).map(([k, t, d]) => (
          <label key={k} className="optcard">
            <input type="radio" name="fmt" checked={kind === k} onChange={() => setKind(k)} style={{ marginTop: 3 }} />
            <span className="stack" style={{ gap: 0 }}><span className="strong">{t}</span><span className="t-small muted">{d}</span></span>
          </label>
        ))}
      </fieldset>
      <dl className="dl" style={{ margin: 0 }}>
        <dt>Version</dt><dd>{label}</dd>
        <dt>Lines</dt><dd>Every line of this version; filters on screen are not applied</dd>
        <dt>File name</dt><dd className="mono">{file}</dd>
      </dl>
      {flagged > 0 && (
        <Banner tone="warn">{fmt(flagged)} flagged lines are unreviewed. You can still export; their flags are written in the file.{" "}
          <button type="button" className="linkbtn" onClick={onReview}>Review them</button></Banner>
      )}
      {err && <Banner tone="crit" role="alert">{err}</Banner>}
      <p className="t-small muted">This file is a plan. Releasing or committing a PO happens in the source system.</p>
    </Dialog>
  );
}
