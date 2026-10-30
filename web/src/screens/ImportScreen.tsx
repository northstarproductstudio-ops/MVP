// S-02 Import (1–10 files per chat) and S-03 Processing (reading rows and proposing field storage).
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Clock, FileSpreadsheet, FileUp, X } from "lucide-react";
import { api, type Json } from "../api";
import { bytes, DATASET_STATUS, fmt } from "../components/common";
import { Button, Empty, IconButton, Progress, StateChip } from "../components/ui";

const MAX = 10;
const OK = /\.(csv|xlsx|xlsm|xls)$/i;
const DONE = ["parsed", "failed", "mapped", "ready", "duplicate"];

type Rejected = { name: string; size: number; reason: string };

export function ImportScreen({ sessionId, datasets, onRefresh, onContinue }: {
  sessionId: string;
  datasets: Json[];
  onRefresh: () => Promise<void>;
  onContinue: () => void;
}) {
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState<File[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tracking, setTracking] = useState<string[]>([]);
  const [live, setLive] = useState<Record<string, Json>>({});
  const [processing, setProcessing] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const used = datasets.filter((d) => !["failed", "duplicate"].includes(d.status)).length;
  const room = MAX - used - uploading.length;

  const add = async (list: FileList | null) => {
    if (!list) return;
    const all = Array.from(list);
    const bad = all.filter((f) => !OK.test(f.name)).map((f) => ({
      name: f.name, size: f.size,
      reason: `Not imported — .${f.name.split(".").pop()} files are not supported. Export this as CSV or XLSX and add it again.`,
    }));
    const good = all.filter((f) => OK.test(f.name));
    const take = good.slice(0, Math.max(0, room));
    const over = good.slice(take.length).map((f) => ({ name: f.name, size: f.size, reason: `Not imported — a chat holds up to ${MAX} files.` }));
    setRejected((r) => [...r, ...bad, ...over]);
    if (!take.length) return;
    setError(null);
    setUploading(take);
    try {
      const res = await api.upload(sessionId, take);
      const ids = (res.files ?? []).map((f: Json) => f.dataset_id).filter(Boolean);
      setTracking(ids);
      setProcessing(ids.length > 0);
      await onRefresh();
    } catch (e: any) {
      setError(`Upload failed for ${take.map((f) => f.name).join(", ")}. Nothing was saved. ${e.message}`);
    } finally {
      setUploading([]);
    }
  };

  // poll reading progress for the files just uploaded
  useEffect(() => {
    if (!tracking.length) return;
    let stop = false;
    const tick = async () => {
      const st = await Promise.all(tracking.map((id) => api.importStatus(id).catch(() => null)));
      if (stop) return;
      setLive((cur) => ({ ...cur, ...Object.fromEntries(st.filter((s): s is Json => Boolean(s)).map((s) => [s.id, s])) }));
      if (st.every((s) => s && DONE.includes(s.status))) {
        // reading finished: back to the file list; mapping opens only from "Continue to mapping"
        await onRefresh();
        setTracking([]);
        setProcessing(false);
      } else setTimeout(tick, 1500);
    };
    tick();
    return () => { stop = true; };
  }, [tracking.join()]); // eslint-disable-line

  const rows = datasets.map((d) => ({ ...d, ...(live[d.id] ?? {}) }));
  if (processing && tracking.length) {
    return <Processing files={rows.filter((d) => tracking.includes(d.id))} onCancel={() => setProcessing(false)} />;
  }

  const ready = rows.filter((d) => ["parsed", "mapped", "ready"].includes(d.status)).length;
  const readingN = rows.filter((d) => ["uploaded", "parsing"].includes(d.status)).length + uploading.length;
  const attention = rows.filter((d) => ["failed", "duplicate"].includes(d.status)).length + rejected.length;
  const total = rows.length + uploading.length + rejected.length;

  return (
    <>
      <main className="content">
        <div className="inner">
          <div className="stack">
            <h1>Import sales files</h1>
            <p className="muted">Add platform sales exports from Blinkit, Zepto or BigBasket, plus any inventory, purchase-order or market files. Each file is read, then you map its fields.</p>
          </div>
          <div className={`drop ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}>
            <FileUp size={16} aria-hidden="true" />
            <span>Drop sales exports here, or{" "}
              <button type="button" className="linkbtn" onClick={() => input.current?.click()} disabled={room <= 0}>choose files</button>
            </span>
            <span className="t-caption muted">CSV or XLSX · {used} of {MAX} files used in this chat</span>
            <input ref={input} type="file" multiple hidden accept=".csv,.xlsx,.xlsm,.xls" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          </div>
          {error && <div className="banner crit" role="alert"><span className="grow">{error}</span></div>}
          <div className="row between" style={{ alignItems: "baseline" }}>
            <h2>Files in this chat</h2>
            <span className="t-small muted">Showing {total} of {total} files</span>
          </div>
          {total === 0 ? (
            <div className="card"><Empty text="No files yet. Add the platform exports you want to plan from." action="Choose files" onAction={() => input.current?.click()} /></div>
          ) : (
            <div className="stack g8">
              {rows.map((d) => <FileRow key={d.id} d={d} />)}
              {uploading.map((f) => (
                <FileRow key={`u-${f.name}`} d={{ file_name: f.name, file_size: f.size, status: "uploaded" }} />
              ))}
              {rejected.map((r, i) => (
                <FileRow key={`r-${i}`} d={{ file_name: r.name, file_size: r.size, status: "failed", error: r.reason }}
                  onRemove={() => setRejected((x) => x.filter((_, j) => j !== i))} />
              ))}
            </div>
          )}
        </div>
      </main>
      <footer className="footbar">
        <span className="muted">{ready} of {total} files ready{readingN ? ` · ${readingN} still reading` : ""}{attention ? ` · ${attention} need attention` : ""}</span>
        <Button variant="primary" iconRight={ArrowRight} disabled={!ready} reason="Continue to mapping once a file is ready." onClick={onContinue}>
          Continue to mapping · {ready}
        </Button>
      </footer>
    </>
  );
}

function FileRow({ d, onRemove }: { d: Json; onRemove?: () => void }) {
  const [label, tone] = DATASET_STATUS[d.status] ?? [d.status, "neutral"];
  const reading = ["uploaded", "parsing"].includes(d.status);
  const meta = [bytes(d.file_size), d.rows_read != null ? `${fmt(d.rows_read)} rows${reading ? " read" : ""}` : null].filter(Boolean).join(" · ");
  const reason = d.status === "failed" ? d.error || "Not imported — this file could not be read." : d.status === "duplicate" ? d.error || "Already imported in this workspace." : null;
  return (
    <div className="filecard">
      <div className="line1">
        <FileSpreadsheet size={16} aria-hidden="true" />
        <span className="stack grow" style={{ gap: 0 }}><span className="strong">{d.file_name}</span><span className="t-caption muted">{meta || "Not read yet"}</span></span>
        <span className="chipcol"><StateChip tone={tone}>{label}</StateChip></span>
        {onRemove && <IconButton icon={X} label={`Remove ${d.file_name}`} size="sm" onClick={onRemove} />}
      </div>
      {reason && <span className="sub t-small">{reason}</span>}
      {reading && <span className="sub"><Progress pct={progressOf(d)} thin label={d.file_name} /></span>}
    </div>
  );
}

function progressOf(d: Json): number {
  if (DONE.includes(d.status)) return 100;
  const p = d.progress ?? {};
  if (p.fields_detected) return 40 + 60 * ((p.fields_proposed ?? 0) / p.fields_detected);
  if (p.header_found) return 30;
  return d.status === "parsing" ? 15 : 4;
}

function Processing({ files, onCancel }: { files: Json[]; onCancel: () => void }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => { const t = setTimeout(() => setSlow(true), 10000); return () => clearTimeout(t); }, []);
  const detected = files.reduce((s, f) => s + (f.progress?.fields_detected ?? 0), 0);
  const proposed = files.reduce((s, f) => s + (DONE.includes(f.status) ? f.progress?.fields_detected ?? 0 : f.progress?.fields_proposed ?? 0), 0);
  return (
    <main className="content centered">
      <section aria-labelledby="proc-t" className="card container" style={{ width: 640, maxWidth: "100%", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="stack">
          <h1 id="proc-t">Reading your files</h1>
          <p className="muted">Reading rows, resolving field types and proposing how each field is stored.</p>
        </div>
        {slow && (
          <div role="status" className="banner info">
            <Clock size={14} aria-hidden="true" />
            <span>Still working — large files take a little longer. You can leave this screen open; reading carries on if you switch to another chat, and the mapping opens as soon as it finishes.</span>
          </div>
        )}
        <ul className="stack g12" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {files.map((f) => {
            const done = DONE.includes(f.status);
            const label = f.status === "failed" ? "Reading stopped" : done ? `Done · ${fmt(f.rows_read)} rows` : f.rows_read != null ? `${fmt(f.rows_read)} rows read` : "Queued";
            return (
              <li key={f.id} className="stack" style={{ gap: 6 }}>
                <span className="row between">
                  <span className="strong">{f.file_name}</span>
                  <span className="t-small muted row" style={{ gap: 6 }}>{done && f.status !== "failed" && <StateChip tone="good">{""}</StateChip>}{label}</span>
                </span>
                <Progress pct={progressOf(f)} label={f.file_name} />
                {f.status === "failed" && <span className="t-small risk">Reading stopped for {f.file_name}. Your other files are fine. {f.error}</span>}
              </li>
            );
          })}
        </ul>
        <div className="row between" style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          <span aria-live="polite">Fields resolved: <span className="strong">{detected ? `${fmt(proposed)} of ${fmt(detected)}` : "—"}</span></span>
          <Button icon={ArrowLeft} onClick={onCancel}>Cancel and keep files</Button>
        </div>
        <span className="t-caption muted">Cancel returns to Import with every file intact.</span>
      </section>
    </main>
  );
}
