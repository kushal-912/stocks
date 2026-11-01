import { useEffect, useMemo, useRef, useState } from "react";
import { LineChart } from "../components/Charts";
import { Card, DataTable, Empty, Kpi, Tabs } from "../components/ui";
import { api, cssVar, dateShort, inr, num, pct, post, price, signedInr, tone, useThemeVersion } from "../lib";

type Ev = Record<string, any>;

function useBacktestFeed() {
  const [state, setState] = useState<any>({ status: "idle" });
  const [events, setEvents] = useState<Ev[]>([]);
  const [saved, setSaved] = useState<any>(null);
  const [minCapital, setMinCapital] = useState<number>(0);
  const next = useRef(0);
  const loadSaved = async () => {
    const d = await api("/api/backtest");
    setSaved(d.result);
    setMinCapital(d.min_capital ?? 0);
    return d;
  };
  const poll = async () => {
    const d = await api(`/api/backtest/stream?since=${next.current}`);
    if (d.next < next.current) {           // a new run started: reset
      next.current = 0;
      setEvents([]);
      return;
    }
    if (d.events.length) setEvents((e) => [...e, ...d.events]);
    next.current = d.next;
    setState(d.state);
    if (d.state.status === "done") loadSaved();
  };
  useEffect(() => {
    loadSaved().catch(() => {});
    poll().catch(() => {});
  }, []);
  useEffect(() => {
    if (state.status !== "running") return;
    const id = window.setInterval(() => poll().catch(() => {}), 1000);
    return () => window.clearInterval(id);
  }, [state.status]);
  const restart = () => {
    next.current = 0;
    setEvents([]);
    setState({ status: "running", progress: 0, message: "starting…" });
  };
  return { state, events, saved, minCapital, restart, poll };
}

function LogLine({ e }: { e: Ev }) {
  if (e.type === "buy")
    return (
      <div className="logline">
        <span className="d">{e.day}</span><span className="tag buy">BUY</span>
        <span><b>{e.qty} {e.symbol}</b> @ ₹{price(e.price)} = {inr(e.value)} · stop ₹{price(e.stop)}
          <span className="muted"> · {e.reason}</span></span>
      </div>
    );
  if (e.type === "sell")
    return (
      <div className="logline">
        <span className="d">{e.day}</span><span className={`tag ${e.pnl >= 0 ? "win" : "loss"}`}>SELL</span>
        <span><b>{e.qty} {e.symbol}</b> @ ₹{price(e.exit_price)} · bought ₹{price(e.entry_price)} on {dateShort(e.entry_day)} ·{" "}
          <b className={tone(e.pnl)}>{signedInr(e.pnl)} ({pct(e.ret, 1)})</b>
          {e.nifty_ret !== null && e.nifty_ret !== undefined && <span className="muted"> vs NIFTY {pct(e.nifty_ret, 1)}</span>}
          <span className="muted"> · {e.holding_days}d · {e.reason}</span></span>
      </div>
    );
  if (e.type === "skip")
    return <div className="logline"><span className="d">{e.day}</span><span className="tag">SKIP</span><span className="muted">{e.symbol}: {e.reason}</span></div>;
  if (e.type === "note")
    return <div className="logline"><span className="d">{e.day}</span><span className="tag">NOTE</span><span className="muted">{e.message}</span></div>;
  if (e.type === "start")
    return <div className="logline"><span className="d">{e.start}</span><span className="tag">START</span>
      <span className="muted">{inr(e.capital)} · {e.days} trading days to {e.end} · universe: {e.universe === "top_liquid" ? `top ${e.top_n} most-traded each day` : e.universe} · {e.charges} charges</span></div>;
  return null;
}

export default function Backtest() {
  useThemeVersion();
  const { state, events, saved, minCapital, restart, poll } = useBacktestFeed();
  const [start, setStart] = useState("2024-01-01");
  const [end, setEnd] = useState("");
  const [capital, setCapital] = useState(50000);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState("All trades");
  const [follow, setFollow] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const running = state.status === "running";

  // Live view while running (or a just-finished run still in memory); otherwise the saved result.
  const live = events.length > 0;
  const days: Ev[] = live ? events.filter((e) => e.type === "day") : (saved?.curve ?? []);
  const log: Ev[] = live ? events.filter((e) => e.type !== "day" && e.type !== "end") : (saved?.log ?? []);
  const sells: Ev[] = live ? events.filter((e) => e.type === "sell")
    : (saved?.trades ?? []).map((t: any) => ({ ...t, day: t.exit_date, entry_day: t.entry_date, reason: t.exit_reason }));
  const capitalUsed = live ? (events.find((e) => e.type === "start")?.capital ?? capital) : (saved?.params?.capital ?? capital);

  const stats = useMemo(() => {
    let peak = -Infinity, dd = 0;
    days.forEach((d) => { peak = Math.max(peak, d.equity); dd = Math.min(dd, d.equity / peak - 1); });
    const last = days[days.length - 1];
    const wins = sells.filter((t) => t.pnl > 0).length;
    const beat = sells.filter((t) => t.nifty_ret !== null && t.nifty_ret !== undefined && t.ret > t.nifty_ret).length;
    return { last, dd, wins, beat, pnl: sells.reduce((a, t) => a + t.pnl, 0) };
  }, [days.length, sells.length]);

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log.length, follow]);

  const run = async () => {
    setErr(null);
    try {
      await post("/api/backtest", { start, end: end || null, capital });
      restart();
      setTimeout(() => poll().catch(() => {}), 300);
    } catch (e: any) { setErr(e.message); }
  };
  const cancel = async () => { await post("/api/backtest/cancel", {}); };

  const m = !running && saved && !live ? saved.metrics : (state.status === "done" && saved ? saved.metrics : null);
  const eqRet = stats.last ? stats.last.equity / capitalUsed - 1 : null;
  const bRet = stats.last?.benchmark ? stats.last.benchmark / capitalUsed - 1 : null;
  const shownTrades = tab === "Winners" ? sells.filter((t) => t.pnl > 0) : tab === "Losers" ? sells.filter((t) => t.pnl <= 0) : sells;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Backtest</h1>
          <p>Replays the agent's policy day by day on real history. Each decision sees only the data available at that day's close and trades at the next open.</p>
        </div>
      </div>

      <Card>
        <div className="row wrap" style={{ gap: 14 }}>
          <label className="row small ink2">From <input type="date" value={start} onChange={(e) => setStart(e.target.value)} disabled={running} /></label>
          <label className="row small ink2">To <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} disabled={running} /></label>
          <label className="row small ink2">Capital ₹ <input type="number" value={capital} step={5000} min={minCapital} style={{ width: 120 }}
            onChange={(e) => setCapital(Number(e.target.value))} disabled={running} /></label>
          {!running ? <button className="btn primary" onClick={run}>Run backtest</button>
            : <button className="btn danger" onClick={cancel}>Stop</button>}
          {minCapital > 0 && capital < minCapital && <span className="small neg">Minimum ₹{minCapital.toLocaleString("en-IN")} (see below)</span>}
          {err && <span className="neg small">{err}</span>}
          {state.status === "failed" && <span className="neg small">Failed: {state.message}</span>}
          {state.status === "cancelled" && <span className="muted small">Stopped.</span>}
        </div>
        {running && (
          <div style={{ marginTop: 14 }}>
            <div className="progress"><div style={{ width: `${(state.progress ?? 0) * 100}%` }} /></div>
            <div className="small muted" style={{ marginTop: 4 }}>{Math.round((state.progress ?? 0) * 100)}% · {state.message}</div>
          </div>
        )}
        <details style={{ marginTop: 12 }}>
          <summary className="small ink2" style={{ cursor: "pointer" }}>How this avoids knowing the future, and what it leaves out</summary>
          <div className="callout" style={{ marginTop: 8 }}>
            <b>No look-ahead:</b> every morning the agent decides from a copy of the data that physically ends at the previous
            close; orders fill at the next day's open, stops on that day's low. The stock universe is the top {"750"} most-traded
            stocks <i>as of each day</i>, not today's index list. Prices are split-adjusted only (dividend adjustment would leak
            later dividends). A test scrambles all prices after a date and checks that nothing before it changes.
            <br /><b>Not replayed:</b> the deep research layer (ML, forecasts), news and the Claude review. <b>Bias that remains:</b>
            companies delisted before today are missing from NSE's list. <b>Small accounts:</b> each position must be at least
            ₹5,000 (fixed charges) and at most 22% of the account, so below about ₹{minCapital.toLocaleString("en-IN")} the agent cannot trade at all.
          </div>
        </details>
      </Card>

      {!days.length && !running ? <div style={{ marginTop: 16 }}><Card><Empty>No backtest yet - choose a period and run one.</Empty></Card></div> : (
        <>
          <div className="kpis" style={{ marginTop: 16 }}>
            <Kpi label={running ? `Equity · ${stats.last?.day ?? ""}` : "Final value"} value={inr(stats.last?.equity)}
              sub={pct(eqRet)} subTone={tone(eqRet)} />
            <Kpi label="NIFTY 50, same money" value={inr(stats.last?.benchmark)} sub={pct(bRet)} subTone={tone(bRet)} />
            <Kpi label="Agent vs NIFTY" value={<span className={tone((eqRet ?? 0) - (bRet ?? 0))}>{bRet === null || eqRet === null ? "–" : pct(eqRet - bRet)}</span>}
              sub={m ? `Sharpe ${num(m.sharpe)} · CAGR ${pct(m.cagr, 1)}` : undefined} />
            <Kpi label="Max drawdown" value={pct(stats.dd, 1)} sub={m ? `NIFTY ${pct(m.benchmark_max_drawdown, 1)}` : undefined} />
            <Kpi label="Closed trades" value={sells.length}
              sub={sells.length ? `${pct(stats.wins / sells.length, 0, false)} won · ${pct(stats.beat / sells.length, 0, false)} beat NIFTY` : "–"} />
            <Kpi label="Realised P&L" value={<span className={tone(stats.pnl)}>{signedInr(stats.pnl)}</span>}
              sub={`${stats.last?.positions ?? 0} open · ${inr(stats.last?.cash)} cash`} />
          </div>

          <div className="grid g-main">
            <Card title={running ? "Live: agent vs NIFTY 50" : `Agent vs NIFTY 50${saved?.params ? ` since ${saved.params.start}` : ""}`}>
              {days.length > 1 ? <LineChart height={360} lines={[
                { name: "Agent", color: cssVar("--s1"), data: days.map((p) => ({ time: p.day, value: p.equity })) },
                { name: "NIFTY 50", color: cssVar("--s2"), data: days.filter((p) => p.benchmark).map((p) => ({ time: p.day, value: p.benchmark })) },
              ]} /> : <Empty>Waiting for the first trading day…</Empty>}
            </Card>
            <Card title={`Trade log${running ? " (live)" : ""}`} pad={false}
              action={<label className="row small ink2"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} style={{ height: "auto" }} /> follow</label>}>
              <div ref={logRef} className="logbox">
                {log.length === 0 ? <div className="muted small" style={{ padding: 12 }}>Events will appear here as the replay runs.</div>
                  : log.slice(-1500).map((e, i) => <LogLine key={i} e={e} />)}
              </div>
            </Card>
          </div>

          <div style={{ height: 16 }} />
          <Card title="Every trade: bought at, sold at, profit/loss, and NIFTY over the same days" pad={false}
            action={<Tabs tabs={["All trades", "Winners", "Losers"]} active={tab} onChange={setTab} />}>
            <DataTable rows={[...shownTrades].reverse()} limit={500} empty="No closed trades yet." cols={[
              { key: "symbol", label: "Stock", render: (r) => <a className="sym" href={`#/stock/${r.symbol}`}>{r.symbol}</a> },
              { key: "entry_day", label: "Bought", render: (r) => <span className="nowrap num">{dateShort(r.entry_day)} @ ₹{price(r.entry_price)}</span> },
              { key: "day", label: "Sold", render: (r) => <span className="nowrap num">{dateShort(r.day)} @ ₹{price(r.exit_price)}</span> },
              { key: "qty", label: "Qty", right: true },
              { key: "holding_days", label: "Days", right: true },
              { key: "pnl", label: "P&L (after charges)", right: true, render: (r) => <b className={tone(r.pnl)}>{signedInr(r.pnl)}</b> },
              { key: "ret", label: "Return", right: true, render: (r) => <span className={tone(r.ret)}>{pct(r.ret, 1)}</span> },
              { key: "nifty_ret", label: "NIFTY same days", right: true, render: (r) => <span className={tone(r.nifty_ret)}>{pct(r.nifty_ret, 1)}</span> },
              { key: "edge", label: "vs NIFTY", right: true, sort: (r) => (r.nifty_ret === null || r.nifty_ret === undefined ? null : r.ret - r.nifty_ret),
                render: (r) => r.nifty_ret === null || r.nifty_ret === undefined ? "–" : <b className={tone(r.ret - r.nifty_ret)}>{pct(r.ret - r.nifty_ret, 1)}</b> },
              { key: "reason", label: "Why it sold", render: (r) => <div className="sub-line">{r.reason}</div> },
            ]} />
          </Card>
          {!running && (saved?.open_positions ?? []).length > 0 && !live && (
            <>
              <div style={{ height: 16 }} />
              <Card title="Still open at the end (marked to the last close)" pad={false}>
                <DataTable rows={saved.open_positions} cols={[
                  { key: "symbol", label: "Stock", render: (r) => <span className="sym">{r.symbol}</span> },
                  { key: "entry_day", label: "Bought", render: (r) => <span className="num">{dateShort(r.entry_day)} @ ₹{price(r.entry_price)}</span> },
                  { key: "qty", label: "Qty", right: true },
                  { key: "last_price", label: "Last", right: true, render: (r) => price(r.last_price) },
                  { key: "stop", label: "Stop", right: true, render: (r) => price(r.stop) },
                  { key: "unrealised", label: "Unrealised", right: true, render: (r) => <b className={tone(r.unrealised)}>{signedInr(r.unrealised)} ({pct(r.ret, 1)})</b> },
                ]} />
              </Card>
            </>
          )}
        </>
      )}
    </>
  );
}
