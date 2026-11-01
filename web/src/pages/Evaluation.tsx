import { LineChart } from "../components/Charts";
import { Card, DataTable, Empty, Kpi, Loading, StatusBadge } from "../components/ui";
import { cssVar, inr, num, pct, signedInr, tone, usePoll, useThemeVersion } from "../lib";

export default function Evaluation() {
  useThemeVersion();
  const { data, loading } = usePoll<any>("/api/evaluation", 60000);
  if (loading && !data) return <Loading />;
  const m = data?.metrics ?? {};
  const t = data?.trades;
  const curve = data?.curve ?? [];
  const dd = (() => {
    let peak = -Infinity;
    return curve.map((p: any) => { peak = Math.max(peak, p.equity); return { time: p.day, value: p.equity / peak - 1 }; });
  })();
  const bdd = (() => {
    let peak = -Infinity;
    return curve.filter((p: any) => p.benchmark).map((p: any) => { peak = Math.max(peak, p.benchmark); return { time: p.day, value: p.benchmark / peak - 1 }; });
  })();
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Evaluation</h1>
          <p>The agent's report card against simply buying NIFTY 50 with the same money on the same day. {data?.days ?? 0} trading days recorded.</p>
        </div>
      </div>
      <div className="grid g-2" style={{ marginBottom: 16 }}>
        <Card title="Verdict so far">
          {(data?.verdict ?? []).map((v: any) => (
            <div key={v.check} className="row" style={{ alignItems: "flex-start", padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
              <StatusBadge status={v.status} /><div><b>{v.check}</b><div className="small ink2">{v.detail}</div></div>
            </div>
          ))}
        </Card>
        <Card title="How to read this">
          <div className="small ink2" style={{ lineHeight: 1.6 }}>
            Judge the agent on <b>risk-adjusted excess return</b>, not raw profit: a strategy that makes 8% while NIFTY makes 12%
            lost to a free alternative. Six months is short - daily returns are noisy enough that an alpha t-stat below ~2 can
            easily be luck in either direction. Look at drawdowns too: how much it lost at its worst matters as much as where
            it ended. All figures are after brokerage, STT, stamp duty, exchange fees, GST and DP charges, before income tax.
          </div>
        </Card>
      </div>
      <div className="kpis">
        <Kpi label="Return" value={<span className={tone(m.total_return)}>{pct(m.total_return)}</span>} sub={`NIFTY ${pct(m.benchmark_return)}`} />
        <Kpi label="Excess return" value={<span className={tone(m.excess_return)}>{pct(m.excess_return)}</span>} sub={`alpha t-stat ${num(m.alpha_t_stat)}`} />
        <Kpi label="Sharpe" value={num(m.sharpe)} sub={`Sortino ${num(m.sortino)}`} />
        <Kpi label="Volatility" value={pct(m.annual_vol, 1, false)} sub={`beta ${num(m.beta)}`} />
        <Kpi label="Max drawdown" value={pct(m.max_drawdown, 1)} sub={`NIFTY ${pct(m.benchmark_max_drawdown, 1)}`} />
        <Kpi label="Avg invested" value={pct(m.avg_exposure, 0, false)} sub="of equity" />
      </div>
      <div className="grid g-2" style={{ marginBottom: 16 }}>
        <Card title="Growth of capital">
          {curve.length > 1 ? <LineChart height={280} lines={[
            { name: "Agent", color: cssVar("--s1"), data: curve.map((p: any) => ({ time: p.day, value: p.equity })) },
            { name: "NIFTY 50", color: cssVar("--s2"), data: curve.filter((p: any) => p.benchmark).map((p: any) => ({ time: p.day, value: p.benchmark })) },
          ]} /> : <Empty>Waiting for the first closed trading day.</Empty>}
        </Card>
        <Card title="Drawdown from peak">
          {curve.length > 1 ? <LineChart height={280} mode="pct" lines={[
            { name: "Agent", color: cssVar("--s1"), data: dd },
            { name: "NIFTY 50", color: cssVar("--s2"), data: bdd },
          ]} /> : <Empty>–</Empty>}
        </Card>
      </div>
      <div className="grid g-2">
        <Card title="Monthly returns" pad={false}>
          <DataTable rows={[...(data?.monthly ?? [])].reverse()} empty="No complete months yet." cols={[
            { key: "month", label: "Month" },
            { key: "return", label: "Agent", right: true, render: (r) => <span className={tone(r.return)}>{pct(r.return)}</span> },
            { key: "benchmark", label: "NIFTY 50", right: true, render: (r) => <span className={tone(r.benchmark)}>{pct(r.benchmark)}</span> },
            { key: "diff", label: "Difference", right: true, sort: (r) => r.return - (r.benchmark ?? 0),
              render: (r) => r.benchmark === null ? "–" : <b className={tone(r.return - r.benchmark)}>{pct(r.return - r.benchmark)}</b> },
          ]} />
        </Card>
        <Card title="Trading statistics">
          {!t ? <div className="muted">No closed trades yet.</div> : (
            <>
              <dl className="kv">
                <dt>Closed trades</dt><dd>{t.count}</dd>
                <dt>Win rate</dt><dd>{pct(t.win_rate, 0, false)}</dd>
                <dt>Average win / loss</dt><dd><span className="pos">{inr(t.avg_win)} ({pct(t.avg_win_pct, 1)})</span> / <span className="neg">{inr(t.avg_loss)} ({pct(t.avg_loss_pct, 1)})</span></dd>
                <dt>Profit factor</dt><dd>{num(t.profit_factor)}</dd>
                <dt>Expectancy per trade</dt><dd className={tone(t.expectancy)}>{signedInr(t.expectancy)}</dd>
                <dt>Average holding</dt><dd>{num(t.avg_holding_days, 1)} days</dd>
                <dt>Charges paid</dt><dd>{inr(t.total_costs)}</dd>
              </dl>
              <h3 style={{ margin: "16px 0 6px" }}>P&L by exit reason</h3>
              {(t.by_exit_reason ?? []).map((r: any) => (
                <div key={r.reason} className="row small" style={{ justifyContent: "space-between", padding: "4px 0" }}>
                  <span className="ink2">{r.reason} ({r.count})</span><b className={`num ${tone(r.sum)}`}>{signedInr(r.sum)}</b>
                </div>
              ))}
            </>
          )}
        </Card>
      </div>
    </>
  );
}
