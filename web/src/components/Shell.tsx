// Global shell: 232/64 left nav, 56 top bar with breadcrumb, 48 step rail on Import, Map, Plan.
import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowLeftRight, Check, CircleHelp, CircleUser, Database, LogOut, MessageSquare, Moon, PanelLeftClose, PanelLeftOpen, Plus,
  Settings, Sun,
} from "lucide-react";
import type { Json } from "../api";
import type { Stage } from "./common";
import { IconButton } from "./ui";

export type NavId = "chats" | "saved" | "crosswalk" | "settings";

export function useTheme(): ["light" | "dark", () => void] {
  const read = () => {
    try { return (localStorage.getItem("theme") as "light" | "dark") || "light"; } catch { return "light"; }
  };
  const [theme, setTheme] = useState<"light" | "dark">(read);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* storage blocked */ }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

function NavItem({ icon: I, label, active, count, onClick }: {
  icon: typeof Database; label: string; active?: boolean; count?: number | null; onClick: () => void;
}) {
  return (
    <button type="button" className={`navitem ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} title={label} onClick={onClick}>
      <I size={16} aria-hidden="true" />
      <span className="label">{label}</span>
      {count ? <span className="count">{count}</span> : null}
    </button>
  );
}

export function Sidebar({ active, workspace, sessions, currentSid, crosswalkCount, onNav, onChat, onNewChat, theme, onTheme, onSignOut }: {
  active: NavId; workspace: string; sessions: Json[]; currentSid: string | null; crosswalkCount: number | null;
  onNav: (n: NavId) => void; onChat: (sid: string) => void; onNewChat: () => void;
  theme: "light" | "dark"; onTheme: () => void; onSignOut?: () => void;
}) {
  const [collapsed, setCollapsed] = useState(() => window.innerWidth <= 1024);
  return (
    <nav aria-label="Main" className={`sidenav ${collapsed ? "collapsed" : ""}`}>
      <div className="brand">
        {!collapsed && <span className="stack" style={{ gap: 0, minWidth: 0 }}><span className="name">Demand Planning</span><span className="t-caption muted">{workspace}</span></span>}
        <IconButton icon={collapsed ? PanelLeftOpen : PanelLeftClose} label={collapsed ? "Expand navigation" : "Collapse navigation"} size="sm" onClick={() => setCollapsed(!collapsed)} />
      </div>
      <div className="stack" style={{ gap: 2 }}>
        <NavItem icon={MessageSquare} label="Chats" active={active === "chats"} onClick={() => onNav("chats")} />
        <NavItem icon={Database} label="Saved data" active={active === "saved"} onClick={() => onNav("saved")} />
        <NavItem icon={ArrowLeftRight} label="Crosswalk" active={active === "crosswalk"} count={crosswalkCount} onClick={() => onNav("crosswalk")} />
        <NavItem icon={Settings} label="Settings" active={active === "settings"} onClick={() => onNav("settings")} />
      </div>
      {!collapsed && (
        <div className="stack" style={{ gap: 4, minHeight: 0, overflow: "auto" }}>
          <span className="row between" style={{ paddingRight: 4 }}>
            <span className="caption">Recent chats</span>
            <IconButton icon={Plus} label="New chat" size="sm" onClick={onNewChat} />
          </span>
          {sessions.slice(0, 12).map((s) => (
            <a key={s.id} href={`#/s/${s.id}/ask`} className={`recent ${s.id === currentSid ? "active" : ""}`}
              aria-current={s.id === currentSid ? "page" : undefined}
              onClick={(e) => { e.preventDefault(); onChat(s.id); }}>{s.title || "Untitled chat"}</a>
          ))}
        </div>
      )}
      <div className="foot">
        <NavItem icon={theme === "dark" ? Sun : Moon} label={theme === "dark" ? "Light theme" : "Dark theme"} onClick={onTheme} />
        {onSignOut ? <NavItem icon={LogOut} label="Sign out" onClick={onSignOut} /> : <NavItem icon={CircleUser} label="Account" onClick={() => undefined} />}
      </div>
    </nav>
  );
}

export function TopBar({ workspace, here }: { workspace: string; here: string }) {
  return (
    <header className="topbar">
      <nav aria-label="Breadcrumb" className="crumbs">
        <span className="muted" style={{ whiteSpace: "nowrap" }}>{workspace}</span>
        <span aria-hidden="true" className="sep">/</span>
        <span className="here">{here}</span>
      </nav>
      <IconButton icon={CircleHelp} label="Help" onClick={() => window.open("https://github.com/northstarproductstudio-ops/MVP#readme", "_blank")} />
    </header>
  );
}

type Gate = { open: boolean; reason?: string; note?: string };

export function StepRail({ stage, done, gates, onGo }: {
  stage: Stage; done: Record<Stage, boolean>; gates: Record<Stage, Gate>; onGo: (s: Stage) => void;
}) {
  const steps: [Stage, string][] = [["import", "Import"], ["map", "Map"], ["ask", "Plan"]];
  const idx = steps.findIndex(([s]) => s === stage);
  return (
    <div className="rail">
      <ol aria-label="Data preparation steps">
        {steps.map(([s, label], i) => {
          const current = i === idx;
          const isDone = !current && done[s];
          const g = gates[s];
          const node = isDone ? "done" : current ? "current" : "todo";
          const items: ReactNode[] = [];
          if (i > 0) items.push(<li key={`l${s}`} aria-hidden="true" className={`link ${steps.slice(0, i).every(([p]) => done[p]) ? "done" : ""}`} />);
          items.push(
            <li key={s} aria-current={current ? "step" : undefined} className={!g.open && !current ? "has-tip" : undefined}>
              <button type="button" className="step" aria-disabled={!g.open && !current ? true : undefined}
                aria-describedby={!g.open && !current ? `rail-${s}` : undefined}
                onClick={() => (g.open || current) && onGo(s)}>
                <span className={`node ${node}`}>{isDone ? <Check size={12} aria-hidden="true" /> : node === "todo" ? i + 1 : null}</span>
                <span className={current ? "strong" : undefined}>{label}</span>
              </button>
              {(current ? g.note ?? "Current" : isDone ? "Done" : null) && <span className="note">{current ? g.note ?? "Current" : "Done"}</span>}
              {!g.open && !current && g.reason && <span id={`rail-${s}`} role="tooltip" className="tip">{g.reason}</span>}
            </li>,
          );
          return items;
        })}
      </ol>
    </div>
  );
}
