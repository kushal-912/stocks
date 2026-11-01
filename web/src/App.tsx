import { useEffect, useState } from "react";
import { Icon } from "./components/ui";
import { api, go, post, timeAgo, toggleTheme, usePoll, useRoute } from "./lib";
import Overview from "./pages/Overview";
import Portfolio from "./pages/Portfolio";
import Research from "./pages/Research";
import Stock from "./pages/Stock";
import Activity from "./pages/Activity";
import Evaluation from "./pages/Evaluation";
import Backtest from "./pages/Backtest";
import Settings from "./pages/Settings";

const NAV = [
  ["", "overview", "Overview"],
  ["portfolio", "portfolio", "Portfolio"],
  ["research", "research", "Research"],
  ["activity", "activity", "Activity"],
  ["evaluation", "evaluation", "Evaluation"],
  ["backtest", "backtest", "Backtest"],
  ["settings", "settings", "Settings"],
];

function Search() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) return setRes([]);
    const t = setTimeout(() => api(`/api/search?q=${encodeURIComponent(q)}`).then(setRes).catch(() => setRes([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="search">
      <Icon name="search" size={16} />
      <input placeholder="Search any NSE stock" value={q} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && q.trim()) { go(`stock/${(res[0]?.symbol ?? q).toUpperCase()}`); setQ(""); }
        }} />
      {res.length > 0 && (
        <div className="results">
          {res.map((r) => (
            <a key={r.symbol} href={`#/stock/${r.symbol}`} onClick={() => setQ("")}>
              <b>{r.symbol}</b> <span className="muted small">{r.name} · {r.sector}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function TopBar({ status, refresh }: { status: any; refresh: () => void }) {
  const ap = status?.autopilot;
  const killed = status?.kill_switch?.on;
  const toggleKill = async () => {
    const on = !killed;
    if (on && !confirm("Engage the kill switch? The agent stops opening positions (exits and stop-losses keep working).")) return;
    await post("/api/kill", { on, reason: on ? "manual stop from web app" : "" });
    refresh();
  };
  return (
    <header className="topbar">
      <span className={`badge ${status?.mode === "live" ? "bad" : "blue"}`}>
        {status?.mode === "live" ? `LIVE · ${status?.broker}` : "PAPER"}
      </span>
      <span className="row small" title={ap?.last_heartbeat ? `last heartbeat ${ap.last_heartbeat}` : "no heartbeat"}>
        <span className={`dot ${ap?.alive ? "good" : "bad"}`} />
        {ap?.alive ? "Agent running" : `Agent offline${ap?.last_heartbeat ? ` (${timeAgo(ap.last_heartbeat)})` : ""}`}
      </span>
      <span className="row small ink2">
        <span className={`dot ${status?.market_open ? "good" : ""}`} />
        {status?.market_open ? "Market open" : "Market closed"}
        {status?.now && <span className="muted num">· {status.now.slice(11, 16)} IST</span>}
      </span>
      <span className="spacer" />
      <Search />
      <button className="btn ghost sm" onClick={toggleTheme} title="Toggle light/dark"><Icon name="theme" size={16} /></button>
      <button className={`btn sm danger ${killed ? "on" : ""}`} onClick={toggleKill}
        title={killed ? status?.kill_switch?.reason : "Stop opening new positions"}>
        <Icon name="shield" size={14} /> {killed ? "Kill switch ON" : "Kill switch"}
      </button>
    </header>
  );
}

export default function App() {
  const route = useRoute();
  const { data: status, refresh } = usePoll<any>("/api/status", 15000);
  const page = route[0] ?? "";
  let body;
  switch (page) {
    case "portfolio": body = <Portfolio />; break;
    case "research": body = <Research />; break;
    case "stock": body = <Stock symbol={(route[1] ?? "").toUpperCase()} />; break;
    case "activity": body = <Activity />; break;
    case "evaluation": body = <Evaluation />; break;
    case "backtest": body = <Backtest />; break;
    case "settings": body = <Settings status={status} />; break;
    default: body = <Overview status={status} />;
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 17l6-6 4 4 7-8" />
            </svg>
          </span>
          QuantDesk
        </div>
        <nav className="nav">
          {NAV.map(([path, icon, label]) => (
            <a key={label} href={`#/${path}`} className={page === path || (page === "stock" && path === "research") ? "active" : ""}>
              <Icon name={icon} /> <span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          {status?.inception ? <>Evaluation since <b className="ink2">{status.inception.date}</b><br />Start capital ₹{Math.round(status.inception.capital).toLocaleString("en-IN")}</>
            : "Agent not started yet"}
        </div>
      </aside>
      <div className="main">
        <TopBar status={status} refresh={refresh} />
        <main className="content">{body}</main>
      </div>
    </div>
  );
}
