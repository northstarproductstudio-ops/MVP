// MVP-05/09: mapping saved — what is now available, warnings, crosswalk review notice.
import { useEffect, useState } from "react";
import { api, download, type Json } from "../api";
import { Chip, fmt } from "../components/common";

export function SavedScreen({ summaries, datasets, onAsk, onRunPipeline, onBack }: {
  summaries: Json[];
  datasets: Json[];
  onAsk: () => void;
  onRunPipeline: () => void;
  onBack: () => void;
}) {
  const [cw, setCw] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = datasets.filter((d) => d.status === "ready").length;
  const pending = datasets.filter((d) => d.status === "mapped").length;

  const loadCw = () => api.crosswalk("review").then(setCw).catch(() => setCw(null));
  useEffect(() => {
    loadCw();
  }, [ready]);

  const stored = summaries.reduce((s, x) => s + x.variables_stored, 0);
  const custom = summaries.reduce((s, x) => s + x.custom_mappings, 0);
  const notStored = summaries.flatMap((x) => x.fields_not_stored);
  const newCats = Array.from(new Set(summaries.flatMap((x) => x.new_categories)));
  const byCat = new Map<string, Set<string>>();
  summaries.forEach((s) => s.available.forEach((a: Json) => {
    const set = byCat.get(a.category) ?? new Set();
    a.variables.forEach((v: string) => set.add(v));
    byCat.set(a.category, set);
  }));
  const warnings = datasets.flatMap((d) => (d.warnings ?? []).map((w: string) => `${d.file_name}: ${w}`));
  const review = (cw?.rows ?? []).filter((r: Json) => r.match_method === "review");

  const confirmAll = async () => {
    setBusy(true);
    await api.confirmCrosswalk(review.map((r: Json) => ({ platform: r.platform, platform_item_id: r.platform_item_id, sku_code: r.sku_code })));
    await loadCw();
    setBusy(false);
  };

  return (
    <div className="col">
      <div>
        <h1>Mapping saved</h1>
        <p className="sub">{datasets.length} files · {fmt(datasets.reduce((s, d) => s + (d.rows_read || 0), 0))} rows · {ready} ready{pending ? `, ${pending} still being stored` : ""}</p>
      </div>
      <div className="kpis">
        <div className="kpi"><div className="k">Variables stored</div><div className="v">{stored}</div></div>
        <div className="kpi"><div className="k">Categories</div><div className="v">{byCat.size} {newCats.length > 0 && <Chip tone="new">{newCats.length}</Chip>}</div></div>
        <div className="kpi"><div className="k">Custom mappings</div><div className="v">{custom}</div></div>
        <div className="kpi"><div className="k">Fields not stored</div><div className="v">{notStored.length}</div></div>
      </div>
      <div className="card pad">
        <h2>What is now available</h2>
        <table className="grid" style={{ marginTop: 8 }}>
          <tbody>
            {Array.from(byCat.entries()).sort().map(([cat, vars]) => (
              <tr key={cat}><td style={{ width: 200 }}><strong>{cat}</strong> {newCats.includes(cat) && <Chip tone="new">{""}</Chip>}</td><td className="muted">{Array.from(vars).sort().join(" · ")}</td></tr>
            ))}
            <tr><td><strong>Not stored</strong></td><td className="muted">{notStored.slice(0, 40).join(" · ")}{notStored.length > 40 ? ` … (${notStored.length})` : ""}</td></tr>
          </tbody>
        </table>
      </div>
      {warnings.length > 0 && (
        <div className="card pad">
          <h2>Fixes applied while reading</h2>
          <ul className="muted">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      {review.length > 0 && (
        <div className="card pad">
          <div className="row between">
            <div>
              <h2>SKU crosswalk needs review</h2>
              <p className="muted" style={{ margin: "4px 0 0" }}>{review.length} platform items were matched to a SKU by name with medium confidence. A plan cannot run until SKUs with sell-in are confirmed.</p>
            </div>
            <div className="row">
              <button className="btn" onClick={() => download("/crosswalk/draft.xlsx", "sku-crosswalk-draft.xlsx")}>Download draft</button>
              <label className="btn">Upload reviewed<input type="file" hidden accept=".xlsx" onChange={async (e) => { if (e.target.files?.[0]) { await api.uploadCrosswalk(e.target.files[0]); loadCw(); } }} /></label>
              <button className="btn primary" onClick={confirmAll} disabled={busy}>Confirm all proposals</button>
            </div>
          </div>
          <div className="scroll" style={{ marginTop: 12, maxHeight: 300 }}>
            <table className="grid">
              <thead><tr><th>Platform</th><th>Platform item</th><th>Proposed SKU</th><th className="num">Score</th><th>Other candidates</th></tr></thead>
              <tbody>
                {review.map((r: Json) => (
                  <tr key={r.platform + r.platform_item_id}>
                    <td>{r.platform}</td><td>{r.platform_name}</td><td><strong>{r.sku_code}</strong></td>
                    <td className="num">{fmt(r.score, 2)}</td>
                    <td className="muted">{(r.candidates ?? []).slice(1).map((c: Json) => c.sku_code).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="card pad">
        <div className="row between">
          <div><h2>Ready to ask</h2><p className="muted" style={{ margin: "4px 0 0" }}>Ask about the stored data, or ask for a demand plan.</p></div>
          <div className="row">
            <button className="btn ghost" onClick={onBack}>Back to mapping</button>
            <button className="btn" onClick={onRunPipeline} disabled={!ready}>Run the pipeline</button>
            <button className="btn primary" onClick={onAsk} disabled={!ready}>Go to Ask</button>
          </div>
        </div>
      </div>
    </div>
  );
}
