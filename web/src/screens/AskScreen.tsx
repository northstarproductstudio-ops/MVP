// S-07 / MVP-06–09: Ask thread with suggested questions, the live plan workflow card and the report card.
import { useEffect, useRef, useState } from "react";
import { ArrowUp, ArrowUpRight, ChevronDown, ChevronRight, Copy, Database, Download, RotateCcw } from "lucide-react";
import { api, download, stream, type Json, type SseEvent } from "../api";
import { fmt, monthLabel, PLATFORMS_OF } from "../components/common";
import { Banner, Button, StateChip } from "../components/ui";

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
  error?: string;
  question?: string;
};

export function AskScreen({ session, crosswalkCount, pendingQuestion, draft, onConsumed, onOpenReport, onGoMap, onGoCrosswalk, onTitle }: {
  session: Json;
  crosswalkCount: number | null;
  pendingQuestion: string | null;
  draft: string | null;
  onConsumed: () => void;
  onOpenReport: (runId: string) => void;
  onGoMap: () => void;
  onGoCrosswalk: () => void;
  onTitle: () => void;
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
            failed: ["failed", "cancelled"].includes(r.status) ? { run_id: r.id, message: r.error, retry_allowed: true, status: r.status } : undefined,
          } : x)));
        }).catch(() => undefined);
      }
    });
  }, []); // eslint-disable-line
  const [sug, setSug] = useState<Json | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [unseen, setUnseen] = useState(0);
  const scroller = useRef<HTMLElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  useEffect(() => {
    api.suggestions(session.id).then(setSug).catch(() => setSug(null));
  }, [session.id, turns.filter((t) => t.card).length]);
  useEffect(() => {
    if (atBottom.current) end.current?.scrollIntoView({ behavior: "smooth" });
    else setUnseen((n) => n + 1);
  }, [turns.length, turns[turns.length - 1]?.card, turns[turns.length - 1]?.runId]); // eslint-disable-line
  useEffect(() => {
    if (pendingQuestion) ask(pendingQuestion);
    else if (draft) setQ(draft);
    if (pendingQuestion || draft) onConsumed();
  }, [pendingQuestion, draft]); // eslint-disable-line

  const patchLast = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setQ("");
    atBottom.current = true;
    setTurns((ts) => [...ts, { role: "user", text, tools: [], evidence: [] }, { role: "assistant", text: "", tools: [], evidence: [], question: text }]);
    try {
      await stream(`/sessions/${session.id}/messages`, { content: text }, (e: SseEvent) => {
        if (e.type === "message_delta") patchLast((t) => ({ ...t, text: t.text + e.text }));
        else if (e.type === "tool_call") patchLast((t) => ({ ...t, tools: [...t.tools, e.label] }));
        else if (e.type === "evidence") patchLast((t) => ({ ...t, evidence: [...t.evidence, e] }));
        else if (e.type === "run_queued") patchLast((t) => ({ ...t, runId: e.run_id }));
        else if (e.type === "report_card") patchLast((t) => ({ ...t, card: e, runId: e.run_id }));
        else if (e.type === "run_failed") patchLast((t) => ({ ...t, failed: e }));
      });
      if (!(session.messages ?? []).length) onTitle();
    } catch (err: any) {
      patchLast((t) => ({ ...t, error: err.message }));
    } finally {
      setBusy(false);
    }
  };

  const retryLast = (i: number) => {
    const question = turns[i].question;
    setTurns((ts) => ts.filter((_, j) => j !== i && j !== i - 1));
    if (question) setTimeout(() => ask(question), 0);
  };

  const datasets: Json[] = session.datasets ?? [];
  const ready = datasets.some((d) => d.status === "ready");
  const empty = turns.length === 0;
  const lastCard = [...turns].reverse().find((t) => t.card)?.card;
  const platforms = PLATFORMS_OF(datasets);
  const ws = sug?.workspace;

  const chips: { text: string; sub?: string }[] = sug
    ? [
      ...(sug.plan ?? []).map((s: string) => ({ text: s, sub: lastCard ? `Plan v${lastCard.version}` : undefined })),
      ...(sug.why ?? []).map((s: string) => ({ text: s, sub: lastCard ? `Plan v${lastCard.version}` : undefined })),
      ...(sug.data ?? []).map((s: string) => ({ text: s })),
    ].slice(0, 6)
    : [];

  return (
    <main className="content" ref={scroller} style={{ padding: "0 var(--page-margin)" }}
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        if (atBottom.current) setUnseen(0);
      }}>
      <div className="ask">
        <div role="log" aria-label="Conversation" className="log">
          {ws?.has_data && (
            <p className="t-small muted" style={{ textAlign: "center" }}>
              {ws.datasets} files · data {ws.date_from} to {ws.as_of} · {fmt(ws.skus)} SKUs · {ws.cities?.length} cities · {ws.platforms?.length} platforms
            </p>
          )}
          {!ready && (
            <Banner tone="warn">
              <span className="row between wrap" style={{ width: "100%" }}>
                <span>{datasets.length ? "No file in this chat has a saved mapping yet. Planning opens when a sales-order file is mapped." : "Import and map a sales-order file to ask about it or plan a month."}</span>
                <Button size="sm" onClick={onGoMap}>Fix the mapping</Button>
              </span>
            </Banner>
          )}
          {ready && Boolean(crosswalkCount) && (
            <Banner tone="info">
              <span className="row between wrap" style={{ width: "100%" }}>
                <span>{crosswalkCount} platform items need a SKU match. Lines without one are left out of the plan.</span>
                <Button size="sm" onClick={onGoCrosswalk}>Open Crosswalk</Button>
              </span>
            </Banner>
          )}

          {empty && ready && (
            <div className="blank" style={{ padding: "64px 0 24px" }}>
              <span className="t-lg">Ask about your sales, or plan a month.</span>
              <Button variant="primary" size="sm" onClick={() => ask("Create a demand plan for next month")}>Plan next month</Button>
            </div>
          )}

          {turns.map((t, i) => t.role === "user" ? (
            <div key={i} className="ububble">{t.text}</div>
          ) : (
            <div key={i} className="stack g16">
              {t.runId && <RunCard runId={t.runId} platforms={platforms} done={Boolean(t.card)}
                onCard={(card) => setTurns((ts) => ts.map((x, j) => (j === i && !x.card ? { ...x, card } : x)))}
                onFailed={(f) => setTurns((ts) => ts.map((x, j) => (j === i && !x.failed ? { ...x, failed: f } : x)))}
                failed={t.failed}
                onRetry={async () => { const r = await api.retry(t.failed!.run_id); setTurns((ts) => ts.map((x, j) => (j === i ? { ...x, runId: r.run_id, failed: undefined } : x))); }} />}
              {t.card && <ReportCard card={t.card} onOpen={() => onOpenReport(t.card!.run_id)} />}
              {(t.text || t.error || (busy && i === turns.length - 1) || t.evidence.length > 0) && (
                <div className="amsg">
                  <span className="ai-label">✦ Assistant{busy && i === turns.length - 1 ? " · writing" : ""}</span>
                  {(t.text || (busy && i === turns.length - 1)) && <div className="text">{t.text}{busy && i === turns.length - 1 && <span className="caret" aria-hidden="true" />}</div>}
                  {t.tools.length > 0 && busy && i === turns.length - 1 && <span className="t-small muted">{t.tools[t.tools.length - 1]}</span>}
                  {t.error && (
                    <Banner tone="crit" role="alert">
                      <span className="stack g8">
                        <span>The reply stopped partway. Nothing was lost.</span>
                        <span className="row">
                          <Button size="sm" variant="primary" icon={RotateCcw} onClick={() => retryLast(i)}>Retry</Button>
                          <Button size="sm" icon={Copy} onClick={() => navigator.clipboard?.writeText(t.error!)}>Copy error details</Button>
                        </span>
                      </span>
                    </Banner>
                  )}
                  {t.evidence.map((ev, j) => <Evidence key={j} ev={ev} />)}
                </div>
              )}
            </div>
          ))}

          {chips.length > 0 && !busy && (
            <div className="sugg" style={{ marginTop: empty ? 0 : "auto" }} aria-label="Suggested questions">
              {chips.map((c) => (
                <button key={c.text} type="button" onClick={() => ask(c.text)}>{c.text}{c.sub && <span className="sub">{c.sub}</span>}</button>
              ))}
            </div>
          )}
          <div ref={end} />
        </div>

        <div className="dock">
          {unseen > 0 && (
            <div className="row" style={{ justifyContent: "center", marginBottom: 8 }}>
              <button type="button" className="tag" style={{ background: "var(--surface)", height: 28, padding: "0 10px", boxShadow: "var(--shadow-card)" }}
                onClick={() => { end.current?.scrollIntoView({ behavior: "smooth" }); setUnseen(0); }}><ChevronDown size={12} aria-hidden="true" />New messages</button>
            </div>
          )}
          <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
            <label className="grow" style={{ display: "flex" }}>
              <span className="sr-only">Message</span>
              <textarea rows={1} value={q} disabled={!ready} placeholder={ready ? "Ask about the plan, or plan another month" : "Map a sales-order file first"}
                onChange={(e) => { setQ(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q); } }} />
            </label>
            <button type="submit" className="sendbtn" aria-label="Send" disabled={busy || !q.trim() || !ready}><ArrowUp size={16} aria-hidden="true" /></button>
          </form>
          <p className="t-caption muted" style={{ marginTop: 6 }}>{busy ? "Working on the reply…" : "Enter sends · Shift+Enter adds a line"}</p>
        </div>
      </div>
    </main>
  );
}

function RunCard({ runId, platforms, done, failed, onCard, onFailed, onRetry }: {
  runId: string; platforms: string[]; done: boolean; failed?: Json;
  onCard?: (c: Json) => void; onFailed?: (f: Json) => void; onRetry: () => void;
}) {
  const [steps, setSteps] = useState<Record<number, Json>>({});
  const [agent, setAgent] = useState<Json | null>(null);
  const [run, setRun] = useState<Json | null>(null);
  const [status, setStatus] = useState<string>(done ? "done" : "running");
  const [open, setOpen] = useState(!done);
  const started = useRef(Date.now());
  const [, tick] = useState(0);

  useEffect(() => {
    if (done) { setStatus("done"); setOpen(false); }
  }, [done]);
  useEffect(() => {
    api.run(runId).then(setRun).catch(() => undefined);
    if (done) return;
    const ctl = new AbortController();
    stream(`/runs/${runId}/events`, null, (e) => {
      if (e.type === "step") setSteps((s) => ({ ...s, [e.step_no]: e }));
      else if (e.type === "agent_progress") setAgent(e);
      else if (e.type === "report_card") { setStatus("done"); setOpen(false); onCard?.(e); }
      else if (e.type === "run_failed") { setStatus("failed"); onFailed?.(e); }
    }, ctl.signal).catch(() => undefined);
    const t = setInterval(() => tick((x) => x + 1), 1000);
    return () => { ctl.abort(); clearInterval(t); };
  }, [runId]); // eslint-disable-line

  const st = failed ? (failed.status === "cancelled" || /cancel/i.test(failed.message ?? "") ? "stopped" : "failed") : status;
  const bySection = SECTIONS.map((name) => {
    const list = Object.values(steps).filter((s) => s.section === name);
    const statuses = list.map((s) => s.status);
    const state = statuses.includes("failed") ? "failed"
      : list.length && statuses.every((x) => ["succeeded", "reused", "skipped"].includes(x)) ? "done"
      : statuses.some((x) => x !== "pending") ? "running" : "pending";
    return { name, state, summary: list.map((s) => s.summary).filter(Boolean).join(" · "), warnings: list.flatMap((s) => s.warnings ?? []) };
  });
  const doneN = bySection.filter((s) => s.state === "done").length;
  const queued = st === "running" && !Object.keys(steps).length;
  const elapsed = Math.round((Date.now() - started.current) / 1000);
  const dur = run?.finished_at && run?.started_at ? Math.round((+new Date(run.finished_at) - +new Date(run.started_at)) / 1000) : null;
  const title = `Plan ${run?.target_month ? monthLabel(run.target_month) : "next month"}${platforms.length ? ` · ${platforms.length} platform${platforms.length > 1 ? "s" : ""}` : ""}`;
  const stopStage = bySection.find((s) => s.state === "failed" || s.state === "running")?.name ?? failed?.step;

  return (
    <section className="card" style={{ padding: st === "done" && !open ? "10px 16px" : 16 }}>
      <div className="row between">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="row strong" style={{ border: 0, background: "transparent", padding: 0 }}>
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}{title}{st === "stopped" ? " · stopped" : ""}
        </button>
        {st === "done" && <StateChip tone="good">Succeeded{dur != null ? ` · ${dur < 90 ? `${dur} s` : `${Math.round(dur / 60)} min`}` : ""}</StateChip>}
        {st === "running" && <span className="row t-small"><StateChip tone="reading">{queued ? "Queued" : "Running"}</StateChip></span>}
        {st === "failed" && <StateChip tone="crit">Failed</StateChip>}
        {st === "stopped" && <StateChip tone="warn">Stopped</StateChip>}
      </div>
      {(st === "failed" || st === "stopped") && (
        <div className="stack g8" style={{ marginTop: 12 }}>
          <span>{st === "stopped" ? `Stopped${stopStage ? ` at ${stopStage}` : ""}. Nothing was saved as a plan.` : `The run stopped${stopStage ? ` at ${stopStage}` : ""}. Your files and mapping are saved.`}{failed?.message && !/^cancelled$/i.test(failed.message) ? ` ${failed.message}` : ""}</span>
          <span className="row end">
            {st === "failed" && <Button size="sm" icon={Copy} onClick={() => navigator.clipboard?.writeText(JSON.stringify(failed))}>Copy error details</Button>}
            <Button size="sm" variant="primary" icon={RotateCcw} onClick={onRetry}>Retry run</Button>
          </span>
        </div>
      )}
      {open && st !== "failed" && st !== "stopped" && (
        <>
          <ol className="wf-steps" style={{ marginTop: 12 }}>
            {bySection.map((s) => (
              <li key={s.name}>
                <span className={`wf-node ${st === "done" ? "done" : s.state}`} aria-hidden="true" />
                <span className="stack" style={{ gap: 0 }}>
                  <span className={s.state === "running" ? "strong" : s.state === "pending" && st !== "done" ? "muted" : undefined}>{s.name}</span>
                  {s.summary && <span className="t-small muted">{s.summary}</span>}
                  {s.warnings.slice(0, 3).map((w: string) => <span key={w} className="t-small"><span className="risk" aria-hidden="true">▲ </span>{w.length > 220 ? w.slice(0, 220) + "…" : w}</span>)}
                </span>
              </li>
            ))}
          </ol>
          {st === "running" && (
            <div className="row between" style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
              <span className="t-small muted">{queued ? "Queued · waiting for a worker" : `Step ${Math.min(doneN + 1, 7)} of 7 · ${elapsed} s`}{agent ? ` · ${agent.label}` : ""} · You can close this tab</span>
              <Button size="sm" onClick={() => api.cancel(runId)}>Stop</Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ReportCard({ card, onOpen }: { card: Json; onOpen: () => void }) {
  const [exporting, setExporting] = useState(false);
  const warnings: string[] = card.warnings ?? [];
  const exp = async () => {
    setExporting(true);
    try { await download(`/runs/${card.run_id}/export/plan.xlsx`, `demand-plan-${card.target_month.slice(0, 7)}-v${card.version}.xlsx`); } finally { setExporting(false); }
  };
  return (
    <section className="card pad stack g12">
      <div className="row between" style={{ alignItems: "baseline" }}>
        <span className="h-section">Order plan · {monthLabel(card.target_month)}</span>
        <span className="t-caption muted">Version {card.version}{card.version === 0 ? " · pipeline original" : ""} · run {String(card.run_id).slice(0, 8)}</span>
      </div>
      {warnings.length > 0 && (
        <div role="status" className="banner warn" style={{ flexDirection: "column", gap: 6 }}>
          <span className="row strong" style={{ gap: 6 }}><span className="risk" aria-hidden="true">▲</span>{warnings.length} validation warning{warnings.length > 1 ? "s" : ""}, shown in full</span>
          {warnings.map((w) => <span key={w}>{w}</span>)}
        </div>
      )}
      <div className="kv">
        <Metric label="Month planned" value={monthLabel(card.target_month)} />
        <Metric label="Demand plan, units" value={fmt(card.dp_total)} note={card.change_pct == null ? undefined : `${card.change_pct > 0 ? "+" : ""}${card.change_pct}% vs last month's sell-in`} />
        <Metric label="Order plan, units" value={card.order_total == null ? "—" : fmt(card.order_total)} note={card.order_total == null ? "Needs stock data" : undefined} />
        <Metric label="Lines needing review" value={`${fmt(card.review)} of ${fmt(card.lines)}`} note={card.agent_fallback ? `${fmt(card.agent_fallback)} agent fallback` : undefined} />
      </div>
      {card.overview && <p className="t-lg">{card.overview}</p>}
      <div className="row end">
        <Button icon={Download} busy={exporting} onClick={exp}>Export</Button>
        <Button variant="primary" iconRight={ArrowUpRight} onClick={onOpen}>Open report</Button>
      </div>
    </section>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <span className="stack" style={{ gap: 2 }}>
      <span className="t-caption muted">{label}</span>
      <span className="metric">{value}</span>
      {note && <span className="t-caption muted">{note}</span>}
    </span>
  );
}

function Evidence({ ev }: { ev: Json }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const rows: Json[] = ev.rows ?? [];
  const cols = rows.length ? Object.keys(rows[0]).filter((c) => !["bridge", "applied_factors"].includes(c)).slice(0, 8) : [];
  const shown = all ? rows : rows.slice(0, 10);
  const qname = /^[a-z_]+$/.test(ev.query ?? "") ? ev.query : "sql";
  return (
    <div className="evid">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <span className="tag q"><Database size={12} aria-hidden="true" />Query · {qname}</span>
        <span className="grow ellipsis" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ev.label}</span>
        <span className="t-caption muted">{fmt(ev.row_count)} rows</span>
      </button>
      {open && rows.length > 0 && (
        <div className="body">
          <div className="tblwrap" style={{ maxHeight: 320 }}>
            <table className="tbl compact">
              <thead><tr>{cols.map((c) => <th key={c} className={typeof rows[0][c] === "number" ? "num" : undefined}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
              <tbody>{shown.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : undefined}>{typeof r[c] === "number" ? fmt(r[c], 2) : r[c] == null || r[c] === "" ? "—" : String(r[c])}</td>)}</tr>)}</tbody>
            </table>
          </div>
          <div className="card-foot line">
            <span>Showing {shown.length} of {fmt(ev.row_count ?? rows.length)} rows{!all && rows.length > 10 ? <> · <button type="button" className="linkbtn" onClick={() => setAll(true)}>Show all</button></> : null}</span>
          </div>
        </div>
      )}
    </div>
  );
}
