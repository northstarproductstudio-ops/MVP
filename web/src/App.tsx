// Shell and routes. A chat runs Import → Map → Plan (planning happens inside Ask); Saved data and
// Crosswalk are workspace pages; the report viewer is a full page opened from a report card.
import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, LogIn } from "lucide-react";
import { api, type Json } from "./api";
import { getToken, signIn, signOut, supabase, usesDevToken } from "./auth";
import type { Stage } from "./components/common";
import { Sidebar, StepRail, TopBar, useTheme, type NavId } from "./components/Shell";
import { Banner, Button, ErrorBlank, Loading } from "./components/ui";
import { AskScreen } from "./screens/AskScreen";
import { CrosswalkScreen } from "./screens/CrosswalkScreen";
import { ImportScreen } from "./screens/ImportScreen";
import { MappingScreen } from "./screens/MappingScreen";
import { ReportView } from "./screens/ReportView";
import { SavedDataScreen } from "./screens/SavedDataScreen";

const WORKSPACE = (import.meta.env.VITE_WORKSPACE_NAME as string) || "Workspace";

type Route =
  | { page: "chat"; sid: string; stage: Stage }
  | { page: "report"; sid: string; rid: string }
  | { page: "saved" }
  | { page: "crosswalk" }
  | { page: "home" };

function parse(): Route {
  const h = window.location.hash;
  let m = h.match(/^#\/s\/([0-9a-f-]+)\/report\/([0-9a-f-]+)/);
  if (m) return { page: "report", sid: m[1], rid: m[2] };
  m = h.match(/^#\/s\/([0-9a-f-]+)\/(import|map|ask)/);
  if (m) return { page: "chat", sid: m[1], stage: m[2] as Stage };
  if (h.startsWith("#/saved")) return { page: "saved" };
  if (h.startsWith("#/crosswalk")) return { page: "crosswalk" };
  return { page: "home" };
}

function useRoute(): [Route, (hash: string) => void] {
  const [r, setR] = useState<Route>(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [r, (hash) => { window.location.hash = hash; }];
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const theme = useTheme();
  useEffect(() => {
    getToken().then((t) => setAuthed(Boolean(t)));
    const sub = supabase?.auth.onAuthStateChange((_e, s) => setAuthed(Boolean(s) || usesDevToken));
    return () => sub?.data.subscription.unsubscribe();
  }, []);
  if (authed === null) return <div className="signin-page"><main className="signin"><Loading text="Checking your session." rows={2} /></main></div>;
  if (!authed) return <SignIn onDone={() => setAuthed(true)} />;
  return <Workspace theme={theme} />;
}

// S-01: idle, submitting (Cancel after 10 s), rejected, service unavailable.
function SignIn({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const cancelled = useRef(false);
  const unavailable = !supabase && !usesDevToken;
  const ref = "AUTH-CONFIG";

  const submit = async () => {
    setErr(null);
    setBusy(true);
    setSlow(false);
    cancelled.current = false;
    const t = setTimeout(() => setSlow(true), 10000);
    try {
      await signIn(email, pw);
      if (!cancelled.current) onDone();
    } catch (x: any) {
      if (!cancelled.current) setErr(/invalid/i.test(x.message) ? `${email || "This account"} couldn't be signed in. Check the email and password, or ask your admin to add you to this workspace.` : x.message);
    } finally {
      clearTimeout(t);
      setBusy(false);
    }
  };

  return (
    <div className="signin-page">
      <main className="signin">
        <h1>Demand Planning</h1>
        <p className="muted" style={{ marginBottom: unavailable || err ? 8 : 16 }}>Plan monthly purchase orders for quick-commerce channels from your own sales exports.</p>
        {unavailable ? (
          <>
            <div role="alert" className="banner crit" style={{ flexDirection: "column" }}>
              <span>Sign-in is unavailable — this workspace is missing its connection settings. Send your admin reference {ref}.</span>
              <span className="row between" style={{ padding: "6px 8px", background: "var(--surface)", borderRadius: 4, width: "100%" }}>
                <code className="mono t-small">{ref}</code>
                <Button size="sm" icon={Copy} onClick={() => navigator.clipboard?.writeText(ref)}>Copy reference</Button>
              </span>
            </div>
            <Button variant="primary" size="lg" disabled reason="Sign-in can't start until an admin adds the connection settings." className="lg">Sign in with your work account</Button>
          </>
        ) : (
          <form className="stack g8" onSubmit={(e) => { e.preventDefault(); if (!busy) submit(); }}>
            {err && <Banner tone="crit" role="alert">{err}</Banner>}
            <label className="stack"><span className="t-small muted">Work email</span>
              <span className="field block"><input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></span></label>
            <label className="stack"><span className="t-small muted">Password</span>
              <span className="field block"><input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} required /></span></label>
            <Button type="submit" variant="primary" size="lg" icon={LogIn} busy={busy} className="lg">{busy ? "Signing in" : err ? "Sign in with another account" : "Sign in with your work account"}</Button>
            {busy && slow && (
              <span role="status" className="t-small muted" style={{ textAlign: "center" }}>
                Waiting for your account to confirm. <button type="button" className="linkbtn" onClick={() => { cancelled.current = true; setBusy(false); }}>Cancel</button>
              </span>
            )}
          </form>
        )}
      </main>
    </div>
  );
}

function Workspace({ theme: [theme, toggleTheme] }: { theme: ReturnType<typeof useTheme> }) {
  const [route, go] = useRoute();
  const [sessions, setSessions] = useState<Json[]>([]);
  const [session, setSession] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cwCount, setCwCount] = useState<number | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const sid = route.page === "chat" || route.page === "report" ? route.sid : null;

  const loadSessions = useCallback(() => api.listSessions().then(setSessions), []);
  const loadCw = useCallback(() => api.crosswalk("review").then((c) => setCwCount((c.rows ?? []).length)).catch(() => setCwCount(null)), []);
  useEffect(() => { loadSessions().catch((e) => setError(e.message)); loadCw(); }, [loadSessions, loadCw]);

  const newChat = async () => {
    const s = await api.createSession("Demand planning");
    await loadSessions();
    go(`#/s/${s.id}/import`);
  };

  useEffect(() => {
    if (route.page !== "home") return;
    api.listSessions().then(async (list) => {
      if (list[0]) go(`#/s/${list[0].id}/${list[0].current_run_id ? "ask" : "import"}`);
      else await newChat();
    }).catch((e) => setError(e.message));
  }, [route.page]); // eslint-disable-line

  const refresh = useCallback(async () => {
    if (sid) setSession(await api.session(sid));
  }, [sid]);
  useEffect(() => {
    setSession((s) => (s?.id === sid ? s : null));
    refresh().catch((e) => setError(e.message));
  }, [refresh, route.page === "chat" ? route.stage : route.page]); // eslint-disable-line
  // keep dataset statuses fresh while files are being stored
  useEffect(() => {
    if (!session?.datasets?.some((d: Json) => ["uploaded", "parsing", "mapped"].includes(d.status))) return;
    const t = setTimeout(() => refresh().catch(() => undefined), 2500);
    return () => clearTimeout(t);
  }, [session, refresh]);

  if (route.page === "report") {
    return <ReportView runId={route.rid} session={session} onBack={() => go(`#/s/${route.sid}/ask`)}
      onEditLine={(text) => { setDraft(text); go(`#/s/${route.sid}/ask`); }} />;
  }

  const active: NavId = route.page === "saved" ? "saved" : route.page === "crosswalk" ? "crosswalk" : "chats";
  const datasets: Json[] = session?.datasets ?? [];
  const here = route.page === "saved" ? "Saved data" : route.page === "crosswalk" ? "Crosswalk"
    : session ? (session.title || "New chat") : "Chat";

  let body;
  if (error) body = <div className="content"><div className="inner"><div className="card"><ErrorBlank text={`This page didn’t load. ${error}`} onAction={() => location.reload()} /></div></div></div>;
  else if (route.page === "saved") body = <SavedDataScreen sessions={sessions} onOpen={(id, st) => go(`#/s/${id}/${st}`)} onNewChat={newChat}
    onPlan={(id) => { setPending("Create a demand plan for next month"); go(`#/s/${id}/ask`); }} />;
  else if (route.page === "crosswalk") body = <CrosswalkScreen onChanged={loadCw} />;
  else if (route.page !== "chat" || !session) body = <div className="content"><div className="inner"><div className="card"><Loading text="Restoring this chat: messages, cards and where you were." /></div></div></div>;
  else {
    const stage = route.stage;
    const anyParsed = datasets.some((d) => ["parsed", "mapped", "ready"].includes(d.status));
    const anyReady = datasets.some((d) => d.status === "ready");
    const reading = datasets.some((d) => ["uploaded", "parsing"].includes(d.status));
    const rail = (
      <StepRail stage={stage} onGo={(s) => go(`#/s/${sid}/${s}`)}
        done={{ import: datasets.some((d) => !["failed", "duplicate"].includes(d.status)), map: anyReady, ask: (session.runs ?? []).some((r: Json) => r.status === "succeeded") }}
        gates={{
          import: { open: true },
          map: { open: anyParsed, reason: "Continue to mapping once a file is ready.", note: reading && stage === "map" ? "Preparing" : undefined },
          ask: { open: anyReady, reason: anyParsed ? "Save the mapping of a sales-order file to plan." : "Map a sales-order file to plan." },
        }} />
    );
    body = (
      <>
        {rail}
        {stage === "import" && <ImportScreen sessionId={session.id} datasets={datasets} onRefresh={refresh} onContinue={() => go(`#/s/${sid}/map`)} />}
        {stage === "map" && <MappingScreen datasets={datasets} onCrosswalk={() => go("#/crosswalk")}
          onImport={() => go(`#/s/${sid}/import`)} onSavedData={() => go("#/saved")} onRefresh={refresh}
          onSaved={async () => { await refresh(); loadCw(); go(`#/s/${sid}/ask`); }} />}
        {stage === "ask" && <AskScreen session={session} crosswalkCount={cwCount} pendingQuestion={pending} draft={draft}
          onConsumed={() => { setPending(null); setDraft(null); }} onOpenReport={(rid) => go(`#/s/${sid}/report/${rid}`)}
          onGoMap={() => go(`#/s/${sid}/map`)} onGoCrosswalk={() => go("#/crosswalk")} onTitle={loadSessions} />}
      </>
    );
  }

  return (
    <div className="app">
      <Sidebar active={active} workspace={WORKSPACE} sessions={sessions} currentSid={sid} crosswalkCount={cwCount}
        onNav={(n) => {
          if (n === "saved") go("#/saved");
          else if (n === "crosswalk") go("#/crosswalk");
          else go(sessions[0] ? `#/s/${sessions[0].id}/ask` : "#/");
        }}
        onChat={(id) => go(`#/s/${id}/ask`)} onNewChat={newChat} theme={theme} onTheme={toggleTheme}
        onSignOut={usesDevToken ? undefined : () => signOut().then(() => location.reload())} />
      <div className="maincol">
        <TopBar workspace={WORKSPACE} here={here} />
        {body}
      </div>
    </div>
  );
}
