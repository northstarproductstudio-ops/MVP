import type { Tone } from "./ui";

export type Stage = "import" | "map" | "ask";

export const fmt = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : Number(n).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 }).replace(/^-/, "−");

export const signed = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n) ? "—" : `${n > 0 ? "+" : ""}${fmt(n, digits)}`;

export const pct = (m: number | null | undefined) =>
  m === null || m === undefined || Number.isNaN(m) ? "—" : signed(Math.round(m * 1000) / 10, 1);

export const bytes = (n?: number) =>
  !n ? "" : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

export const monthLabel = (d?: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { month: "long", year: "numeric" }) : "—";

export const dateLabel = (d?: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—";

// Mapping states as stored by the API, drawn as the design's state chips.
export const STATE_LABEL: Record<string, [string, Tone]> = {
  recognised: ["Confirmed", "good"],
  confirmed: ["Confirmed", "good"],
  proposed: ["Proposed", "proposed"],
  new_category: ["Proposed · new category", "proposed"],
  needs_decision: ["Needs a decision", "warn"],
  not_stored: ["Ignored", "ignored"],
  custom: ["Custom, added by you", "custom"],
};

export const DATASET_STATUS: Record<string, [string, Tone]> = {
  uploaded: ["Queued", "reading"],
  parsing: ["Reading rows", "reading"],
  parsed: ["Ready to map", "good"],
  mapped: ["Storing fields", "reading"],
  ready: ["Mapped", "good"],
  failed: ["Not imported", "crit"],
  duplicate: ["Already imported", "warn"],
};

export const PLATFORMS_OF = (datasets: { file_type?: string; file_name?: string }[]) => {
  const names = new Set<string>();
  datasets.forEach((d) => {
    const s = `${d.file_type ?? ""} ${d.file_name ?? ""}`.toLowerCase();
    if (s.includes("blinkit")) names.add("Blinkit");
    if (s.includes("zepto")) names.add("Zepto");
    if (s.includes("bigbasket") || s.includes("bb_")) names.add("BigBasket");
  });
  return Array.from(names);
};
