// Thin client for the fmcg-api endpoints and SSE streams.
import { getToken } from "./auth";

export const API = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000";

export type Json = Record<string, any>;

async function req<T = Json>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  createSession: (title?: string) => req("/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  listSessions: () => req<Json[]>("/sessions"),
  session: (id: string) => req(`/sessions/${id}`),
  upload: (sid: string, files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f, f.name));
    return req(`/sessions/${sid}/imports`, { method: "POST", body: fd });
  },
  importStatus: (did: string) => req(`/imports/${did}`),
  mapping: (did: string) => req(`/imports/${did}/mapping`),
  saveMapping: (did: string, fields: Json[], note?: string) =>
    req(`/imports/${did}/mapping`, { method: "PUT", body: JSON.stringify({ fields, note }) }),
  setType: (did: string, file_type: string, custom_name?: string) =>
    req(`/imports/${did}/type`, { method: "PATCH", body: JSON.stringify({ file_type, custom_name }) }),
  suggestions: (sid: string) => req(`/sessions/${sid}/suggestions`),
  // Gap 10: what the plan needs (same rule the run uses)
  readiness: (sid: string) => req(`/sessions/${sid}/readiness`),
  // Gap 12: data management
  datasets: (sid: string) => req(`/sessions/${sid}/datasets`),
  replaceDataset: (did: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return req(`/datasets/${did}/replace`, { method: "POST", body: fd });
  },
  removeDataset: (did: string) => req(`/datasets/${did}`, { method: "DELETE" }),
  clearDatasets: (sid: string) => req(`/sessions/${sid}/datasets/clear`, { method: "POST" }),
  run: (rid: string) => req(`/runs/${rid}`),
  retry: (rid: string) => req(`/runs/${rid}/retry`, { method: "POST" }),
  cancel: (rid: string) => req(`/runs/${rid}/cancel`, { method: "POST" }),
  plan: (rid: string, page = 1, version?: number) =>
    req(`/runs/${rid}/plan?page=${page}&page_size=200${version !== undefined ? `&version=${version}` : ""}`),
  reasoning: (rid: string, page = 1, version?: number) =>
    req(`/runs/${rid}/reasoning?page=${page}&page_size=200${version !== undefined ? `&version=${version}` : ""}`),
  versions: (rid: string) => req<Json[]>(`/runs/${rid}/versions`),
  undo: (rid: string) => req(`/runs/${rid}/undo`, { method: "POST" }),
  crosswalk: (status?: string) => req(`/crosswalk${status ? `?status=${status}` : ""}`),
  confirmCrosswalk: (rows: Json[]) => req("/crosswalk", { method: "POST", body: JSON.stringify(rows) }),
  uploadCrosswalk: (file: File) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return req("/crosswalk", { method: "POST", body: fd });
  },
};

export async function download(path: string, filename: string) {
  const token = await getToken();
  const res = await fetch(`${API}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export type SseEvent = { id: string; type: string; [k: string]: any };

// SSE over fetch so we can POST and send the Authorization header; reconnects with Last-Event-ID.
export async function stream(
  path: string,
  body: Json | null,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let lastId = "";
  let failures = 0;
  let finished = false;
  const terminal = new Set(["report_card", "run_failed", "message_done"]);
  // AppSail closes responses at ~30 s, so GET streams are resumed until a terminal event arrives.
  while (!finished && failures < 5) {
    const token = await getToken();
    try {
      const res = await fetch(`${API}${path}`, {
        method: body ? "POST" : "GET",
        body: body ? JSON.stringify(body) : undefined,
        signal,
        headers: {
          Accept: "text/event-stream",
          ...(body ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(lastId ? { "Last-Event-ID": lastId } : {}),
        },
      });
      if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!data) continue;
          const ev = JSON.parse(data.slice(6)) as SseEvent;
          lastId = ev.id || lastId;
          if (terminal.has(ev.type)) finished = true;
          onEvent(ev);
        }
      }
      if (body) return; // never replay a POST (it would re-send the question)
      failures = 0;
    } catch (e) {
      if (signal?.aborted || body) throw e;
      failures++;
      await new Promise((r) => setTimeout(r, 1000 * failures));
    }
  }
}
