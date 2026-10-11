// Gap 12: Data — add, replace, remove and clear imported files at any time.
// New data arrives every two weeks or monthly: open the chat → Data → add or replace the
// new exports → saved mappings apply → the readiness panel shows ready → ask for the plan.
import { useEffect, useRef, useState } from "react";
import { api, type Json } from "../api";
import { Chip, fmt } from "../components/common";
import { ReadinessPanel, useReadiness } from "../components/ReadinessPanel";

const GROUP_HINT: Record<string, string> = {
  sellin: "Zoho sales-order export", po: "platform PO export (Instamart, Zepto or BigBasket)",
  stock: "Blinkit DOH export", market: "Dcluttr category insight export",
};

const STATUS: Record<string, [string, "ok" | "warn" | "bad" | "info" | undefined]> = {
  ready: ["Ready", "ok"], uploaded: ["Reading", "info"], parsing: ["Reading", "info"], parsed: ["Needs mapping", "warn"],
  mapped: ["Storing", "info"], failed: ["Failed", "bad"], removed: ["Removed", undefined],
};

const period = (d: Json) => {
  if (!d.data_from) return "–";
  const f = (s: string) => new Date(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  return d.data_from === d.data_to ? f(d.data_from) : `${f(d.data_from)} – ${f(d.data_to)}`;
};

export function DataScreen({ sessionId, suggest, onAddFiles, onMap, onAsk }: {
  sessionId: string;
  suggest?: string | null;
  onAddFiles: () => void;
  onMap: () => void;
  onAsk: () => void;
}) {
  const [data, setData] = useState<Json | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [readiness, reloadReadiness] = useReadiness(sessionId, data?.datasets?.map((d: Json) => d.id + d.status));
  const replaceFor = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = () => api.datasets(sessionId).then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [sessionId]); // eslint-disable-line
  // keep statuses fresh while files are being read or stored
  useEffect(() => {
    if (!data?.datasets?.some((d: Json) => ["uploaded", "parsing", "mapped"].includes(d.status))) return;
    const t = setTimeout(load, 2500);
    return () => clearTimeout(t);
  }, [data]); // eslint-disable-line

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await load();
      reloadReadiness();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const rows: Json[] = data?.datasets ?? [];
  const locked = Boolean(data?.plan_running);
  const needsMapping = rows.some((d) => d.status === "parsed");

  return (
    <div className="col wide">
      <div className="row between wrap">
        <div>
          <h1>Data</h1>
          <p className="sub">{rows.length} files in this chat · add the new month's exports or replace last month's; saved mappings apply automatically.</p>
        </div>
        <div className="row">
          <button className="btn ghost" onClick={onAsk}>Back to Ask</button>
          <button className="btn primary" onClick={onAddFiles} disabled={locked}>Add files</button>
        </div>
      </div>

      {suggest && GROUP_HINT[suggest] && (
        <div className="notice info">The assistant needs a {GROUP_HINT[suggest]} to answer that. Use <strong>Add files</strong> to import it.</div>
      )}
      {locked && <div className="notice">A plan is running in this chat; files can be changed when it finishes.</div>}
      {error && <div className="notice bad">{error}</div>}

      <ReadinessPanel readiness={readiness} compact onMapNow={needsMapping ? () => onMap() : undefined} />

      <div className="card">
        <div className="scroll">
          <table className="grid">
            <thead><tr><th>File</th><th>Type</th><th>Period</th><th className="num">Rows</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map((d) => {
                const [label, tone] = STATUS[d.status] ?? [d.status, undefined];
                return (
                  <tr key={d.id}>
                    <td><strong>{d.file_name}</strong>
                      {d.replaces_dataset_id && <div className="muted" style={{ fontSize: 12 }}>replaces an earlier version</div>}
                      {d.error && <div className="error" style={{ fontSize: 12 }}>{d.error}</div>}</td>
                    <td><Chip tone={d.plan_role === "essential" ? "ok" : undefined}>{d.type_name ?? "Not tagged yet"}</Chip></td>
                    <td>{period(d)}</td>
                    <td className="num">{fmt(d.rows_read)}</td>
                    <td><Chip tone={tone}>{label}</Chip>{d.status === "parsed" && <button className="btn small" style={{ marginLeft: 6 }} onClick={onMap}>Map</button>}</td>
                    <td className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn small" disabled={locked || busy !== null}
                        onClick={() => { replaceFor.current = d.id; fileInput.current?.click(); }}>
                        {busy === `r${d.id}` ? "Uploading…" : "Replace"}</button>
                      <button className="btn small" disabled={locked || busy !== null}
                        onClick={() => act(`x${d.id}`, () => api.removeDataset(d.id))}>
                        {busy === `x${d.id}` ? "Removing…" : "Remove"}</button>
                    </td>
                  </tr>
                );
              })}
              {!rows.length && <tr><td colSpan={6} className="muted">No files yet. Add a Zoho sales-order export to plan, and any platform or market files to improve it.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="footer">
          <span className="muted">Removing a file keeps every finished plan unchanged; the next question or plan uses the files listed here.</span>
          {confirmClear ? (
            <div className="row">
              <span>Remove all {rows.length} files from this chat?</span>
              <button className="btn" onClick={() => setConfirmClear(false)}>Cancel</button>
              <button className="btn primary" disabled={busy !== null}
                onClick={() => act("clear", () => api.clearDatasets(sessionId)).then(() => setConfirmClear(false))}>Clear all data</button>
            </div>
          ) : (
            <button className="btn ghost" disabled={locked || !rows.length} onClick={() => setConfirmClear(true)}>Clear all data</button>
          )}
        </div>
      </div>
      <input ref={fileInput} type="file" hidden accept=".csv,.xlsx,.xlsm,.xls"
        onChange={(e) => {
          const f = e.target.files?.[0];
          const id = replaceFor.current;
          e.target.value = "";
          if (f && id) act(`r${id}`, () => api.replaceDataset(id, f));
        }} />
    </div>
  );
}
