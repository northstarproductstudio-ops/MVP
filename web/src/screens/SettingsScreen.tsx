// Workspace settings: the planning agent (pipeline step 8) on or off, and the model the AI features use.
import { useEffect, useState } from "react";
import { api, type Json } from "../api";
import { dateLabel } from "../components/common";
import { Banner, ErrorBlank, Loading, StateChip } from "../components/ui";

export function SettingsScreen() {
  const [s, setS] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api.settings().then(setS).catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const toggle = async (enabled: boolean) => {
    setSaving(true);
    setSaveErr(null);
    try {
      setS(await api.setPlanningAgent(enabled));
    } catch (e: any) {
      setSaveErr(`The setting wasn’t changed. ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const agent = s?.planning_agent;
  const model = s?.model;
  return (
    <main className="content">
      <div className="inner">
        <div className="pagehead">
          <div className="stack"><h1>Settings</h1>
            <p className="muted">Applies to every chat in this workspace. A plan that has already started keeps the settings it began with.</p></div>
        </div>

        {error ? <div className="card"><ErrorBlank text={`Settings didn’t load. Nothing was changed. ${error}`} onAction={load} /></div>
          : !s ? <div className="card"><Loading text="Loading settings." rows={4} /></div>
          : <>
            {saveErr && <Banner tone="crit" role="alert">{saveErr}</Banner>}
            <section className="card" aria-labelledby="agent-h">
              <div className="card-head">
                <span id="agent-h" className="h-section">Planning agent</span>
                <StateChip tone={agent.enabled ? "good" : "ignored"}>{agent.enabled ? "On" : "Off"}</StateChip>
              </div>
              <div className="stack g8" style={{ padding: 16 }}>
                <label className="row" style={{ gap: 12, alignItems: "flex-start", cursor: saving ? "default" : "pointer" }}>
                  <span className="switch">
                    <input type="checkbox" role="switch" checked={agent.enabled} disabled={saving}
                      aria-describedby="agent-desc" onChange={(e) => toggle(e.target.checked)} />
                    <span className="track" aria-hidden="true" /><span className="thumb" aria-hidden="true" />
                  </span>
                  <span className="stack" style={{ gap: 2 }}>
                    <span className="strong">Use the AI planning agent in step 8 of the pipeline</span>
                    <span id="agent-desc" className="t-small muted">
                      {agent.enabled
                        ? "On: the AI reviews each SKU’s forecast, can resize the Python factors and adds festival and seasonality effects. Python checks every answer in step 9 and falls back to the reference forecast for any line that fails. Each SKU is one AI call, so plans take longer."
                        : "Off: plans use the reference forecast from steps 1–7 (trend, sell-through, fill rate and stock cover) with no festival or seasonality adjustment. A plan finishes in seconds."}
                    </span>
                  </span>
                </label>
                <span className="t-caption muted">
                  {saving ? "Saving…" : `Last changed ${dateLabel(agent.updated_at)} · ${agent.scope === "workspace" ? "set for this workspace" : "default configuration"}`}
                </span>
              </div>
            </section>

            <section className="card" aria-labelledby="model-h">
              <div className="card-head"><span id="model-h" className="h-section">AI model</span></div>
              <dl className="dl" style={{ padding: 16, gridTemplateColumns: "140px minmax(0, 1fr)" }}>
                <dt>Model</dt><dd><code className="mono">{model.id}</code></dd>
                <dt>Provider</dt><dd>{model.provider}</dd>
                <dt>Used by</dt><dd>Planning agent (step 8), Ask chat, import field mapping and the plan overview text</dd>
                <dt>Prompt data</dt>
                <dd>{model.data_collection === "allow"
                  ? "Providers may store prompts (needed for free models) — use test data only."
                  : "Sent only to providers that don’t store or train on prompts."}</dd>
              </dl>
              <div className="card-foot line"><span>The model is set by the LLM_MODEL variable on the fmcg-api and fmcg-worker services in the Catalyst console.</span></div>
            </section>
          </>}
      </div>
    </main>
  );
}
