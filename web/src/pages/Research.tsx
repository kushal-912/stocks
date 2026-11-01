import { useState } from "react";
import { Card, DataTable, Empty, Loading, Meter, StatusBadge, Tabs } from "../components/ui";
import { dateShort, go, inr, pct, price, score, tone, usePoll } from "../lib";

export default function Research() {
  const days = usePoll<string[]>("/api/scan/days", 0);
  const [day, setDay] = useState<string | null>(null);
  const { data, loading } = usePoll<any>(`/api/scan${day ? `?day=${day}` : ""}`, 60000);
  const [tab, setTab] = useState("Deep research");
  if (loading && !data) return <Loading />;
  const s = data?.summary;
  if (!s) {
    return (
      <>
        <div className="page-head"><div><h1>Research</h1></div></div>
        <Card><Empty>No research yet. The agent scans the whole market every trading morning at 08:15 IST
          (or run <span className="mono">qd premarket</span>).</Empty></Card>
      </>
    );
  }
  const f = s.funnel ?? {};
  const reg = s.regime ?? {};
  const cands: any[] = data.candidates ?? [];
  const researched = cands.filter((c) => c.final_score !== null);
  const shown = tab === "Deep research" ? researched : cands;
  const steps: [string, any, string][] = [
    ["NSE equities", f.universe, "every EQ-series stock"],
    ["Liquid", f.liquid, "price ≥ ₹20, ≥ ₹2 Cr traded/day"],
    ["Uptrend", f.uptrend, "above 200-DMA, 50 > 200"],
    ["Eligible", f.eligible, "not overextended or too volatile"],
    ["Deep research", f.researched, "top-ranked + holdings"],
    ["Candidates", f.candidates, "passed vetoes & review"],
    ["Plan", (s.plan ?? []).length, "orders for the open"],
  ];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Research</h1>
          <p>Prices as of {dateShort(s.as_of)} · researched {data.created?.slice(11, 16)} IST · Claude review: {s.llm_review}</p>
        </div>
        <select value={day ?? data.day} onChange={(e) => setDay(e.target.value)}>
          {(days.data ?? [data.day]).map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      <Card title="Today's funnel" className="" pad>
        <div className="funnel">
          {steps.map(([l, n, sub]) => (
            <div className="step" key={l} title={sub}>
              <div className="n">{n ?? "–"}</div>
              <div className="l">{l}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid g-2" style={{ margin: "16px 0" }}>
        <Card title="Market regime">
          <div className="row" style={{ marginBottom: 10 }}>
            <span className={`badge ${reg.risk_on ? "good" : "bad"}`}>{reg.state}</span>
            <span className="ink2 small">
              {reg.risk_on ? "Full exposure allowed" : "Exposure capped - the agent buys less while the index is in a downtrend"}
            </span>
          </div>
          <dl className="kv">
            <dt>NIFTY 50</dt><dd>{price(reg.close)} ({pct((reg.pct_vs_sma200 ?? 0) / 100, 1)} vs 200-day avg)</dd>
            <dt>50 / 200-day avg</dt><dd>{price(reg.sma50)} / {price(reg.sma200)}</dd>
            <dt>Breadth</dt><dd>{pct(reg.breadth, 0, false)} of liquid stocks above their 200-day avg</dd>
            <dt>Equity / cash at plan time</dt><dd>{inr(s.equity)} / {inr(s.cash)}</dd>
          </dl>
        </Card>
        <Card title="Plan for the open">
          {(s.plan ?? []).length === 0 ? <div className="muted">No trades today - nothing cleared the bar, or the portfolio is already where it should be.</div> :
            (s.plan ?? []).map((a: any) => (
              <div key={a.symbol + a.action} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                <div className="row">
                  <span className={`badge ${a.action === "BUY" ? "blue" : "warn"}`}>{a.action}</span>
                  <a href={`#/stock/${a.symbol}`} className="sym">{a.symbol}</a>
                  <span className="num ink2">{a.qty} × ₹{price(a.ref_price)}{a.stop ? ` · stop ₹${price(a.stop)}` : ""}</span>
                  <span className={`num ${tone(a.score)}`} style={{ marginLeft: "auto" }}>{score(a.score)}</span>
                </div>
                <div className="small ink2" style={{ marginTop: 4 }}>{a.reason}</div>
              </div>
            ))}
        </Card>
      </div>

      <Tabs tabs={["Deep research", "Top 150 screened"]} active={tab} onChange={setTab} />
      <Card pad={false}>
        <DataTable rows={shown} onRow={(r) => go(`stock/${r.symbol}`)} initialSort={["final_score", "desc"]} cols={[
          { key: "rank", label: "#", right: true, sort: (r) => r.data?.rank, render: (r) => r.data?.rank ? Math.round(r.data.rank) : "–" },
          { key: "symbol", label: "Stock", render: (r) => <div><span className="sym">{r.symbol}</span><div className="sub-line">{r.data?.name ?? ""}{r.sector ? ` · ${r.sector}` : ""}</div></div> },
          { key: "screen_score", label: "Screen", right: true, render: (r) => <span className={tone(r.screen_score)}>{score(r.screen_score)}</span> },
          { key: "deep_score", label: "Deep", right: true, render: (r) => <span className={tone(r.deep_score)}>{score(r.deep_score)}</span> },
          { key: "final_score", label: "Final", right: true, render: (r) => r.final_score === null ? "–" :
            <div className="row" style={{ justifyContent: "flex-end" }}><Meter value={(r.final_score + 1) / 2} /><b className={`num ${tone(r.final_score)}`}>{score(r.final_score)}</b></div> },
          { key: "mom", label: "12-1m mom", right: true, sort: (r) => r.data?.mom_12_1, render: (r) => <span className={tone(r.data?.mom_12_1)}>{pct(r.data?.mom_12_1, 0)}</span> },
          { key: "hi", label: "of 52w high", right: true, sort: (r) => r.data?.high_52w, render: (r) => pct(r.data?.high_52w, 0, false) },
          { key: "vol", label: "Volatility", right: true, sort: (r) => r.data?.vol_63, render: (r) => pct(r.data?.vol_63, 0, false) },
          { key: "liq", label: "₹ Cr/day", right: true, sort: (r) => r.data?.traded_value_cr, render: (r) => r.data?.traded_value_cr?.toFixed(0) ?? "–" },
          { key: "verdict", label: "Verdict", render: (r) => r.verdict ? <StatusBadge status={r.verdict.length > 12 ? "reject" : r.verdict} /> : (r.data?.eligible ? <span className="muted small">eligible</span> : <span className="muted small">filtered</span>) },
        ]} />
      </Card>
    </>
  );
}
