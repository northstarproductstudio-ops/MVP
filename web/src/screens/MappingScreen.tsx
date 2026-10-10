// MVP-04: confirm how fields are stored (proposed mapping, custom mappings, save).
import { useEffect, useMemo, useState } from "react";
import { api, type Json } from "../api";
import { Chip, fmt, STATE_LABEL } from "../components/common";

type Row = Json & { did: string; file: string; key: string; selected?: boolean };

export function MappingScreen({ datasets, onSaved }: { datasets: Json[]; onSaved: (summaries: Json[]) => void }) {
  const parsed = datasets.filter((d) => d.status === "parsed");
  const [maps, setMaps] = useState<Record<string, Json>>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [tab, setTab] = useState<string>("all");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [custom, setCustom] = useState({ did: "", field: "", variable: "", category: "" });
  const [bulkCat, setBulkCat] = useState("");

  useEffect(() => {
    (async () => {
      const out: Record<string, Json> = {};
      const all: Row[] = [];
      for (const d of parsed) {
        const m = await api.mapping(d.id);
        out[d.id] = m;
        for (const f of m.proposal.fields) {
          all.push({ ...f, did: d.id, file: d.file_name, key: `${d.id}|${f.sheet}|${f.source_field}` });
        }
      }
      setMaps(out);
      setRows(all);
      if (parsed[0]) setCustom((c) => ({ ...c, did: parsed[0].id }));
    })().catch((e) => setError(e.message));
  }, [parsed.map((d) => d.id).join()]); // eslint-disable-line

  const any = Object.values(maps)[0];
  const variables: Json[] = any?.variables ?? [];
  const categories: string[] = any?.categories ?? [];
  const varCat = useMemo(() => Object.fromEntries(variables.map((v) => [v.name, v.category])), [variables]);

  const shown = rows.filter((r) => tab === "all" || r.did === tab);
  const counts = useMemo(() => {
    const c = { mapped: 0, unmapped: 0, newcat: 0, custom: 0 };
    rows.forEach((r) => {
      if (r.state === "needs_decision") c.unmapped++;
      else if (r.state !== "not_stored") c.mapped++;
      if (r.state === "new_category") c.newcat++;
      if (r.state === "custom") c.custom++;
    });
    return c;
  }, [rows]);
  const totalRows = parsed.reduce((s, d) => s + (d.rows_read || 0), 0);

  const update = (key: string, patch: Json) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const dupes = useMemo(() => {
    const seen = new Map<string, number>();
    rows.forEach((r) => {
      if (r.variable && r.state !== "not_stored" && varCat[r.variable] && r.tier !== 2) {
        const k = `${r.did}|${r.sheet}|${r.variable}`;
        seen.set(k, (seen.get(k) || 0) + 1);
      }
    });
    return seen;
  }, [rows, varCat]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const summaries = [];
      for (const d of parsed) {
        const fields = rows.filter((r) => r.did === d.id).map((r) => ({
          sheet: r.sheet, source_field: r.source_field,
          variable: r.state === "not_stored" ? null : r.variable,
          category: r.category ?? varCat[r.variable] ?? null,
          state: r.state === "needs_decision" ? "not_stored" : r.state,
          new_variable: r.new_variable ?? null,
        }));
        summaries.push({ file_name: d.file_name, ...(await api.saveMapping(d.id, fields)) });
      }
      onSaved(summaries);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const addCustom = () => {
    const r = rows.find((x) => x.did === custom.did && x.source_field === custom.field);
    if (!r || !custom.variable) return;
    update(r.key, { variable: custom.variable, category: custom.category || varCat[custom.variable] || "Other",
      state: "custom", new_variable: varCat[custom.variable] ? null : { name: custom.variable, category: custom.category || "Other", data_type: r.inferred_type || "text" } });
    setCustom({ ...custom, field: "", variable: "", category: "" });
  };

  if (!parsed.length) {
    return <div className="col"><h1>Nothing to map yet</h1><p className="sub">Files appear here once they have been read.</p></div>;
  }
  const selected = rows.filter((r) => r.selected);

  return (
    <div className="col wide">
      <div className="row between wrap">
        <div>
          <h1>Confirm how these fields are stored</h1>
          <p className="sub">{parsed.length} files · {fmt(totalRows)} rows · {rows.length} fields</p>
        </div>
        <div className="row wrap">
          <Chip tone="ok">{counts.mapped} mapped</Chip>
          <Chip tone="warn">{counts.unmapped} still unmapped</Chip>
          <Chip tone="new">{counts.newcat} new category</Chip>
          <Chip tone="info">{counts.custom} custom</Chip>
        </div>
      </div>

      <div className="card pad">
        <h2>How this mapping was proposed</h2>
        <p className="muted" style={{ margin: "6px 0 0" }}>
          Each file's type is decided from its columns (the file name is only a hint). Fields the pipeline needs are matched
          to plan variables; other useful columns are proposed as conversation variables; anything the agent could not
          place is left as <em>Needs a decision</em> — it never invents a value. Saved mappings are reused for the next file of the same type.
        </p>
        {parsed.map((d) => {
          const p = maps[d.id]?.proposal;
          if (!p?.ask_user && !(p?.warnings ?? []).length) return null;
          return (
            <div key={d.id} className="notice" style={{ marginTop: 12 }}>
              <strong>{d.file_name}:</strong> {p.question}
              {(p.warnings ?? []).map((w: string) => <div key={w}>{w}</div>)}
              <div className="row" style={{ marginTop: 8 }}>
                <span>File type</span>
                <select className="inline" value={p.file_type} onChange={async (e) => { await api.setType(d.id, e.target.value); window.location.reload(); }}>
                  {(maps[d.id]?.file_types ?? []).map((t: Json) => <option key={t.key} value={t.key}>{t.name}</option>)}
                </select>
              </div>
            </div>
          );
        })}
      </div>

      <div className="card pad">
        <div className="row between"><h2>Add a custom mapping</h2></div>
        <div className="row wrap" style={{ marginTop: 12 }}>
          <select className="inline" value={custom.did} onChange={(e) => setCustom({ ...custom, did: e.target.value, field: "" })}>
            {parsed.map((d) => <option key={d.id} value={d.id}>{d.file_name}</option>)}
          </select>
          <select className="inline" value={custom.field} onChange={(e) => setCustom({ ...custom, field: e.target.value })}>
            <option value="">Field in file…</option>
            {rows.filter((r) => r.did === custom.did).map((r) => <option key={r.key} value={r.source_field}>{r.source_field}</option>)}
          </select>
          <input className="inline" placeholder="Mapped variable" list="vars" value={custom.variable} onChange={(e) => setCustom({ ...custom, variable: e.target.value })} />
          <input className="inline" placeholder="Category" list="cats" value={custom.category} onChange={(e) => setCustom({ ...custom, category: e.target.value })} />
          <button className="btn" onClick={addCustom} disabled={!custom.field || !custom.variable}>Add mapping</button>
        </div>
        {custom.variable && !varCat[custom.variable] && <p className="muted">No variable named {custom.variable} exists yet; it will be created as a conversation variable.</p>}
        <datalist id="vars">{variables.map((v) => <option key={v.name} value={v.name} />)}</datalist>
        <datalist id="cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      </div>

      <div className="card">
        <div className="tabs">
          <button className={`tab ${tab === "all" ? "active" : ""}`} onClick={() => setTab("all")}>All files <span className="muted">{rows.length}</span></button>
          {parsed.map((d) => (
            <button key={d.id} className={`tab ${tab === d.id ? "active" : ""}`} onClick={() => setTab(d.id)}>
              {d.file_name} <span className="muted">{rows.filter((r) => r.did === d.id).length}</span>
            </button>
          ))}
        </div>
        <div className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)" }}>
          <span className="muted">{selected.length} selected</span>
          <input className="inline" placeholder="Set category" list="cats" value={bulkCat} onChange={(e) => setBulkCat(e.target.value)} />
          <button className="btn small" disabled={!selected.length || !bulkCat} onClick={() => selected.forEach((r) => update(r.key, { category: bulkCat, selected: false }))}>Apply to {selected.length}</button>
          <button className="btn small" disabled={!selected.length} onClick={() => selected.forEach((r) => update(r.key, { state: "not_stored", variable: null, selected: false }))}>Exclude {selected.length}</button>
        </div>
        <div className="scroll">
          <table className="grid">
            <thead><tr><th></th><th>Field in file</th><th>Sample values</th><th>Mapped variable</th><th>Category</th><th>State</th></tr></thead>
            <tbody>
              {shown.map((r) => {
                const [label, tone] = STATE_LABEL[r.state] ?? [r.state, undefined];
                const dup = r.variable && (dupes.get(`${r.did}|${r.sheet}|${r.variable}`) || 0) > 1;
                return (
                  <tr key={r.key}>
                    <td><input type="checkbox" checked={!!r.selected} onChange={(e) => update(r.key, { selected: e.target.checked })} /></td>
                    <td><strong>{r.source_field}</strong><div className="muted">{r.inferred_type} · {r.file}{r.sheet !== "csv" ? ` · ${r.sheet}` : ""}</div></td>
                    <td><div className="samples">{(r.sample_values ?? []).join(" · ")}</div>
                      {r.blank_pct > 0.1 && <div className="muted">{Math.round(r.blank_pct * 100)}% of rows are blank here</div>}</td>
                    <td>
                      <input className="inline" list="vars" value={r.variable ?? ""} placeholder="No variable"
                        onChange={(e) => update(r.key, { variable: e.target.value || null, state: e.target.value ? (varCat[e.target.value] ? "proposed" : "custom") : "needs_decision",
                          category: varCat[e.target.value] ?? r.category })} />
                      {dup && <div className="error">another field already maps here</div>}
                    </td>
                    <td><input className="inline" list="cats" value={r.category ?? ""} placeholder="No category" onChange={(e) => update(r.key, { category: e.target.value })} /></td>
                    <td><Chip tone={tone}>{label}</Chip></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="footer">
          <span className="muted">Showing {shown.length} of {rows.length} fields. Saving stores every mapping exactly as shown; the {counts.unmapped} unmapped fields will not be stored.</span>
          <div className="row">
            {error && <span className="error">{error}</span>}
            <button className="btn primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Confirm and save"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
