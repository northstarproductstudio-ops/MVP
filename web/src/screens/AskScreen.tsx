// MVP-06/07/08/09: Ask chat with suggested questions, live plan workflow card and report card.
import { useEffect, useRef, useState } from "react";
import { api, stream, type Json, type SseEvent } from "../api";
import { Chip, fmt } from "../components/common";
import { ReportView } from "./ReportView";

const SECTIONS = [
  "Reading imported data", "Cleaning demand data", "Correcting for availability", "Classifying demand patterns",
  "Fixing accuracy baseline", "Generating the forecast", "Building the plan",
];

type Turn = {
  role: "user" | "assistant";
  text: string;
  tools: string[];
  evidence: Json[];
  runId?: string;
  card?: Json;
  failed?: Json;
};

export function AskScreen({ session, pendingQuestion, onConsumed }: {
  session: Json;
  pendingQuestion: string | null;
  onConsumed: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>(() =>
    (session.messages ?? []).map((m: Json) => ({
      role: m.role, text: m.content ?? "", tools: [], evidence: m.evidence ?? [],
      runId: (m.tool_calls ?? []).map((t: Json) => t.run_id).filter(Boolean).pop(),
    })));
  // redraw report cards for runs referenced in the history
  useEffect(() => {
    turns.forEach((t, i) => {
      if (t.runId && !t.card && !t.failed) {
        api.run(t.runId).then((r) => {
          setTurns((ts) => ts.map((x, j) => (j === i ? {
            ...x,
            card: r.report_card ?? undefined,
            failed: r.status === "failed" ? { run_id: r.id, message: r.error, retry_allowed: true } : undefined,
          } : x)));
        }).catch(() => undefined);
      }
    });
  }, []); // eslint-disable-line
  const [sug, setSug] = useState<Json | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<{ runId: string; tab: "plan" | "reasoning" } | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.suggestions(session.id).then(setSug).catch(() => setSug(null));
  }, [session.id, turns.filter((t) => t.card).length]);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [turns]);
  useEffect(() => {
    if (pendingQuestion) {
      ask(pendingQuestion);
      onConsumed();
    }
  }, [pendingQuestion]); // eslint-disable-line

  const patchLast = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setQ("");
    setTurns((ts) => [...ts, { role: "user", text, tools: [], evidence: [] }, { role: "assistant", text: "", tools: [], evidence: [] }]);
    try {
      await stream(`/sessions/${session.id}/messages`, { content: text }, (e: SseEvent) => {
        if (e.type === "message_delta") patchLast((t) => ({ ...t, text: t.text + e.text }));
        else if (e.type === "tool_call") patchLast((t) => ({ ...t, tools: [...t.tools, e.label] }));
        else if (e.type === "evidence") patchLast((t) => ({ ...t, evidence: [...t.evidence, e] }));
        else if (e.type === "run_queued") patchLast((t) => ({ ...t, runId: e.run_id }));
        else if (e.type === "report_card") patchLast((t) => ({ ...t, card: e, runId: e.run_id }));
        else if (e.type === "run_failed") patchLast((t) => ({ ...t, failed: e }));
      });
    } catch (err: any) {
      patchLast((t) => ({ ...t, text: t.text || `Something went wrong: ${err.message}` }));
    } finally {
      setBusy(false);
    }
  };

  const empty = turns.length === 0;
  const ws = sug?.workspace;

  return (
    <div className="col">
      <div>
        <h1>Ask about your data and the plan</h1>
        {ws?.has_data && (
          <p className="sub">{ws.datasets} files · data {ws.date_from} → {ws.as_of} · {ws.skus} SKUs · {ws.cities?.length} cities · {ws.platforms?.length} platforms</p>
        )}
      </div>

      {empty && sug && (
        <div className="suggest">
          {(["plan", "why", "data"] as const).map((g) => (
            <div className="group" key={g}>
              <h3>{{ plan: "About the plan", why: "About why", data: "About the data" }[g]}</h3>
              {(sug[g] ?? []).map((s: string) => <button key={s} onClick={() => ask(s)}>{s}</button>)}
              {!(sug[g] ?? []).length && <p className="muted">{g === "why" ? "Appears once a plan exists or the data shows a clear move." : "—"}</p>}
            </div>
          ))}
        </div>
      )}
      {empty && sug && <p className="muted">The suggestions above are built from what is in your data.</p>}

      <div className="chat">
        {turns.map((t, i) => (
          <div key={i} className={`msg ${t.role}`}>
            {t.role === "user" ? t.text : (
              <>
                {t.tools.map((l, j) => <div className="toolchip" key={j}>↳ {l}</div>)}
                {t.runId && <RunCard runId={t.runId} collapsed={Boolean(t.card)}
                  onCard={(card) => setTurns((ts) => ts.map((x, j) => (j === i && !x.card ? { ...x, card } : x)))}
                  onFailed={(f) => setTurns((ts) => ts.map((x, j) => (j === i && !x.failed ? { ...x, failed: f } : x)))} />}
                {t.failed && (
                  <div className="notice bad" style={{ marginTop: 8 }}>
                    Plan stopped{t.failed.step ? ` at ${t.failed.step}` : ""}: {t.failed.message}
                    {t.failed.retry_allowed && <div style={{ marginTop: 8 }}><button className="btn small" onClick={async () => { const r = await api.retry(t.failed!.run_id); patchRun(i, r.run_id); }}>Retry</button></div>}
                  </div>
                )}
                {t.card && <ReportCard card={t.card} onOpen={(tab) => setReport({ runId: t.card!.run_id, tab })} />}
                {(t.text || (busy && i === turns.length - 1)) && <div className="bubble">{t.text || "…"}</div>}
                {t.evidence.map((ev, j) => <Evidence key={j} ev={ev} />)}
              </>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <div className="composer">
        <form onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about the imported data or the demand plan…" disabled={busy} />
          <button className="btn primary" disabled={busy || !q.trim()}>{busy ? "Working…" : "Send"}</button>
        </form>
      </div>

      {report && <ReportView runId={report.runId} initialTab={report.tab} onClose={() => setReport(null)} />}
    </div>
  );

  function patchRun(i: number, runId: string) {
    setTurns((ts) => ts.map((t, j) => (j === i ? { ...t, runId, failed: undefined } : t)));
  }
}

function RunCard({ runId, collapsed, onCard, onFailed }: {
  runId: string; collapsed?: boolean; onCard?: (c: Json) => void; onFailed?: (f: Json) => void;
}) {
  const [steps, setSteps] = useState<Record<number, Json>>({});
  const [agent, setAgent] = useState<Json | null>(null);
  const [status, setStatus] = useState<string>("running");
  const [open, setOpen] = useState(!collapsed);
  const started = useRef(Date.now());
  const [, tick] = useState(0);

  useEffect(() => {
    const ctl = new AbortController();
    stream(`/runs/${runId}/events`, null, (e) => {
      if (e.type === "step") setSteps((s) => ({ ...s, [e.step_no]: e }));
      else if (e.type === "agent_progress") setAgent(e);
      else if (e.type === "report_card") { setStatus("done"); onCard?.(e); }
      else if (e.type === "run_failed") { setStatus("failed"); onFailed?.(e); }
    }, ctl.signal).catch(() => undefined);
    const t = setInterval(() => tick((x) => x + 1), 1000);
    return () => { ctl.abort(); clearInterval(t); };
  }, [runId]);

  const bySection = SECTIONS.map((name) => {
    const st = Object.values(steps).filter((s) => s.section === name);
    const statuses = st.map((s) => s.status);
    const state = statuses.includes("failed") ? "failed"
      : st.length && statuses.every((x) => ["succeeded", "reused", "skipped"].includes(x)) ? "done"
      : statuses.some((x) => x !== "pending") ? "running" : "pending";
    return { name, state, summary: st.map((s) => s.summary).filter(Boolean).join(" · "), warnings: st.flatMap((s) => s.warnings ?? []) };
  });
  const doneN = bySection.filter((s) => s.state === "done").length;
  const elapsed = Math.round((Date.now() - started.current) / 1000);

  return (
    <div className="card pad runcard" style={{ marginBottom: 8 }}>
      <div className="row between">
        <div>
          <h2>Demand planning workflow{status === "done" ? " · complete" : status === "failed" ? " · stopped" : ""}</h2>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {status === "running" ? `Running · step ${Math.min(doneN + 1, 7)} of 7 · elapsed ${elapsed} s` : `All ${doneN} of 7 sections`}
            {agent && status === "running" ? ` · ${agent.label}` : ""}
          </p>
        </div>
        <div className="row">
          <button className="btn ghost small" onClick={() => setOpen(!open)}>{open ? "Hide step reasoning" : "Show step reasoning"}</button>
          {status === "running" && <button className="btn small" onClick={() => api.cancel(runId)}>Stop the workflow</button>}
        </div>
      </div>
      {open && bySection.map((s, i) => (
        <div className="section" key={s.name}>
          <span className={`dot ${s.state}`} />
          <div>
            <strong>{i + 1}. {s.name}</strong> {s.state === "running" && <Chip tone="info">In progress</Chip>}
            {s.state === "pending" && <span className="muted"> · Not started</span>}
            {s.summary && <div className="muted">{s.summary}</div>}
            {s.warnings.slice(0, 3).map((w: string) => <div key={w} className="muted" style={{ color: "var(--warn)" }}>! {w.length > 220 ? w.slice(0, 220) + "…" : w}</div>)}
          </div>
        </div>
      ))}
    </div>
  );
}

function ReportCard({ card, onOpen }: { card: Json; onOpen: (tab: "plan" | "reasoning") => void }) {
  const month = new Date(card.target_month).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  const [exporting, setExporting] = useState(false);
  const exp = async (kind: "plan" | "reasoning") => {
    setExporting(true);
    const { download } = await import("../api");
    await download(`/runs/${card.run_id}/export/${kind}.xlsx`, `${kind === "plan" ? "demand-plan" : "reasoning"}-${card.target_month.slice(0, 7)}-v${card.version}.xlsx`);
    setExporting(false);
  };
  return (
    <div className="card pad" style={{ marginBottom: 8 }}>
      <div className="row between">
        <div><Chip tone="ok">Plan created</Chip> <strong style={{ marginLeft: 6 }}>Demand plan · {month}</strong> <span className="muted">· version {card.version}</span></div>
      </div>
      <div className="kpis" style={{ marginTop: 12 }}>
        <div className="kpi"><div className="k">Total DP</div><div className="v">{fmt(card.dp_total)}</div></div>
        <div className="kpi"><div className="k">vs last month's sell-in</div><div className="v">{card.change_pct == null ? "–" : `${card.change_pct > 0 ? "+" : ""}${card.change_pct}%`}</div></div>
        <div className="kpi"><div className="k">Lines planned</div><div className="v">{fmt(card.lines)}</div></div>
        <div className="kpi"><div className="k">Agent fallback · review</div><div className="v">{fmt(card.agent_fallback)} · {fmt(card.review)}</div></div>
      </div>
      {card.overview && <p style={{ marginBottom: 0 }}>{card.overview}</p>}
      {card.order_total == null && <p className="muted" style={{ marginBottom: 0 }}>Order quantity needs stock data.</p>}
      {(card.warnings ?? []).length > 0 && (
        <details style={{ marginTop: 8 }}><summary className="muted">{card.warnings.length} warnings</summary>
          <ul className="muted">{card.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul></details>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" onClick={() => onOpen("plan")}>View plan</button>
        <button className="btn" onClick={() => onOpen("reasoning")}>View reasoning</button>
        <button className="btn primary" disabled={exporting} onClick={() => exp("plan")}>Export plan</button>
        <button className="btn" disabled={exporting} onClick={() => exp("reasoning")}>Export reasoning</button>
      </div>
    </div>
  );
}

function Evidence({ ev }: { ev: Json }) {
  const rows: Json[] = ev.rows ?? [];
  const cols = rows.length ? Object.keys(rows[0]).filter((c) => !["bridge", "applied_factors"].includes(c)).slice(0, 8) : [];
  return (
    <details className="evidence">
      <summary>What it rests on · {fmt(ev.row_count)} rows · {ev.label}</summary>
      {rows.length > 0 && (
        <div className="scroll" style={{ maxHeight: 260 }}>
          <table className="grid">
            <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 50).map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : ""}>{typeof r[c] === "number" ? fmt(r[c], 2) : String(r[c] ?? "")}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </details>
  );
}
