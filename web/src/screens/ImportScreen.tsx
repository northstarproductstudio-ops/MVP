// MVP-01/02/03: import 1–10 files, then show processing progress per file.
import { useEffect, useRef, useState } from "react";
import { api, type Json } from "../api";
import { bytes, Chip, fmt } from "../components/common";

const MAX = 10;

export function ImportScreen({ sessionId, datasets, onProcessed }: {
  sessionId: string;
  datasets: Json[];
  onProcessed: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tracking, setTracking] = useState<Json[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const existing = datasets.filter((d) => !["failed", "duplicate"].includes(d.status)).length;
  const room = MAX - existing - files.length;

  const add = (list: FileList | null) => {
    if (!list) return;
    const ok = Array.from(list).filter((f) => /\.(csv|xlsx|xlsm|xls)$/i.test(f.name));
    setFiles((cur) => [...cur, ...ok].slice(0, MAX - existing));
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.upload(sessionId, files);
      setTracking(res.files);
      setFiles([]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  // poll processing progress
  useEffect(() => {
    const ids = tracking.filter((t) => t.dataset_id && t.status === "uploaded").map((t) => t.dataset_id);
    if (!ids.length) return;
    let stop = false;
    const tick = async () => {
      const st = await Promise.all(ids.map((id: string) => api.importStatus(id)));
      if (stop) return;
      setTracking((cur) => cur.map((t) => ({ ...t, live: st.find((s: Json) => s.id === t.dataset_id) ?? t.live })));
      if (st.every((s: Json) => ["parsed", "failed", "mapped", "ready"].includes(s.status))) onProcessed();
      else setTimeout(tick, 1500);
    };
    tick();
    return () => {
      stop = true;
    };
  }, [tracking.length]); // eslint-disable-line

  if (tracking.length) {
    const done = tracking.filter((t) => ["parsed", "failed", "ready"].includes(t.live?.status)).length;
    return (
      <div className="col">
        <div>
          <h1>Processing {tracking.length} files</h1>
          <p className="sub">Reading rows, resolving field types and proposing how each field is stored.</p>
        </div>
        <div className="card">
          {tracking.map((t, i) => {
            const s = t.live ?? {};
            const status = t.status !== "uploaded" ? t.status : s.status ?? "queued";
            return (
              <div className="file" key={i}>
                <div>
                  <strong>{t.file_name}</strong>
                  <div className="muted">{bytes(t.size)} {s.progress?.sheets ? `· ${s.progress.sheets.length} sheet(s)` : ""} {s.type_name ? `· ${s.type_name}` : ""}</div>
                  {s.rows_read != null && (
                    <div className="stats">
                      <div className="stat"><div className="k">Rows read</div><div className="v">{fmt(s.rows_read)}</div></div>
                      <div className="stat"><div className="k">Fields detected</div><div className="v">{fmt(s.progress?.fields_detected)}</div></div>
                      <div className="stat"><div className="k">Header row</div><div className="v">{s.progress?.header_found ? "Found" : "–"}</div></div>
                      <div className="stat"><div className="k">Fields proposed</div><div className="v">{fmt(s.progress?.fields_proposed)} of {fmt(s.progress?.fields_detected)}</div></div>
                    </div>
                  )}
                  {(t.error || s.error) && <div className="error">{t.error || s.error}</div>}
                </div>
                <div>
                  <Chip tone={status === "parsed" || status === "ready" ? "ok" : status === "failed" || status === "duplicate" ? "bad" : "info"}>
                    {status === "parsed" ? "Complete" : status === "parsing" ? "Reading" : status === "uploaded" ? "Queued" : status}
                  </Chip>
                </div>
              </div>
            );
          })}
          <div className="footer">
            <span className="muted">File {Math.min(done + 1, tracking.length)} of {tracking.length} · preparing the proposed mapping</span>
            <button className="btn" onClick={onProcessed}>Go to mapping</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="col">
      <div>
        <h1>Import your sources</h1>
        <p className="sub">Add between 1 and 10 files of sales, inventory, purchase orders or market data. Any mix works — plan Blinkit alone with only Blinkit files.</p>
      </div>
      <div
        className={`drop ${files.length ? "compact" : ""} ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
      >
        <div>
          <h2>{files.length ? "Drop more files here" : "Drop files here"}</h2>
          <p className="muted">{files.length ? `${Math.max(0, room)} more can be added.` : "or choose them from your computer"}</p>
        </div>
        <div>
          <button className="btn" onClick={() => input.current?.click()} disabled={room <= 0}>Add files</button>
          <input ref={input} type="file" multiple hidden accept=".csv,.xlsx,.xlsm,.xls" onChange={(e) => add(e.target.files)} />
        </div>
        {!files.length && <p className="muted" style={{ marginTop: 16 }}>CSV, XLSX and other tabular files.</p>}
      </div>
      {files.length > 0 && (
        <div className="card">
          <div className="row between" style={{ padding: "12px 20px" }}>
            <h2>Files to process</h2>
            <span className="muted">{files.length + existing} of {MAX}</span>
          </div>
          {files.map((f, i) => (
            <div className="file" key={i}>
              <div><strong>{f.name}</strong><div className="muted">{bytes(f.size)} · not read yet</div></div>
              <div className="row"><Chip>Queued</Chip><button className="btn ghost small" onClick={() => setFiles(files.filter((_, j) => j !== i))}>Remove</button></div>
            </div>
          ))}
        </div>
      )}
      {error && <div className="notice bad">{error}</div>}
      <div className="card footer">
        <span className="muted">{files.length + existing} of {MAX} files added. {files.length ? "Processing reads every file and proposes how its fields are stored." : "Add at least one file to continue."}</span>
        <div className="row">
          {existing > 0 && !files.length && <button className="btn" onClick={onProcessed}>Continue with {existing} imported</button>}
          <button className="btn primary" onClick={start} disabled={!files.length || busy}>{busy ? "Uploading…" : "Start process"}</button>
        </div>
      </div>
    </div>
  );
}
