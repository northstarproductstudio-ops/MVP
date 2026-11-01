// Pipeline: the demand_plan v1 steps (worker/steps) as data-flow diagrams — inputs on the left,
// the mathematical operations in between, each result on the right — joined step to step.
// Read-only; constants are the default plan config (config/demand_plan.yaml), a workspace may pin others.
import { useMemo, useState } from "react";
import { ArrowDown, ArrowRight, ChevronDown, ChevronRight, Database, Info, Layers } from "lucide-react";
import { Button } from "../components/ui";

type N =
  | { k: "data"; v: string; src?: string } // a column from an imported table or an earlier step
  | { k: "ref"; v: string } // a result worked out earlier in the same step
  | { k: "const"; v: string; key?: string } // a plan-config number
  | { k: "lit"; v: string } // a label value
  | { k: "op"; v: string; c: N[] } // + − × ÷ ^ > …
  | { k: "fn"; v: string; c: N[]; hint?: string }; // round, max, Σ, …

type Formula = { out: string; tree: N; when?: string; note?: string; rules?: [string, string][] };
type Step = {
  no: number; name: string; title: string; section: string; agent?: boolean; what: string;
  reads: string[]; out: string[]; saves?: string[]; formulas: Formula[];
  checks?: { stop: string[]; warn: string[] }; off?: string;
};

const d = (v: string, src?: string): N => ({ k: "data", v, src });
const r = (v: string): N => ({ k: "ref", v });
const k = (v: string, key?: string): N => ({ k: "const", v, key });
const op = (v: string, ...c: N[]): N => ({ k: "op", v, c });
const fn = (v: string, c: N[], hint?: string): N => ({ k: "fn", v, c, hint });

// tables filled by Import; everything else is made by a step
const RAW = new Set(["sellin_line", "po_line", "stock_snapshot", "market_daily", "sku_crosswalk", "sku_master"]);

// the plan's reference arithmetic, shared by steps 7 and 9
const dpTree = (m: N, a: N) => fn("max", [k("0"), fn("round", [op("+", op("×", d("base", "base"), fn("Π", [op("+", k("1"), m)])), fn("Σ", [a]))])]);

const STEPS: Step[] = [
  {
    no: 1, name: "validate_inputs", title: "Validate inputs", section: "Reading imported data",
    what: "Checks the imported tables. A hard problem stops the run; a missing optional file switches off the steps that need it.",
    reads: ["sellin_line", "po_line", "stock_snapshot", "market_daily", "sku_crosswalk", "sku_master"], out: ["inputs"],
    formulas: [
      { out: "usable lines", tree: fn("filter", [d("rows", "sellin_line"), op("∈", d("status", "sellin_line"), k("{invoiced, confirmed}", "history.sellin_statuses")), fn("not", [d("is_internal", "sellin_line")])]) },
      { out: "months_present", tree: fn("count months", [d("order_date", "sellin_line")], "usable lines") },
    ],
    checks: {
      stop: ["Order date, SKU, customer and quantity invoiced are mapped", "Every order date parses", "No duplicate sales-order lines",
        "No negative quantities", "SKUs with sell-in have confirmed crosswalk rows"],
      warn: ["No PO file: fill-rate factor off", "No stock file: stock cover and netting off", "Stock snapshot older than 14 days: flagged stale",
        "No market file: sell-through, OSA and market ceiling off", "Fewer than 3 months: base uses the months present"],
    },
  },
  {
    no: 2, name: "build_history", title: "Build history", section: "Cleaning demand data",
    what: "Turns usable sales-order lines into units and value per SKU, city, platform and month, plus weekly national units.",
    reads: ["sellin_line", "sku_master"], out: ["history", "weekly"],
    formulas: [
      { out: "q", tree: op("+", d("qty_invoiced", "sellin_line"), d("qty_cancelled", "sellin_line")), note: "qty_ordered is used when quantity invoiced is not mapped." },
      { out: "price", tree: fn("if", [op(">", d("item_price", "sellin_line"), k("0")), d("item_price", "sellin_line"), d("mrp", "sellin_line")]), note: "if(condition, then, else)" },
      { out: "value", tree: op("×", r("q"), r("price")) },
      { out: "history", tree: fn("Σ", [r("q"), r("value")], "per SKU × city × platform × month") },
      { out: "weekly", tree: fn("Σ", [r("q")], "per SKU × platform × week") },
    ],
  },
  {
    no: 3, name: "correct_oos", title: "Correct for availability", section: "Correcting for availability",
    what: "Measures on-shelf availability (OSA) from own-brand market data. A signal for later steps; sell-in is not corrected in v1.",
    reads: ["market_daily"], out: ["oos"], saves: ["variable_value"],
    formulas: [
      { out: "osa_28d", tree: fn("mean", [d("osa_pct", "market_daily"), k("28", "market.osa_window_days")], "last N days, own brand") },
      { out: "osa_min_7d", tree: fn("min", [fn("daily mean", [d("osa_pct", "market_daily")]), k("7", "market.osa_min_window_days")], "last N days") },
      { out: "competitor_osa", tree: fn("median", [d("osa_pct", "market_daily")], "other brands, per platform × category") },
    ],
    off: "Skipped when there is no market file.",
  },
  {
    no: 4, name: "classify_skus", title: "Classify SKUs", section: "Classifying demand patterns",
    what: "ABC class from sell-in value over the history window; Syntetos-Boylan demand pattern from weekly units.",
    reads: ["history", "weekly"], out: ["classes"], saves: ["variable_value"],
    formulas: [
      { out: "share_before", tree: op("÷", fn("running Σ", [d("value", "history")], "largest SKU first"), fn("Σ", [d("value", "history")])) },
      { out: "abc", tree: fn("band", [r("share_before"), k("0.80", "abc.cutoffs"), k("0.95", "abc.cutoffs")]),
        rules: [["A", "share_before < 0.80"], ["B", "share_before < 0.95"], ["C", "otherwise"]] },
      { out: "ADI", tree: op("÷", d("weeks", "weekly"), d("weeks with sales", "weekly")) },
      { out: "CV²", tree: op("^", op("÷", fn("std", [d("q", "weekly")]), fn("mean", [d("q", "weekly")])), k("2")), note: "Over the weeks with sales." },
      { out: "sb_class", tree: fn("classify", [r("ADI"), k("1.32", "sb.adi"), r("CV²"), k("0.49", "sb.cv2")]),
        rules: [["smooth", "ADI < 1.32 and CV² < 0.49"], ["erratic", "ADI < 1.32 and CV² ≥ 0.49"], ["intermittent", "ADI ≥ 1.32 and CV² < 0.49"],
          ["lumpy", "ADI ≥ 1.32 and CV² ≥ 0.49"], ["new", "first sale within the last 8 weeks"]] },
    ],
  },
  {
    no: 5, name: "base_forecast", title: "Base forecast", section: "Fixing accuracy baseline",
    what: "National base demand per SKU × platform by demand pattern, then split to cities by each city's weighted share.",
    reads: ["history", "weekly", "classes", "sku_master"], out: ["base"], saves: ["variable_value"],
    formulas: [
      { out: "B", when: "smooth or erratic", tree: op("+", op("×", k("0.5", "base.weights"), d("q(M1)", "history")), op("×", k("0.3", "base.weights"), d("q(M2)", "history")), op("×", k("0.2", "base.weights"), d("q(M3)", "history"))),
        note: "Weights are re-normalised over the months present." },
      { out: "B", when: "new", tree: fn("mean", [d("q, months sold", "history")]) },
      { out: "B", when: "intermittent or lumpy", tree: op("÷", op("×", fn("Croston-SBA", [d("q", "weekly"), k("0.1", "base.croston_alpha")]), d("days in month")), k("7")) },
      { out: "share", tree: op("÷", d("weighted q in city", "history"), d("weighted national q", "history")),
        note: "Below 100 weighted units the category's city split is used, then the platform's." },
      { out: "base", tree: op("×", r("B"), r("share")) },
    ],
  },
  {
    no: 6, name: "derive_factors", title: "Derive factors", section: "Generating the forecast",
    what: "Python factors per line — multipliers m and additive units a — and the signals the planning agent reads.",
    reads: ["history", "classes", "base", "oos", "inputs", "po_line", "stock_snapshot", "market_daily"], out: ["factors", "signals"],
    saves: ["factor", "variable_value"],
    formulas: [
      { out: "trend", tree: fn("clip", [op("−", op("^", op("÷", d("q(M1)", "history"), d("q(M3)", "history")), op("÷", d("months ahead"), k("2"))), k("1")), k("−0.20", "trend.cap"), k("+0.20", "trend.cap")]),
        note: "Not for intermittent, lumpy or new SKUs." },
      { out: "units", tree: op("÷", d("sales_sp", "market_daily"), d("sp", "market_daily")), note: "sales_mrp ÷ mrp where selling-price sales are absent (Zepto)." },
      { out: "S", tree: fn("Σ", [op("÷", r("units"), fn("max", [op("÷", d("osa_pct", "market_daily"), k("100")), k("0.30", "sellthrough.osa_floor")]))], "own brand, month M1") },
      { out: "sell_through", tree: fn("clip", [op("−", op("÷", r("S"), d("q(M1)", "history")), k("1")), k("−0.15", "sellthrough.cap"), k("+0.15", "sellthrough.cap")]),
        note: "Quick-commerce platforms only: blinkit, instamart, zepto." },
      { out: "fill", tree: op("÷", fn("Σ", [d("qty_received", "po_line")]), fn("Σ", [d("qty_ordered", "po_line")])), note: "Completed POs in the last 60 days." },
      { out: "fill_rate", when: "fill < 0.95, else 0", tree: fn("min", [k("0.15", "fill.cap"), op("−", op("÷", k("1"), r("fill")), k("1"))]) },
      { out: "doh", tree: op("÷", d("soh_units", "stock_snapshot"), d("run_rate", "stock_snapshot")) },
      { out: "stock_cover", tree: fn("max", [op("×", op("−", k("21", "stock.target_doh"), r("doh")), d("run_rate", "stock_snapshot")), op("−", d("base", "base"))]),
        note: "Additive units, Blinkit lines only. The seasonal index is off: it needs 12 months of history." },
    ],
  },
  {
    no: 7, name: "build_evidence_pack", title: "Build evidence pack", section: "Generating the forecast",
    what: "The reference demand plan per line from the Python factors, and one evidence pack per SKU for the planning agent.",
    reads: ["history", "classes", "base", "factors", "signals"], out: ["evidence_pack", "reference"],
    formulas: [
      { out: "reference_dp", tree: dpTree(d("m", "factors"), d("a", "factors")), note: "m = trend, sell_through, fill_rate; a = stock_cover." },
      { out: "evidence_pack", tree: fn("pack per SKU", [d("history"), d("classes"), d("base"), d("factors"), d("signals"), r("reference_dp")]) },
    ],
  },
  {
    no: 8, name: "agent_plan", title: "Planning agent", section: "Generating the forecast", agent: true,
    what: "One structured model call per SKU proposes agent factors and a judgment for every line, with a reason.",
    reads: ["evidence_pack"], out: ["agent_raw"],
    formulas: [
      { out: "agent_raw", tree: fn("LLM", [d("evidence_pack"), k("40", "agent.max_lines_per_call"), k("5", "agent.parallel_calls")], "pack, lines per call, calls at once"),
        rules: [["agent factor", "between −0.30 and +0.50"], ["judgment", "within ±0.10"], ["failing SKU", "retried once"]] },
    ],
    off: "Runs only when the planning agent is on in Settings. When it is off, step 9 uses the reference DP.",
  },
  {
    no: 9, name: "validate_agent_output", title: "Validate agent output", section: "Building the plan",
    what: "Re-checks each agent answer; lines that fail fall back to the reference DP. Writes the factor bridge and the market-ceiling flag.",
    reads: ["evidence_pack", "reference", "agent_raw", "classes", "base", "market_daily", "factors"], out: ["plan_lines"], saves: ["factor"],
    formulas: [
      { out: "final_dp", when: "the agent answer passes its checks", tree: dpTree(d("m applied"), d("a applied")),
        note: "Otherwise final_dp = reference_dp and the line is flagged agent_fallback." },
      { out: "implied_share", tree: op("÷", op("×", fn("Σ", [r("final_dp")]), d("price", "market_daily")), d("category value M1", "market_daily")) },
      { out: "market_ceiling", tree: op(">", r("implied_share"), op("×", k("1.5", "market.ceiling_ratio"), d("current share", "market_daily"))),
        note: "A flag only; it never changes a number." },
    ],
  },
  {
    no: 10, name: "net_and_round", title: "Net and round", section: "Building the plan",
    what: "Turns the demand plan into order quantities: safety stock, minus stock and open POs, rounded up to cases.",
    reads: ["plan_lines", "stock_snapshot", "po_line", "sku_master", "inputs"], out: ["netted"],
    formulas: [
      { out: "safety_stock", tree: op("×", op("÷", d("final_dp", "plan_lines"), d("days in month")), d("cover days[abc][class]")) },
      { out: "order_qty", tree: op("×", fn("ceil", [op("÷", fn("max", [k("0"), op("−", op("−", op("+", d("final_dp", "plan_lines"), r("safety_stock")), d("soh_units", "stock_snapshot")), d("qty_open", "po_line"))]), d("case_size", "sku_master"))]), d("case_size", "sku_master")),
        note: "Raised to the SKU's MOQ when below it (flag moq_raised)." },
    ],
    off: "Off in the default config (netting.enabled is false and safety.cover_days is empty), so order quantity stays empty.",
  },
  {
    no: 11, name: "build_report", title: "Build report", section: "Building the plan",
    what: "Saves the plan lines and report version 0, totals the run and writes the plan overview.",
    reads: ["netted", "history", "factors", "base"], out: ["dp_output", "report"], saves: ["report_version"],
    formulas: [
      { out: "total_dp", tree: fn("Σ", [d("final_dp", "netted")]) },
      { out: "overview", tree: fn("LLM", [d("totals"), d("factor counts"), d("flag counts")]), note: "A template overview is used when the model call fails." },
    ],
  },
];

const PRODUCER: Record<string, number> = Object.fromEntries(STEPS.flatMap((s) => s.out.map((x) => [x, s.no])));

// ---- equation text --------------------------------------------------------------------------
const PREC: Record<string, number> = { ">": 1, "<": 1, "∈": 1, "+": 2, "−": 2, "×": 3, "÷": 3, "^": 4 };

function equation(n: N, outer = 0): string {
  if (n.k === "fn") return `${n.v}(${n.c.map((x) => equation(x)).join(", ")})`;
  if (n.k !== "op") return n.v;
  const p = PREC[n.v] ?? 2;
  const s = n.c.length === 1 ? `${n.v}${equation(n.c[0], 5)}` : n.c.map((x, i) => equation(x, i ? p + 0.5 : p)).join(` ${n.v} `);
  return p < outer ? `(${s})` : s;
}

// ---- diagram layout: inputs in column 0, each operation as soon as its inputs exist, result last ----
const ROW = 44;
const GAP = 34;
const OP = 30;
let measure: CanvasRenderingContext2D | null = null;

function textW(s: string, px: number): number {
  measure ??= document.createElement("canvas").getContext("2d");
  if (!measure) return s.length * px * 0.62;
  measure.font = `${px}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  return measure.measureText(s).width;
}

function nodeW(n: N): number {
  if (n.k === "op") return OP;
  if (n.k === "fn") return Math.ceil(Math.max(textW(n.v, 12), n.hint ? textW(n.hint, 10) : 0) + 22);
  if (n.k === "data" && n.src) return Math.ceil(textW(n.src, 10) + 5 + textW(n.v, 12) + 22);
  return Math.ceil(textW(n.v, 12) + 22);
}

const depth = (n: N): number => (n.k === "op" || n.k === "fn" ? 1 + Math.max(...n.c.map(depth)) : 0);

type Box = { n: N | { k: "out"; v: string }; x: number; y: number; w: number };
type Edge = { x1: number; y1: number; x2: number; y2: number };

function layout(f: Formula) {
  const top = depth(f.tree);
  const colW: number[] = Array(top + 2).fill(0);
  let rows = 0;
  type T = { n: N; col: number; row: number; w: number; kids: T[] };
  const build = (n: N): T => {
    if (n.k === "op" || n.k === "fn") {
      const kids = n.c.map(build);
      const t = { n, col: depth(n), row: (kids[0].row + kids[kids.length - 1].row) / 2, w: nodeW(n), kids };
      colW[t.col] = Math.max(colW[t.col], t.w);
      return t;
    }
    const t = { n, col: 0, row: rows++, w: nodeW(n), kids: [] };
    colW[0] = Math.max(colW[0], t.w);
    return t;
  };
  const root = build(f.tree);
  const outW = Math.ceil(textW(f.out, 12) + 24);
  colW[top + 1] = outW;
  const colX: number[] = [];
  colW.reduce((x, w, i) => { colX[i] = x; return x + w + GAP; }, 0);
  const boxes: Box[] = [];
  const edges: Edge[] = [];
  const place = (t: T): Box => {
    const x = t.col === 0 ? colX[0] + colW[0] - t.w : colX[t.col] + (colW[t.col] - t.w) / 2;
    const b = { n: t.n, x, y: t.row * ROW + ROW / 2, w: t.w };
    boxes.push(b);
    for (const kid of t.kids.map(place)) edges.push({ x1: kid.x + kid.w, y1: kid.y, x2: b.x, y2: b.y });
    return b;
  };
  const rb = place(root);
  const out = { n: { k: "out" as const, v: f.out }, x: colX[top + 1], y: rb.y, w: outW };
  boxes.push(out);
  edges.push({ x1: rb.x + rb.w, y1: rb.y, x2: out.x, y2: out.y });
  return { boxes, edges, width: colX[top + 1] + outW, height: rows * ROW };
}

// ---- screen ---------------------------------------------------------------------------------
export function PipelineScreen() {
  const [closed, setClosed] = useState<Set<number>>(new Set());
  const toggle = (n: number) => setClosed((cur) => { const x = new Set(cur); x.has(n) ? x.delete(n) : x.add(n); return x; });
  const jump = (n: number) => document.getElementById(`step-${n}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const allClosed = closed.size === STEPS.length;

  return (
    <main className="pipe">
      <aside className="pipe-palette" aria-label="Legend and steps">
        <span className="caption">Legend</span>
        <ul className="pipe-legend">
          <li><span className="dnode data raw"><span className="dsrc">table</span>column</span><span className="t-caption muted">Imported data</span></li>
          <li><span className="dnode data"><span className="dsrc">step</span>column</span><span className="t-caption muted">Data from an earlier step</span></li>
          <li><span className="dnode ref">result</span><span className="t-caption muted">Result from this step</span></li>
          <li><span className="dnode const">0.5</span><span className="t-caption muted">Constant (plan config)</span></li>
          <li><span className="row" style={{ gap: 6 }}>{["+", "−", "×", "÷"].map((x) => <span key={x} className="dnode op">{x}</span>)}</span><span className="t-caption muted">Add, subtract, multiply, divide</span></li>
          <li><span className="dnode fn">round</span><span className="t-caption muted">Function</span></li>
          <li><span className="dnode out">output</span><span className="t-caption muted">What the formula produces</span></li>
        </ul>
        <span className="caption">Steps</span>
        <ol className="pipe-outline">
          {STEPS.map((s) => (
            <li key={s.no}><button type="button" onClick={() => jump(s.no)}><span className="pnum sm">{s.no}</span><span className="grow">{s.title}</span>{s.agent && <span className="t-caption accent">LLM</span>}</button></li>
          ))}
        </ol>
      </aside>

      <div className="pipe-canvas">
        <div className="pipe-flow">
          <div className="pagehead">
            <div className="stack"><h1>Pipeline</h1>
              <p className="muted">How a plan is calculated. Each of the {STEPS.length} steps reads data on the left, applies the operations in between and hands its outputs to the next step.</p></div>
            <Button size="sm" icon={allClosed ? ChevronDown : ChevronRight} onClick={() => setClosed(allClosed ? new Set() : new Set(STEPS.map((s) => s.no)))}>
              {allClosed ? "Expand all" : "Collapse all"}
            </Button>
          </div>
          <div className="pwire start" aria-hidden="true"><span className="pwire-label"><Database size={12} aria-hidden="true" />Imported data</span></div>
          {STEPS.map((s, i) => (
            <div key={s.no}>
              <StepCard s={s} open={!closed.has(s.no)} onToggle={() => toggle(s.no)} next={STEPS[i + 1]} onJump={jump} />
              <div className="pwire" aria-hidden="true"><span className="pwire-label">{STEPS[i + 1] ? s.out.join(" · ") : "Plan report in the chat"}</span></div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}

function StepCard({ s, open, onToggle, next, onJump }: { s: Step; open: boolean; onToggle: () => void; next?: Step; onJump: (n: number) => void }) {
  return (
    <section id={`step-${s.no}`} className={`pnode ${s.agent ? "agent" : ""}`} aria-labelledby={`step-${s.no}-t`}>
      <span className="port in" aria-hidden="true" />
      <button type="button" className={`pnode-head ${open ? "open" : ""}`} onClick={onToggle} aria-expanded={open}>
        <span className="pnum">{s.no}</span>
        <span className="stack grow" style={{ gap: 2 }}>
          <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span id={`step-${s.no}-t`} className="strong">{s.title}</span>
            <span className={`ptag ${s.agent ? "agent" : ""}`}>{s.agent ? "Planning agent · LLM" : "Python"}</span>
          </span>
          <span className="t-small muted">{s.what}</span>
        </span>
        <span className="t-caption muted pnode-section">{s.section}</span>
        <ChevronDown size={16} aria-hidden="true" className={`pchev ${open ? "" : "shut"}`} />
      </button>
      {open && (
        <div className="pnode-body">
          <div className="pinputs">
            <span className="plabel">Inputs</span>
            {s.reads.map((x) => RAW.has(x)
              ? <span key={x} className="pchip raw" title="Imported table"><Database size={12} aria-hidden="true" />{x}</span>
              : <span key={x} className="pchip" title={`Made by step ${PRODUCER[x]}`}><Layers size={12} aria-hidden="true" />{x}<span className="pchip-from">step {PRODUCER[x]}</span></span>)}
          </div>
          {s.formulas.map((f, i) => <FormulaFlow key={i} f={f} />)}
          {s.checks && (
            <div className="pchecks">
              <div className="stack g8"><span className="plabel">Stops the run if not true</span>
                <ul>{s.checks.stop.map((x) => <li key={x} className="stop">{x}</li>)}</ul></div>
              <div className="stack g8"><span className="plabel">Warns and switches off</span>
                <ul>{s.checks.warn.map((x) => <li key={x}>{x}</li>)}</ul></div>
            </div>
          )}
          {s.off && <div className="banner info"><Info size={14} aria-hidden="true" /><span>{s.off}</span></div>}
        </div>
      )}
      <div className="pnode-foot">
        <span className="plabel">Outputs</span>
        {s.out.map((x) => <span key={x} className="dnode out sm">{x}</span>)}
        {s.saves && <span className="t-caption muted">also saved: {s.saves.join(", ")}</span>}
        <span className="grow" />
        {next ? (
          <>
            <span>Next</span><ArrowRight size={14} aria-hidden="true" />
            <button type="button" className="linkbtn" onClick={() => onJump(next.no)}>{next.no} · {next.title}</button>
          </>
        ) : <><span>End of run</span><ArrowDown size={14} aria-hidden="true" /><span>report opens in the chat</span></>}
      </div>
      <span className="port out" aria-hidden="true" />
    </section>
  );
}

function FormulaFlow({ f }: { f: Formula }) {
  const g = useMemo(() => layout(f), [f]);
  return (
    <div className="pflow">
      <div className="pflow-eq">
        <span className="mono"><span className="strong">{f.out}</span> = {equation(f.tree)}</span>
        {f.when && <span className="pwhen">when {f.when}</span>}
      </div>
      <div className="dflow-scroll">
        <div className="dflow" style={{ width: g.width, height: g.height }}>
          <svg width={g.width} height={g.height} aria-hidden="true">
            {g.edges.map((e, i) => {
              const mid = (e.x1 + e.x2) / 2;
              return (
                <g key={i}>
                  <path d={`M${e.x1} ${e.y1} C${mid} ${e.y1} ${mid} ${e.y2} ${e.x2 - 5} ${e.y2}`} />
                  <path className="tip" d={`M${e.x2 - 6} ${e.y2 - 3.5} L${e.x2} ${e.y2} L${e.x2 - 6} ${e.y2 + 3.5} Z`} />
                </g>
              );
            })}
          </svg>
          {g.boxes.map((b, i) => <FlowNode key={i} b={b} />)}
        </div>
      </div>
      {f.rules && (
        <div className="prules">
          {f.rules.map(([v, cond]) => <span key={v} className="prule"><span className="dnode lit sm">{v}</span><span className="t-small muted">{cond}</span></span>)}
        </div>
      )}
      {f.note && <p className="t-caption muted pflow-note">{f.note}</p>}
    </div>
  );
}

function FlowNode({ b }: { b: Box }) {
  const n = b.n;
  const style = { left: b.x, top: b.y, width: b.w };
  switch (n.k) {
    case "op": return <span className="dnode op" style={style} aria-hidden="true">{n.v}</span>;
    case "fn": return <span className={`dnode fn ${n.hint ? "hinted" : ""}`} style={style}>{n.v}{n.hint && <span className="dhint">{n.hint}</span>}</span>;
    case "data": return <span className={`dnode data ${n.src && RAW.has(n.src) ? "raw" : ""}`} style={style}>{n.src && <span className="dsrc">{n.src}</span>}{n.v}</span>;
    case "const": return <span className="dnode const" style={style} title={n.key ? `Plan config: ${n.key}` : "Constant"}>{n.v}</span>;
    default: return <span className={`dnode ${n.k}`} style={style}>{n.v}</span>;
  }
}
