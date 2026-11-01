import { useState } from "react";
import { Card, DataTable, Kpi, Loading, Tabs } from "../components/ui";
import { dateShort, go, inr, pct, price, signedInr, tone, usePoll } from "../lib";

export default function Portfolio() {
  const pos = usePoll<any>("/api/positions", 20000);
  const trades = usePoll<any[]>("/api/trades", 60000);
  const [tab, setTab] = useState("Open positions");
  if (pos.loading && !pos.data) return <Loading />;
  const rows = pos.data?.positions ?? [];
  const tr = trades.data ?? [];
  const equity = pos.data?.equity ?? 0;
  const bySector: Record<string, number> = {};
  rows.forEach((r: any) => (bySector[r.sector || "Other"] = (bySector[r.sector || "Other"] ?? 0) + r.value));
  const risk = rows.reduce((a: number, r: any) => a + Math.max(r.risk_to_stop ?? 0, 0), 0);
  const wins = tr.filter((t) => t.pnl > 0);

  return (
    <>
      <div className="page-head">
        <div><h1>Portfolio</h1><p>Everything the agent owns, why it bought it, and where it will get out.</p></div>
      </div>
      <div className="kpis">
        <Kpi label="Market value" value={inr(rows.reduce((a: number, r: any) => a + r.value, 0))} sub={`${inr(pos.data?.cash)} cash`} />
        <Kpi label="Unrealised P&L" value={<span className={tone(rows.reduce((a: number, r: any) => a + r.pnl, 0))}>
          {signedInr(rows.reduce((a: number, r: any) => a + r.pnl, 0))}</span>} />
        <Kpi label="Open risk to stops" value={inr(risk)} sub={equity ? `${pct(risk / equity, 1, false)} of equity` : undefined}
          hint="What the portfolio would lose (before gaps) if every stop-loss were hit" />
        <Kpi label="Closed trades" value={tr.length} sub={tr.length ? `${pct(wins.length / tr.length, 0, false)} winners` : "–"} />
        <Kpi label="Realised P&L" value={<span className={tone(tr.reduce((a, t) => a + t.pnl, 0))}>
          {signedInr(tr.reduce((a, t) => a + t.pnl, 0))}</span>} sub={`${inr(tr.reduce((a, t) => a + t.costs, 0))} charges`} />
      </div>
      <Tabs tabs={["Open positions", "Closed trades", "Allocation"]} active={tab} onChange={setTab} />
      {tab === "Open positions" && (
        <Card pad={false}>
          <DataTable rows={rows} onRow={(r) => go(`stock/${r.symbol}`)} empty="No open positions." initialSort={["value", "desc"]}
            cols={[
              { key: "symbol", label: "Stock", render: (r) => <div><span className="sym">{r.symbol}</span><div className="sub-line">{r.sector}</div></div> },
              { key: "entry_date", label: "Since", render: (r) => dateShort(r.entry_date) },
              { key: "qty", label: "Qty", right: true },
              { key: "entry_price", label: "Avg cost", right: true, render: (r) => price(r.entry_price) },
              { key: "ltp", label: "LTP", right: true, render: (r) => price(r.ltp) },
              { key: "pnl", label: "P&L", right: true, render: (r) => <span className={tone(r.pnl)}>{signedInr(r.pnl)}<div className="small">{pct(r.pnl_pct, 1)}</div></span> },
              { key: "stop", label: "Stop-loss", right: true, render: (r) => <div>{price(r.stop)}<div className="small muted">{pct(r.stop / r.ltp - 1, 1)} away</div></div> },
              { key: "value", label: "Value", right: true, render: (r) => <div>{inr(r.value)}<div className="small muted">{equity ? pct(r.value / equity, 0, false) : ""}</div></div> },
              { key: "thesis", label: "Entry thesis", render: (r) => <div className="sub-line">{r.thesis?.reason ?? "–"}</div> },
            ]} />
        </Card>
      )}
      {tab === "Closed trades" && (
        <Card pad={false}>
          <DataTable rows={tr} onRow={(r) => go(`stock/${r.symbol}`)} empty="No closed trades yet." cols={[
            { key: "symbol", label: "Stock", render: (r) => <span className="sym">{r.symbol}</span> },
            { key: "entry_date", label: "Held", render: (r) => <span className="nowrap">{dateShort(r.entry_date)} → {dateShort(r.exit_date)}<div className="small muted">{r.holding_days} days</div></span> },
            { key: "qty", label: "Qty", right: true },
            { key: "entry_price", label: "Entry", right: true, render: (r) => price(r.entry_price) },
            { key: "exit_price", label: "Exit", right: true, render: (r) => price(r.exit_price) },
            { key: "pnl", label: "P&L (net)", right: true, render: (r) => <span className={tone(r.pnl)}>{signedInr(r.pnl)}<div className="small">{pct(r.ret, 1)}</div></span> },
            { key: "costs", label: "Charges", right: true, render: (r) => inr(r.costs) },
            { key: "exit_reason", label: "Why it exited", render: (r) => <div className="sub-line">{r.exit_reason}</div> },
          ]} />
        </Card>
      )}
      {tab === "Allocation" && (
        <Card title="By sector">
          {Object.keys(bySector).length === 0 && <div className="muted">Nothing invested.</div>}
          {Object.entries(bySector).sort((a, b) => b[1] - a[1]).map(([sec, v]) => (
            <div key={sec} className="hbar" style={{ gridTemplateColumns: "200px 1fr 120px" }}>
              <span className="ink2">{sec}</span>
              <div className="track"><div className="fill" style={{ left: 0, width: `${(v / equity) * 100}%`, background: "var(--s1)" }} /></div>
              <span className="num right">{inr(v)} · {pct(v / equity, 0, false)}</span>
            </div>
          ))}
          <div className="hbar" style={{ gridTemplateColumns: "200px 1fr 120px" }}>
            <span className="ink2">Cash</span>
            <div className="track"><div className="fill" style={{ left: 0, width: `${((pos.data?.cash ?? 0) / (equity || 1)) * 100}%`, background: "var(--axis)" }} /></div>
            <span className="num right">{inr(pos.data?.cash)} · {pct((pos.data?.cash ?? 0) / (equity || 1), 0, false)}</span>
          </div>
        </Card>
      )}
    </>
  );
}
