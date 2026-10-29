// S-05: datasets kept across chats. Each chat's imported files form one dataset.
import { useEffect, useState } from "react";
import { MessageSquare, Plus, Upload } from "lucide-react";
import { api, type Json } from "../api";
import { DATASET_STATUS, dateLabel, fmt, monthLabel, PLATFORMS_OF } from "../components/common";
import { Button, Empty, ErrorBlank, Loading, NoResults, SearchField, StateChip } from "../components/ui";

export function SavedDataScreen({ sessions, onOpen, onPlan, onNewChat }: {
  sessions: Json[];
  onOpen: (sid: string, stage: "import" | "map" | "ask") => void; onPlan: (sid: string) => void; onNewChat: () => void;
}) {
  const [detail, setDetail] = useState<Json[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const query = q.toLowerCase();

  const load = () => {
    setError(null);
    Promise.all(sessions.map((s) => api.session(s.id)))
      .then((all) => setDetail(all.filter((s) => (s.datasets ?? []).length)))
      .catch((e) => setError(e.message));
  };
  useEffect(load, [sessions.map((s) => s.id).join()]); // eslint-disable-line

  const rows = (detail ?? []).filter((s) => !query || `${s.title} ${(s.datasets ?? []).map((d: Json) => d.file_name).join(" ")}`.toLowerCase().includes(query));
  const cur = rows.find((s) => s.id === sel) ?? rows[0];

  return (
    <main className="content">
      <div className="inner">
        <div className="pagehead">
          <div className="stack"><h1>Saved data</h1><p className="muted">Datasets kept across chats. Reuse one in a new plan without importing again.</p></div>
          <span className="row"><SearchField value={q} onChange={setQ} placeholder="Filter datasets" width={220} /><Button variant="primary" icon={Upload} onClick={onNewChat}>Upload a dataset</Button></span>
        </div>

        <section className="card">
          {error ? <ErrorBlank text={`The datasets didn’t load. Nothing was changed. ${error}`} onAction={load} />
            : !detail ? <Loading text="Loading datasets." />
            : !detail.length ? <Empty text="No saved datasets yet." action="Upload a dataset" onAction={onNewChat} />
            : !rows.length ? <NoResults count={`0 of ${detail.length} datasets`} text="No datasets match these filters." action="Clear filters" onAction={() => setQ("")} />
            : (
              <>
                <div className="tblwrap">
                  <table className="tbl">
                    <thead><tr><th>Dataset</th><th>Platforms</th><th className="num">Files</th><th>Months planned</th><th className="num">Rows</th><th>Used in</th><th>Updated</th></tr></thead>
                    <tbody>
                      {rows.map((s) => {
                        const ds: Json[] = s.datasets;
                        const runs: Json[] = (s.runs ?? []).filter((r: Json) => r.status === "succeeded");
                        const months = runs.map((r) => r.target_month).sort();
                        const on = cur?.id === s.id;
                        return (
                          <tr key={s.id} className={`clickable ${on ? "sel" : ""}`} onClick={() => setSel(s.id)} aria-selected={on}>
                            <td className="strong"><span className="row"><span className="selmark" style={{ height: 20, background: on ? undefined : "transparent" }} aria-hidden="true" />
                              <button type="button" className="linkbtn" style={{ textDecoration: "none", fontWeight: 500 }} onClick={() => setSel(s.id)}>{s.title || "Untitled chat"}</button></span></td>
                            <td>{PLATFORMS_OF(ds).join(" · ") || "—"}</td>
                            <td className="num">{ds.length}</td>
                            <td>{months.length ? months.length > 1 ? `${monthLabel(months[0])} – ${monthLabel(months[months.length - 1])}` : monthLabel(months[0]) : "—"}</td>
                            <td className="num">{fmt(ds.reduce((n, d) => n + (d.rows_read || 0), 0))}</td>
                            <td>{runs.length ? <a href={`#/s/${s.id}/ask`} onClick={(e) => e.stopPropagation()}>{runs.length} plan{runs.length > 1 ? "s" : ""}</a> : <span className="muted">Not used yet</span>}</td>
                            <td className="muted">{dateLabel(ds[ds.length - 1]?.created_at ?? s.created_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="card-foot"><span>Showing {rows.length} of {detail.length} rows</span></div>
              </>
            )}
        </section>

        {cur && (
          <section className="card">
            <div className="card-head">
              <h2>Files in {cur.title || "this chat"}</h2>
              <span className="row">
                <Button size="sm" icon={Plus} onClick={() => onOpen(cur.id, "import")}>Add files</Button>
                <Button size="sm" icon={MessageSquare} disabled={!cur.datasets.some((d: Json) => d.status === "ready")} reason="Map a sales-order file to plan." onClick={() => onPlan(cur.id)}>Plan from this dataset</Button>
              </span>
            </div>
            <div className="tblwrap">
              <table className="tbl">
                <thead><tr><th>File</th><th className="ctr">Status</th><th className="num">Rows</th><th>Type</th><th>Imported</th><th>Actions</th></tr></thead>
                <tbody>
                  {cur.datasets.map((d: Json) => {
                    const [label, tone] = DATASET_STATUS[d.status] ?? [d.status, "neutral"];
                    return (
                      <tr key={d.id}>
                        <td className="strong">{d.file_name}</td>
                        <td className="ctr"><StateChip tone={tone}>{label}</StateChip></td>
                        <td className="num">{fmt(d.rows_read)}</td>
                        <td>{d.file_type ?? "—"}</td>
                        <td className="muted">{dateLabel(d.created_at)}</td>
                        <td>{d.status === "ready" ? <a href={`#/s/${cur.id}/map`}>Review mapping</a>
                          : ["parsed", "mapped"].includes(d.status) ? <a href={`#/s/${cur.id}/map`}>Continue to mapping</a>
                          : <a href={`#/s/${cur.id}/import`}>Open import</a>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="card-foot line"><span>Showing {cur.datasets.length} of {cur.datasets.length} rows</span></div>
          </section>
        )}
      </div>
    </main>
  );
}
