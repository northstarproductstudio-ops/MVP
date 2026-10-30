// S-04: confirm how each file's fields are stored. One file at a time; saving stores every file.
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowLeftRight, ArrowRight, ChevronDown, Database, FileSpreadsheet, FileUp, OctagonX, RotateCcw, TriangleAlert } from "lucide-react";
import { api, type Json } from "../api";
import { DATASET_STATUS, fmt, STATE_LABEL } from "../components/common";
import { Banner, Button, ErrorBlank, Loading, NoResults, Progress, SearchField, SelectField, StateChip } from "../components/ui";

type Row = Json & { did: string; file: string; key: string; n: number; orig: Json };

const MAPPABLE = ["parsed", "mapped", "ready"];

export function MappingScreen({ datasets, onSaved, onCrosswalk, onImport, onSavedData, onRefresh }: {
  datasets: Json[]; onSaved: () => Promise<void> | void; onCrosswalk: () => void;
  onImport: () => void; onSavedData: () => void; onRefresh: () => Promise<void>;
}) {
  const parsed = datasets.filter((d) => MAPPABLE.includes(d.status));
  const ids = parsed.map((d) => d.id).join();
  const [maps, setMaps] = useState<Record<string, Json>>({});
  const [rows, setRows] = useState<Row[] | null>(null);
  const [file, setFile] = useState<string>("");
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [jump, setJump] = useState<string | null>(null);

  // After the footer link picks a field, bring its row into view once that file has rendered.
  useEffect(() => {
    if (!jump) return;
    const tr = document.querySelector<HTMLElement>(`tr[data-key="${CSS.escape(jump)}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center", behavior: "smooth" });
    tr.querySelector<HTMLInputElement>("input")?.focus({ preventScroll: true });
    setJump(null);
  }, [jump, file, rows]);

  const load = () => {
    setLoadErr(null);
    (async () => {
      const out: Record<string, Json> = {};
      const all: Row[] = [];
      for (const d of parsed) {
        const m = await api.mapping(d.id);
        out[d.id] = m;
        m.proposal.fields.forEach((f: Json, i: number) => {
          all.push({ ...f, did: d.id, file: d.file_name, key: `${d.id}|${f.sheet}|${f.source_field}`, n: i + 1, orig: { ...f } });
        });
      }
      setMaps(out);
      setRows(all);
      setFile((cur) => cur || parsed[0]?.id || datasets[0]?.id || "");
    })().catch((e) => setLoadErr(e.message));
  };
  useEffect(load, [ids]); // eslint-disable-line

  const any = Object.values(maps)[0];
  const variables: Json[] = any?.variables ?? [];
  const categories: string[] = any?.categories ?? [];
  const varInfo = useMemo(() => Object.fromEntries(variables.map((v) => [v.name, v])), [variables]);

  const live = (r: Row) => r.state !== "not_stored" && r.variable;
  const conflicts = useMemo(() => {
    const by = new Map<string, Row[]>();
    (rows ?? []).forEach((r) => {
      if (live(r) && varInfo[r.variable]?.tier === 1) {
        const k = `${r.did}|${r.sheet}|${r.variable}`;
        by.set(k, [...(by.get(k) ?? []), r]);
      }
    });
    const out = new Map<string, Row[]>();
    by.forEach((list) => { if (list.length > 1) list.forEach((r) => out.set(r.key, list.filter((x) => x.key !== r.key))); });
    return out;
  }, [rows, varInfo]);

  const update = (key: string, patch: Json) => {
    setDirty(true);
    setRows((rs) => (rs ?? []).map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  if (!datasets.length) {
    return (
      <main className="content"><div className="inner"><h1>Map fields</h1>
        <div className="card">
          <div className="blank">
            <span>No files in this chat yet. Add the platform exports you want to plan from.</span>
            <span className="t-small muted">Files imported in other chats are listed in Saved data.</span>
            <span className="row"><Button variant="primary" size="sm" icon={FileUp} onClick={onImport}>Go to Import</Button>
              <Button size="sm" icon={Database} onClick={onSavedData}>Open Saved data</Button></span>
          </div>
        </div>
      </div></main>
    );
  }

  const cur = datasets.find((d) => d.id === file) ?? parsed[0] ?? datasets[0];
  const picker = <FilePicker files={datasets} current={cur} onPick={(id) => { setFile(id); setSel(null); setQ(""); }} />;
  if (!MAPPABLE.includes(cur.status)) {
    return <main className="content"><div className="inner"><h1>Map fields</h1><div className="row">{picker}</div>
      <FileState d={cur} onImport={onImport} onRefresh={onRefresh} /></div></main>;
  }
  if (loadErr) return <main className="content"><div className="inner"><h1>Map fields</h1><div className="row">{picker}</div><div className="card"><ErrorBlank text={`The mapping for ${cur.file_name} didn’t load. Your file and any saved mapping are safe. ${loadErr}`} onAction={load} details={loadErr} /></div></div></main>;
  if (!rows) return <main className="content"><div className="inner"><h1>Map fields</h1><div className="row">{picker}</div><div className="card"><Loading text={`Loading fields for ${cur.file_name}.`} rows={6} /></div></div></main>;

  const mine = rows.filter((r) => r.did === cur.id);
  const shown = mine.filter((r) => !q || `${r.source_field} ${r.variable ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  const stateOf = (r: Row) => (conflicts.has(r.key) ? "conflict" : r.state);
  const count = (list: Row[], s: string) => list.filter((r) => stateOf(r) === s || (s === "confirmed" && stateOf(r) === "recognised") || (s === "proposed" && stateOf(r) === "new_category")).length;
  const allNeeds = rows.filter((r) => stateOf(r) === "needs_decision").length;
  const allConf = new Set(rows.filter((r) => conflicts.has(r.key)).map((r) => `${r.did}|${r.sheet}|${r.variable}`)).size;
  const selected = mine.find((r) => r.key === sel) ?? mine[0];
  const p = maps[cur.id]?.proposal;

  // Fields blocking the save, in file then row order. The footer link walks them from the
  // current file onwards, one per click.
  const issues = rows.filter((r) => ["needs_decision", "conflict"].includes(stateOf(r)));
  const order = parsed.map((d) => d.id);
  const goToIssue = () => {
    if (!issues.length) return;
    const at = issues.findIndex((r) => r.key === sel);
    const next = at >= 0 ? issues[(at + 1) % issues.length]
      : issues.find((r) => order.indexOf(r.did) >= order.indexOf(cur.id)) ?? issues[0];
    setFile(next.did);
    setSel(next.key);
    setQ("");
    setJump(next.key);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      for (const d of parsed) {
        const fields = rows.filter((r) => r.did === d.id).map((r) => ({
          sheet: r.sheet, source_field: r.source_field,
          variable: r.state === "not_stored" ? null : r.variable,
          category: r.category ?? varInfo[r.variable]?.category ?? null,
          state: r.state, new_variable: r.new_variable ?? null,
        }));
        await api.saveMapping(d.id, fields);
      }
      setDirty(false);
      await onSaved();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const reasons = [allConf ? `${allConf} conflict${allConf > 1 ? "s" : ""}` : "", allNeeds ? `${allNeeds} field${allNeeds > 1 ? "s" : ""} that need${allNeeds > 1 ? "" : "s"} a decision` : ""].filter(Boolean);
  const blockReason = reasons.length ? `Resolve ${reasons.join(" and ")} to continue.` : "";

  return (
    <>
      <main className="content" style={{ paddingTop: 20 }}>
        <div className="inner" style={{ gap: 14 }}>
          <div className="pagehead">
            <div className="stack">
              <h1>Map fields</h1>
              <div className="row wrap muted" style={{ gap: 12 }}>
                {picker}
                <span>{count(mine, "needs_decision")} needs a decision · {new Set(mine.filter((r) => conflicts.has(r.key)).map((r) => `${r.sheet}|${r.variable}`)).size} conflict · {count(mine, "proposed")} proposed · {count(mine, "confirmed")} confirmed · {count(mine, "custom")} custom · {count(mine, "not_stored")} ignored</span>
              </div>
            </div>
            <div className="row">
              {dirty && <span className="t-small muted">Unsaved changes</span>}
              <Button disabled={!mine.some((r) => ["proposed", "new_category"].includes(stateOf(r)))} reason="No proposed fields in this file."
                onClick={() => mine.forEach((r) => ["proposed", "new_category"].includes(stateOf(r)) && update(r.key, { state: "confirmed" }))}>Confirm all proposed</Button>
              <Button variant="primary" busy={saving} disabled={Boolean(blockReason)} reason={blockReason} onClick={save}>Save mapping</Button>
            </div>
          </div>

          {(p?.ask_user || (p?.warnings ?? []).length > 0) && (
            <Banner tone="warn">
              <div className="stack g8">
                {p.question && <span className="strong">{p.question}</span>}
                {(p.warnings ?? []).map((w: string) => <span key={w}>{w}</span>)}
                <span className="row">
                  <span>File type</span>
                  <SelectField size="sm" label="File type" width={220} value={p.file_type}
                    options={(maps[cur.id]?.file_types ?? []).map((t: Json) => ({ value: t.key, label: t.name }))}
                    onChange={async (v) => { await api.setType(cur.id, v); window.location.reload(); }} />
                </span>
              </div>
            </Banner>
          )}
          {error && <Banner tone="crit" role="alert">{error}</Banner>}

          <div className="grid12">
            <section className="card span9">
              <div className="card-head" style={{ padding: "8px 12px 8px 16px" }}>
                <span className="t-small muted">Each file's type is decided from its columns. Fields the agent could not place need a decision — it never invents a value.</span>
                <SearchField value={q} onChange={setQ} placeholder="Find a field" width={200} />
              </div>
              {shown.length === 0 ? (
                <NoResults count={`0 of ${mine.length} fields`} text="No field names match." action="Clear search" onAction={() => setQ("")} />
              ) : (
                <div className="tblwrap">
                  <table className="tbl top dense">
                    <thead><tr>
                      <th style={{ width: 130 }}>Source field</th><th>Sample values</th><th style={{ width: 180 }}>Mapped variable</th>
                      <th style={{ width: 64 }}>Type</th><th className="ctr" style={{ width: 150 }}>State</th><th style={{ width: 200 }}>Actions</th>
                    </tr></thead>
                    <tbody>
                      {shown.map((r) => (
                        <MapRow key={r.key} r={r} state={stateOf(r)} others={conflicts.get(r.key) ?? []} selected={selected?.key === r.key}
                          onSelect={() => setSel(r.key)} update={update} varInfo={varInfo} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="card-foot line"><span>Showing {shown.length} of {mine.length} fields · ignored fields stay listed</span><span>{fmt(cur.rows_read)} rows in this file</span></div>
            </section>

            {selected && (
              <aside aria-label={`Preview of ${selected.source_field}`} className="card span3">
                <div className="card-head" style={{ flexDirection: "column", alignItems: "stretch", gap: 2 }}>
                  <span className="t-caption muted">Preview · row {selected.n}</span>
                  <span className="h-section">{selected.source_field}</span>
                  <span className="t-small muted">{selected.inferred_type || "Text"} · {(selected.sample_values ?? []).length} sample values · {Math.round((selected.blank_pct ?? 0) * 100)}% empty</span>
                </div>
                <table className="tbl compact">
                  <thead><tr><th className="num" style={{ width: 48 }}>Row</th><th>Value</th></tr></thead>
                  <tbody>
                    {(selected.sample_values ?? []).slice(0, 7).map((v: any, i: number) => (
                      <tr key={i}><td className="num muted">{i + 1}</td><td className={v === null || v === "" ? "muted" : undefined}>{v === null || v === "" ? "—" : String(v)}</td></tr>
                    ))}
                  </tbody>
                </table>
                <div className="stack g8" style={{ padding: 16, borderTop: "1px solid var(--line)" }}>
                  <label className="stack"><span className="t-small muted">Category</span>
                    <span className="field block sm"><input list="cats" value={selected.category ?? varInfo[selected.variable]?.category ?? ""} placeholder="No category"
                      onChange={(e) => update(selected.key, { category: e.target.value })} /></span></label>
                  {selected.variable && !varInfo[selected.variable] && selected.state !== "not_stored" && (
                    <span className="t-small muted">No variable named {selected.variable} exists yet; saving creates it as a conversation variable.</span>
                  )}
                </div>
                <div className="card-foot line"><span>Showing the first {Math.min(7, (selected.sample_values ?? []).length)} sample values of {fmt(cur.rows_read)} rows</span></div>
              </aside>
            )}
          </div>
          <Banner tone="info">Platform item IDs that don't match a SKU code are reviewed in the <button type="button" className="linkbtn" onClick={onCrosswalk}>Crosswalk</button> after saving. <ArrowLeftRight size={12} aria-hidden="true" /></Banner>
          <datalist id="vars">{variables.map((v) => <option key={v.name} value={v.name}>{v.category}</option>)}</datalist>
          <datalist id="cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
        </div>
      </main>
      <footer className="footbar end">
        {blockReason && <button type="button" id="cont-reason" className="linkbtn" onClick={goToIssue}
          title={issues.length > 1 ? "Go to the next field that needs a decision" : "Go to the field that needs a decision"}>{blockReason}</button>}
        <Button variant="primary" iconRight={ArrowRight} busy={saving} disabled={Boolean(blockReason)} describedBy={blockReason ? "cont-reason" : undefined} onClick={save}>Save and continue to plan</Button>
      </footer>
    </>
  );
}

function MapRow({ r, state, others, selected, onSelect, update, varInfo }: {
  r: Row; state: string; others: Row[]; selected: boolean; onSelect: () => void;
  update: (key: string, patch: Json) => void; varInfo: Record<string, Json>;
}) {
  const ignored = state === "not_stored";
  const conflict = state === "conflict";
  const [label, tone] = conflict ? ["Conflict", "crit" as const] : STATE_LABEL[state] ?? [state, "neutral" as const];
  const proposed = state === "proposed" || state === "new_category";
  const bg = conflict ? "color-mix(in srgb, var(--risk-tint) 60%, var(--surface))" : selected ? "var(--soft)" : undefined;
  const other = others[0];
  const ignore = () => update(r.key, { state: "not_stored" });
  return (
    <tr data-key={r.key} onClick={onSelect} className="clickable" style={{ background: bg }} aria-selected={selected}>
      <td className="strong" style={{ whiteSpace: "nowrap" }}>
        <span className="row" style={{ gap: 6 }}>{selected && <span className="selmark" aria-hidden="true" />}
          <span style={{ textDecoration: ignored ? "line-through" : undefined, color: ignored ? "var(--text-secondary)" : undefined }}>{r.source_field}</span></span>
        <span className="t-caption muted" style={{ display: "block", fontWeight: 400 }}>Row {r.n}{r.sheet && r.sheet !== "csv" ? ` · ${r.sheet}` : ""}</span>
      </td>
      <td className="muted ellipsis" style={{ maxWidth: 140 }}>{(r.sample_values ?? []).filter((v: any) => v !== null && v !== "").slice(0, 3).join(", ") || "—"}</td>
      <td style={{ paddingTop: 6 }} onClick={(e) => e.stopPropagation()}>
        {ignored ? <span className="muted" style={{ display: "inline-block", paddingTop: 6 }}>Not used</span> : (
          <label className={`field block ${conflict || state === "needs_decision" ? "bad" : proposed ? "dashed" : ""}`}>
            <span className="sr-only">Mapped variable for {r.source_field}</span>
            <input list="vars" value={r.variable ?? ""} placeholder="Choose a variable" onFocus={onSelect}
              onChange={(e) => {
                const v = e.target.value;
                update(r.key, {
                  variable: v || null, category: varInfo[v]?.category ?? r.category,
                  state: !v ? "needs_decision" : varInfo[v] ? (v === r.orig.variable && r.orig.state !== "needs_decision" ? r.orig.state : "confirmed") : "custom",
                  new_variable: v && !varInfo[v] ? { name: v, category: r.category || "Other", data_type: r.inferred_type || "text" } : null,
                });
              }} />
          </label>
        )}
      </td>
      <td>{ignored ? "—" : r.inferred_type || "Text"}</td>
      <td className="ctr"><StateChip tone={tone}>{label}</StateChip></td>
      <td className="t-small" style={{ paddingTop: 6 }} onClick={(e) => e.stopPropagation()}>
        {conflict && other && <span role="alert" style={{ display: "block", padding: "4px 0 6px" }}>{other.source_field} in row {other.n} is also set to {r.variable}.</span>}
        <span className="row wrap" style={{ gap: 6, minHeight: 32 }}>
          {conflict && other && <>
            <Button size="sm" onClick={() => others.forEach((o) => update(o.key, { variable: null, state: "needs_decision" }))}>Keep it here</Button>
            <Button size="sm" onClick={() => update(r.key, { variable: null, state: "needs_decision" })}>Keep the other</Button>
          </>}
          {!conflict && proposed && <Button size="sm" onClick={() => update(r.key, { state: "confirmed" })}>Confirm</Button>}
          {ignored
            ? <button type="button" className="linkbtn" onClick={() => update(r.key, { state: r.orig.state === "not_stored" ? (r.orig.variable ? "proposed" : "needs_decision") : r.orig.state, variable: r.orig.variable })}>Restore</button>
            : <button type="button" className="linkbtn" onClick={ignore}>Ignore field</button>}
        </span>
      </td>
    </tr>
  );
}

// The S-04 file switcher: every file in the chat, with its status, one at a time.
function FilePicker({ files, current, onPick }: { files: Json[]; current: Json; onPick: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const idx = files.findIndex((d) => d.id === current.id);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button type="button" className="btn sm" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)} style={{ background: "var(--surface)" }}>
        <FileSpreadsheet size={14} aria-hidden="true" />{current.file_name} · file {idx + 1} of {files.length}<ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className="popover" role="listbox" aria-label="Files in this chat" style={{ top: 34, left: 0, width: 520 }}>
          <span className="t-caption muted" style={{ padding: "4px 8px" }}>Files in this chat · {files.length}</span>
          {files.map((d) => {
            const [label, tone] = DATASET_STATUS[d.status] ?? [d.status, "neutral"];
            return (
              <button key={d.id} type="button" role="option" aria-selected={d.id === current.id} className="menuitem"
                style={d.id === current.id ? { background: "var(--soft)" } : undefined} onClick={() => { onPick(d.id); setOpen(false); }}>
                <FileSpreadsheet size={14} aria-hidden="true" className="muted" />
                <span className="stack grow" style={{ gap: 0 }}>
                  <span className={d.id === current.id ? "strong" : undefined} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.file_name}</span>
                  <span className="t-caption muted">{d.rows_read != null ? `${fmt(d.rows_read)} rows` : "Not read yet"}</span>
                </span>
                <StateChip tone={tone}>{label}</StateChip>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// S-04 states for a file that can't be mapped yet: still being read, or not imported.
function FileState({ d, onImport, onRefresh }: { d: Json; onImport: () => void; onRefresh: () => Promise<void> }) {
  const [checking, setChecking] = useState(false);
  if (d.status === "failed" || d.status === "duplicate") {
    const dup = d.status === "duplicate";
    return (
      <div className="card"><div className="blank" role="alert" style={{ gap: 10 }}>
        {dup ? <TriangleAlert size={16} className="risk" aria-hidden="true" /> : <OctagonX size={16} className="risk" aria-hidden="true" />}
        <span className="strong">{dup ? `${d.file_name} was already imported.` : `${d.file_name} was not imported.`}</span>
        <span className="t-small muted">{d.error || (dup ? "Its rows are already stored from the earlier import." : "This file could not be read.")} There is nothing to map in it.</span>
        <Button size="sm" icon={ArrowLeft} onClick={onImport}>Back to Import</Button>
      </div></div>
    );
  }
  const p = d.progress ?? {};
  const pct = p.fields_detected ? 40 + 60 * ((p.fields_proposed ?? 0) / p.fields_detected) : p.header_found ? 30 : d.status === "parsing" ? 15 : 4;
  return (
    <div className="card"><div className="blank" role="status" style={{ gap: 10 }}>
      <span className="spinner md muted" aria-hidden="true" />
      <span>This file is still being read. The mapping opens as soon as it finishes.</span>
      <span style={{ width: 240 }}><Progress pct={pct} label={d.file_name} /></span>
      <span className="t-small muted">{d.rows_read != null ? `${fmt(d.rows_read)} rows read` : "Waiting to start"}</span>
      <Button size="sm" icon={RotateCcw} busy={checking} onClick={async () => { setChecking(true); try { await onRefresh(); } finally { setChecking(false); } }}>Check again</Button>
    </div></div>
  );
}
