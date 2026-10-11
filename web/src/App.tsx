// Three stages agreed in the 10 Oct walkthrough: Import → Map fields → Ask (demand planning runs inside Ask).
import { useCallback, useEffect, useState } from "react";
import { api, type Json } from "./api";
import { getToken, signIn, signOut, supabase, usesDevToken } from "./auth";
import { StepHeader, type Stage } from "./components/common";
import { AskScreen } from "./screens/AskScreen";
import { DataScreen } from "./screens/DataScreen";
import { ImportScreen } from "./screens/ImportScreen";
import { MappingScreen } from "./screens/MappingScreen";
import { SavedScreen } from "./screens/SavedScreen";

type Route = Stage | "data";
const RETURN_KEY = "dp.returnToData";

function useHashRoute(): [string | null, Route, string | null, (sid: string, st: Route, suggest?: string) => void] {
  const parse = () => {
    const m = window.location.hash.match(/^#\/s\/([0-9a-f-]+)\/(import|map|ask|data)(?:\?g=([a-z_]+))?/);
    return m ? [m[1], m[2] as Route, m[3] ?? null] as const : [null, "import" as Route, null] as const;
  };
  const [state, setState] = useState(parse());
  useEffect(() => {
    const on = () => setState(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [state[0], state[1], state[2], (sid, st, g) => { window.location.hash = `#/s/${sid}/${st}${g ? `?g=${g}` : ""}`; }];
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    getToken().then((t) => setAuthed(Boolean(t)));
    const sub = supabase?.auth.onAuthStateChange((_e, s) => setAuthed(Boolean(s) || usesDevToken));
    return () => sub?.data.subscription.unsubscribe();
  }, []);
  if (authed === null) return null;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <Workspace />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <form className="login card pad" onSubmit={async (e) => { e.preventDefault(); try { await signIn(email, pw); onDone(); } catch (x: any) { setErr(x.message); } }}>
      <h1>Demand Planning</h1>
      <p className="muted">Sign in with your company account.</p>
      <input placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input placeholder="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
      {err && <span className="error">{err}</span>}
      <button className="btn primary">Sign in</button>
    </form>
  );
}

function Workspace() {
  const [sid, stage, suggest, go] = useHashRoute();
  const [session, setSession] = useState<Json | null>(null);
  const [summaries, setSummaries] = useState<Json[] | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sid) return;
    api.listSessions().then(async (list) => {
      const s = list[0] ?? (await api.createSession("Demand planning"));
      go(s.id, "import");
    }).catch((e) => setError(e.message));
  }, [sid]); // eslint-disable-line

  const refresh = useCallback(async () => {
    if (sid) setSession(await api.session(sid));
  }, [sid]);
  useEffect(() => { refresh().catch((e) => setError(e.message)); }, [refresh, stage]);
  // keep dataset statuses fresh while files are being stored
  useEffect(() => {
    if (!session?.datasets?.some((d: Json) => ["uploaded", "parsing", "mapped"].includes(d.status))) return;
    const t = setTimeout(refresh, 2500);
    return () => clearTimeout(t);
  }, [session, refresh]);

  if (error) return <div className="page"><div className="col"><div className="notice bad">{error}</div></div></div>;
  if (!sid || !session) return null;
  const datasets: Json[] = session.datasets ?? [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Demand Planning<small>{session.title}</small></div>
        <StepHeader stage={stage === "data" ? "ask" : stage} onGo={(s) => go(sid, s)} />
        <div className="row">
          <button className={`btn small ${stage === "data" ? "" : "ghost"}`} onClick={() => go(sid, "data")}>Data</button>
          <button className="btn ghost small" onClick={async () => { const s = await api.createSession("Demand planning"); go(s.id, "import"); }}>New chat</button>
          {!usesDevToken && <button className="btn ghost small" onClick={() => signOut().then(() => location.reload())}>Sign out</button>}
        </div>
      </header>
      <main className="page">
        {stage === "import" && <ImportScreen sessionId={sid} datasets={datasets} onProcessed={() => { refresh(); go(sid, "map"); }} />}
        {stage === "map" && (summaries
          ? <SavedScreen sessionId={sid} summaries={summaries} datasets={datasets} onBack={() => setSummaries(null)}
              onAsk={() => go(sid, "ask")} onData={() => go(sid, "data")}
              onRunPipeline={() => { setPending("Create a demand plan for next month"); go(sid, "ask"); }} />
          : <MappingScreen sessionId={sid} datasets={datasets} onSaved={(s) => {
              refresh();
              // "Add files" from the Data screen returns there after the mapping is saved
              if (sessionStorage.getItem(RETURN_KEY) === sid) { sessionStorage.removeItem(RETURN_KEY); go(sid, "data"); }
              else setSummaries(s);
            }} />)}
        {stage === "ask" && <AskScreen session={session} pendingQuestion={pending} onConsumed={() => setPending(null)}
          onAddFile={(g) => go(sid, "data", g)} />}
        {stage === "data" && <DataScreen sessionId={sid} suggest={suggest}
          onAddFiles={() => { sessionStorage.setItem(RETURN_KEY, sid); go(sid, "import"); }}
          onMap={() => { setSummaries(null); go(sid, "map"); }} onAsk={() => go(sid, "ask")} />}
      </main>
    </div>
  );
}
