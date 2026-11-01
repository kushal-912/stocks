import { LineChart } from "../components/Charts";
import { Card, DataTable, Empty, Kpi, Loading, StatusBadge } from "../components/ui";
import { cssVar, dateShort, go, hhmm, inr, pct, price, score, signedInr, tone, usePoll, useThemeVersion } from "../lib";

const PHASES: [string, string][] = [["premarket", "Research & plan"], ["open", "Execute at open"], ["cancel", "Cancel unfilled"], ["eod", "Close & snapshot"]];

export function Timeline({ status }: { status: any }) {
  const ph = status?.phases ?? {};
  const sch = status?.schedule ?? {};
  const times: Record<string, string> = { premarket: sch.premarket, open: sch.open, cancel: sch.cancel_entries, eod: sch.eod };
  return (
    <div className="timeline">
      {PHASES.map(([k, label]) => {
        const p = ph[k];
        return (
          <div key={k} className={`ph ${p?.status ?? ""}`}>
            <div><b>{label}</b></div>
            <div className="t num">{p?.finished ? `done ${hhmm(p.finished)}` : p?.status ?? `at ${times[k] ?? "–"}`}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function Overview({ status }: { status: any }) {
  useThemeVersion();
  const { data, loading } = usePoll<any>("/api/overview", 20000);
  if (loading && !data) return <Loading />;
  const snap = data?.snapshot ?? {};
  const m = data?.metrics ?? {};
  const started = !!status?.inception;
  const curve = data?.curve ?? [];
  const exposure = snap.equity ? snap.invested / snap.equity : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>
            Autonomous {status?.mode === "live" ? "live" : "paper"} trading · started {status?.inception ? dateShort(status.inception.date) : "–"} with{" "}
            {inr(snap.initial_capital ?? status?.inception?.capital)}
          </p>
        </div>
        <Timeline status={status} />
      </div>

      {!started && (
        <div className="callout warn" style={{ marginBottom: 16 }}>
          The agent hasn't started yet. Run <span className="mono">qd start</span> - it researches the market before the
          open (08:15 IST), trades at 09:20 and manages positions all day. Nothing for you to do after that.
        </div>
      )}

      <div className="kpis">
        <Kpi label="Equity" value={inr(snap.equity)} sub={pct(m.total_return)} subTone={tone(m.total_return)}
          hint="Cash + market value of the agent's positions" />
        <Kpi label="vs NIFTY 50" value={<span className={tone(m.excess_return)}>{pct(m.excess_return)}</span>}
          sub={`NIFTY ${pct(m.benchmark_return)}`} hint="Agent return minus buy-and-hold NIFTY over the same period" />
        <Kpi label="Today" value={<span className={tone(data?.today_pnl)}>{signedInr(data?.today_pnl)}</span>}
          sub={data?.today_base ? pct(data.today_pnl / data.today_base) : undefined} subTone={tone(data?.today_pnl)} />
        <Kpi label="Max drawdown" value={pct(m.max_drawdown, 1)} sub={`NIFTY ${pct(m.benchmark_max_drawdown, 1)}`} />
        <Kpi label="Invested" value={exposure === null ? "–" : pct(exposure, 0, false)}
          sub={`${inr(snap.cash)} cash`} hint="Share of equity in stocks" />
        <Kpi label="Positions" value={snap.positions?.length ?? 0} sub={`${inr(snap.realised_pnl)} realised`}
          subTone={tone(snap.realised_pnl)} />
      </div>

      <div className="grid g-main" style={{ marginBottom: 16 }}>
        <Card title="Equity vs NIFTY 50" action={<span className="muted small">same starting capital</span>}>
          {curve.length > 1 ? (
            <LineChart height={300} lines={[
              { name: "Agent", color: cssVar("--s1"), data: curve.map((p: any) => ({ time: p.day, value: p.equity })) },
              { name: "NIFTY 50", color: cssVar("--s2"), data: curve.filter((p: any) => p.benchmark).map((p: any) => ({ time: p.day, value: p.benchmark })) },
            ]} />
          ) : (
            <Empty>The curve appears after the first trading day closes.</Empty>
          )}
        </Card>
        <Card title="Scorecard" action={<a href="#/evaluation" className="small">details →</a>}>
          {(data?.verdict ?? []).map((v: any) => (
            <div key={v.check} className="row" style={{ alignItems: "flex-start", padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
              <StatusBadge status={v.status} />
              <div><b>{v.check}</b><div className="small ink2">{v.detail}</div></div>
            </div>
          ))}
        </Card>
      </div>

      <div className="grid g-main" style={{ marginBottom: 16 }}>
        <Card title="Holdings" action={<a href="#/portfolio" className="small">portfolio →</a>} pad={false}>
          <DataTable rows={snap.positions ?? []} onRow={(r) => go(`stock/${r.symbol}`)} empty="No open positions."
            initialSort={["value", "desc"]} cols={[
              { key: "symbol", label: "Stock", render: (r) => <span className="sym">{r.symbol}</span> },
              { key: "qty", label: "Qty", right: true },
              { key: "entry_price", label: "Avg", right: true, render: (r) => price(r.entry_price) },
              { key: "ltp", label: "LTP", right: true, render: (r) => price(r.ltp) },
              { key: "pnl", label: "P&L", right: true, render: (r) => <span className={tone(r.pnl)}>{signedInr(r.pnl)} <span className="small">({pct(r.pnl_pct, 1)})</span></span> },
              { key: "stop", label: "Stop", right: true, render: (r) => <span title="protective stop-loss">{price(r.stop)}</span> },
              { key: "value", label: "Value", right: true, render: (r) => inr(r.value) },
            ]} />
        </Card>
        <Card title="Agent activity" action={<a href="#/activity" className="small">all →</a>}>
          <ul className="feed">
            {(data?.events ?? []).slice(0, 14).map((e: any) => (
              <li key={e.id}>
                <span className="when">{e.ts.slice(5, 10) === new Date().toISOString().slice(5, 10) ? hhmm(e.ts) : dateShort(e.ts)}</span>
                <span className={e.level}><span className="kind">{e.kind.replace("_", " ")}</span>{e.message}</span>
              </li>
            ))}
            {!data?.events?.length && <Empty>No activity yet.</Empty>}
          </ul>
        </Card>
      </div>

      <Card title="Latest decisions" action={<a href="#/research" className="small">today's research →</a>} pad={false}>
        <DataTable rows={data?.recent_decisions ?? []} onRow={(r) => go(`stock/${r.symbol}`)} empty="No decisions yet."
          cols={[
            { key: "day", label: "Day", render: (r) => dateShort(r.day) },
            { key: "action", label: "Action", render: (r) => <span className={`badge ${r.action === "BUY" ? "blue" : "warn"}`}>{r.action}</span> },
            { key: "symbol", label: "Stock", render: (r) => <span className="sym">{r.symbol}</span> },
            { key: "qty", label: "Qty", right: true },
            { key: "score", label: "Score", right: true, render: (r) => <span className={tone(r.score)}>{score(r.score)}</span> },
            { key: "reason", label: "Why", render: (r) => <div className="sub-line">{r.reason}</div> },
            { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
          ]} />
      </Card>
    </>
  );
}
