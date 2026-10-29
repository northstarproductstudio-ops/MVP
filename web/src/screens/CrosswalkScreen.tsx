// S-06: match each platform item ID to one of your SKU codes. Confirmed matches apply to every plan.
import { useEffect, useMemo, useState } from "react";
import { Download, TriangleAlert, Upload } from "lucide-react";
import { api, download, type Json } from "../api";
import { fmt } from "../components/common";
import { Banner, Button, Empty, ErrorBlank, Loading, NoResults, SearchField, SelectField, Toast } from "../components/ui";

type Filter = "all" | "unmatched" | "low" | "confirmed";
const METHOD: Record<string, string> = {
  ean: "EAN", manual: "Confirmed by you", auto: "Name + pack size", auto_nopack: "Name", review: "Name · needs review", unmatched: "No match found",
};

const keyOf = (r: Json) => `${r.platform}|${r.platform_item_id}`;
const candsOf = (r: Json): Json[] => (typeof r.candidates === "string" ? JSON.parse(r.candidates || "[]") : r.candidates ?? []);
const isConfirmed = (r: Json) => Boolean(r.confirmed_at) || ["ean", "manual"].includes(r.match_method);
const kindOf = (r: Json): Filter => (isConfirmed(r) ? "confirmed" : r.match_method === "unmatched" ? "unmatched" : r.match_method === "review" ? "low" : "all");

export function CrosswalkScreen({ onChanged }: { onChanged: () => void }) {
  const [rows, setRows] = useState<Json[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter | null>(null);
  const [platform, setPlatform] = useState("all");
  const [q, setQ] = useState("");
  const [sku, setSku] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api.crosswalk().then((c) => {
      const list: Json[] = c.rows ?? [];
      setRows(list);
      setSku(Object.fromEntries(list.map((r) => [keyOf(r), r.sku_code ?? candsOf(r)[0]?.sku_code ?? ""])));
      setChecked(Object.fromEntries(list.map((r) => [keyOf(r), !isConfirmed(r) && ["auto", "auto_nopack"].includes(r.match_method) && Boolean(r.sku_code)])));
      setFilter((f) => f ?? (list.some((r) => kindOf(r) === "unmatched") ? "unmatched" : list.some((r) => kindOf(r) === "low") ? "low" : "all"));
    }).catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const all = rows ?? [];
  const counts = useMemo(() => ({
    all: all.length,
    unmatched: all.filter((r) => kindOf(r) === "unmatched").length,
    low: all.filter((r) => kindOf(r) === "low").length,
    confirmed: all.filter((r) => kindOf(r) === "confirmed").length,
  }), [all]);
  const platforms = Array.from(new Set(all.map((r) => r.platform))).sort();
  const skus = useMemo(() => Array.from(new Set(all.flatMap((r) => [r.sku_code, ...candsOf(r).map((c) => c.sku_code)]).filter(Boolean))).sort(), [all]);
  const f = filter ?? "all";
  const shown = all.filter((r) => (f === "all" || kindOf(r) === f) && (platform === "all" || r.platform === platform)
    && (!q || `${r.platform_item_id} ${r.platform_name}`.toLowerCase().includes(q.toLowerCase())));
  const selected = shown.filter((r) => checked[keyOf(r)] && sku[keyOf(r)] && !isConfirmed(r));
  const needs = counts.unmatched + counts.low;
  const filterLabel = { all: "All", unmatched: "Unmatched", low: "Low score", confirmed: "Confirmed" }[f];

  const confirm = async (list: Json[]) => {
    setBusy(true);
    try {
      await api.confirmCrosswalk(list.map((r) => ({ platform: r.platform, platform_item_id: r.platform_item_id, sku_code: sku[keyOf(r)] })));
      setToast(`${list.length} line${list.length > 1 ? "s" : ""} confirmed. They apply to every plan from now on.`);
      load();
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="content">
      <div className="inner" style={{ gap: 14 }}>
        <div className="pagehead">
          <div className="stack"><h1>Crosswalk · {fmt(needs)} to review</h1><p className="muted">Match each platform item ID to one of your SKU codes. Confirmed matches are used in every plan from now on.</p></div>
          <span className="row">
            <Button size="sm" icon={Download} onClick={() => download("/crosswalk/draft.xlsx", "sku-crosswalk-draft.xlsx")}>Download template</Button>
            <label className="btn sm"><Upload size={16} aria-hidden="true" />Upload filled template
              <input type="file" hidden accept=".xlsx" onChange={async (e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                try { await api.uploadCrosswalk(file); setToast(`${file.name} applied. Rows marked confirm were saved.`); load(); onChanged(); } catch (x: any) { setError(x.message); }
              }} /></label>
          </span>
        </div>

        {error && rows && <Banner tone="crit" role="alert">{error}</Banner>}
        {!rows ? (
          <section className="card">{error ? <ErrorBlank text={`Matches didn’t load. Confirmed matches are safe. ${error}`} onAction={load} /> : <Loading text="Loading matches." rows={6} />}</section>
        ) : (
          <>
            <div className="row between wrap">
              <div role="radiogroup" aria-label="Show" className="seg">
                {(["all", "unmatched", "low", "confirmed"] as Filter[]).map((k) => (
                  <button key={k} type="button" role="radio" aria-checked={f === k} onClick={() => setFilter(k)}>
                    {{ all: "All", unmatched: "Unmatched", low: "Low score", confirmed: "Confirmed" }[k]} · {counts[k]}
                  </button>
                ))}
              </div>
              <span className="row">
                <SelectField size="sm" label="Platform" width={160} value={platform} onChange={setPlatform}
                  options={[{ value: "all", label: "All platforms" }, ...platforms.map((p) => ({ value: p, label: p }))]} />
                <SearchField value={q} onChange={setQ} placeholder="Item ID or name" width={220} />
              </span>
            </div>

            {selected.length > 0 && (
              <div role="region" aria-label="Bulk actions" className="bulkbar">
                <span>{selected.length} lines with a proposed SKU selected · filter: {filterLabel}</span>
                <span className="row">
                  <button type="button" className="btn sm on-ink" onClick={() => setChecked({})}>Clear selection</button>
                  <button type="button" className="btn sm on-ink fill" aria-disabled={busy || undefined} onClick={() => !busy && confirm(selected)}>Confirm {selected.length} lines</button>
                </span>
              </div>
            )}

            <section className="card">
              {all.length === 0 || (f !== "confirmed" && f !== "all" && counts[f] === 0 && platform === "all" && !q) ? (
                <Empty text={all.length ? "Every platform item is matched." : "No platform items yet. They appear after a sales file is mapped."}
                  action={all.length ? "View confirmed" : undefined} onAction={() => setFilter("confirmed")} />
              ) : shown.length === 0 ? (
                <NoResults count={`0 of ${counts[f]} rows`} text="No rows match these filters." action="Clear filters" onAction={() => { setQ(""); setPlatform("all"); }} />
              ) : (
                <div className="tblwrap">
                  <table className="tbl compact">
                    <thead><tr>
                      <th>Platform</th><th>Platform item ID</th><th>Platform name</th><th style={{ width: 180 }}>Proposed SKU code</th>
                      <th className="num">Score</th><th>Match method</th><th>Candidate 2</th><th>Candidate 3</th>
                      <th className="ctr"><label className="check"><input type="checkbox" aria-label="Confirm all shown lines that have a proposed SKU"
                        checked={shown.filter((r) => !isConfirmed(r) && sku[keyOf(r)]).every((r) => checked[keyOf(r)]) && shown.some((r) => !isConfirmed(r) && sku[keyOf(r)])}
                        onChange={(e) => setChecked((c) => ({ ...c, ...Object.fromEntries(shown.filter((r) => !isConfirmed(r) && sku[keyOf(r)]).map((r) => [keyOf(r), e.target.checked])) }))} />Confirm</label></th>
                    </tr></thead>
                    <tbody>
                      {shown.map((r) => {
                        const k = keyOf(r);
                        const cands = candsOf(r);
                        const done = isConfirmed(r);
                        const low = r.match_method === "review" && !done;
                        const missing = !sku[k];
                        return (
                          <tr key={k} className={low && !done ? "sel" : undefined} style={{ height: 44 }}>
                            <td>{r.platform}</td>
                            <td className="mono">{r.platform_item_id}</td>
                            <td className="ellipsis">{r.platform_name ?? "—"}</td>
                            <td>
                              <label className={`field block sm ${missing ? "bad" : ""}`}>
                                <span className="sr-only">SKU code for {r.platform_item_id}</span>
                                <input list="cw-skus" value={sku[k] ?? ""} placeholder="Choose a SKU" disabled={done}
                                  onChange={(e) => setSku((s) => ({ ...s, [k]: e.target.value }))} />
                              </label>
                            </td>
                            <td className="num"><span className="row end" style={{ gap: 6 }}>
                              {low && <span className="tag" style={{ borderColor: "var(--risk)" }}><TriangleAlert size={12} className="risk" aria-hidden="true" />Low</span>}
                              {r.score == null || r.match_method === "unmatched" ? "—" : fmt(r.score, 2)}</span></td>
                            <td className="muted">{done && r.match_method !== "ean" ? "Confirmed" : METHOD[r.match_method] ?? r.match_method}</td>
                            {[1, 2].map((i) => (
                              <td key={i}>{cands[i] ? <button type="button" className="linkbtn" disabled={done} onClick={() => setSku((s) => ({ ...s, [k]: cands[i].sku_code }))}>{cands[i].sku_code}</button> : <span className="muted">—</span>}</td>
                            ))}
                            <td className="ctr">
                              <input type="checkbox" aria-label={`Confirm ${r.platform_item_id}`} checked={done || Boolean(checked[k])} disabled={done || missing}
                                title={missing ? "Choose a SKU code before confirming." : done ? "Already confirmed." : undefined}
                                onChange={(e) => setChecked((c) => ({ ...c, [k]: e.target.checked }))} />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="card-foot line"><span>Showing {shown.length} of {counts[f]} {filterLabel.toLowerCase()} rows · {counts.all} in all</span><span>Click a candidate to make it the proposed SKU</span></div>
            </section>
            <Banner tone="info">Prefer a spreadsheet? The template's confirm column reads: <span className="strong">"enter yes, y, true or 1 to confirm a row; anything else leaves it unconfirmed."</span></Banner>
          </>
        )}
        <datalist id="cw-skus">{skus.map((s) => <option key={s} value={s} />)}</datalist>
      </div>
      {toast && <Toast onDone={() => setToast(null)}>{toast}</Toast>}
    </main>
  );
}
