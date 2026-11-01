import { Fragment, useState } from "react";
import { CandleChart } from "../components/Charts";
import { Card, DivBar, Empty, Loading, StatusBadge } from "../components/ui";
import { cssVar, dateShort, inr, num, pct, post, price, score, tone, usePoll, useThemeVersion } from "../lib";

export default function Stock({ symbol }: { symbol: string }) {
  useThemeVersion();
  const { data, loading, error } = usePoll<any>(`/api/stock/${symbol}`, 0);
  const [fresh, setFresh] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (loading && !data) return <Loading what={`Loading ${symbol}`} />;
  if (error || !data) return <Card><Empty>{error ?? "Not found"}</Empty></Card>;

  const research = fresh ?? data.research;
  const rep = research?.report;
  const sent = research?.sentiment ?? rep?.sentiment;
  const bars = data.bars ?? [];
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  const pos = data.position;
  const markers = (data.orders ?? []).map((o: any) => ({
    time: o.ts.slice(0, 10), position: o.side === "BUY" ? "belowBar" : "aboveBar",
    color: o.side === "BUY" ? cssVar("--s1") : cssVar("--s2"), shape: o.side === "BUY" ? "arrowUp" : "arrowDown",
    text: `${o.side} ${o.qty}`,
  }));
  const lines = pos ? [
    { price: pos.stop, title: "stop", color: cssVar("--neg-mark") },
    { price: pos.entry_price, title: "entry", color: cssVar("--muted") },
  ] : [];
  const run = async () => {
    setBusy(true); setErr(null);
    try { setFresh(await post(`/api/stock/${symbol}/research`, {})); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const comps = rep?.components ?? data.candidate?.data?.components ?? {};

  return (
    <>
      <div className="page-head">
        <div>
          <div className="row"><h1>{symbol}</h1>{pos && <span className="badge blue">held</span>}</div>
          <p>{data.info?.name ?? ""}{data.info?.sector ? ` · ${data.info.sector}` : ""}</p>
        </div>
        <div className="row">
          {last && <div className="right"><div style={{ fontSize: 22, fontWeight: 650 }} className="num">₹{price(last.close)}</div>
            {prev && <div className={`small num ${tone(last.close - prev.close)}`}>{pct(last.close / prev.close - 1)} · {last.time}</div>}</div>}
          <button className="btn" onClick={run} disabled={busy}>{busy ? "Researching…" : "Run fresh research"}</button>
        </div>
      </div>
      {err && <div className="callout bad" style={{ marginBottom: 12 }}>{err}</div>}

      <Card pad>
        <CandleChart bars={bars} height={420} markers={markers as any} lines={lines} overlays={[
          { name: "50-day", color: cssVar("--s4"), data: data.sma50 },
          { name: "200-day", color: cssVar("--s7"), data: data.sma200 },
        ]} />
      </Card>

      <div className="grid g-3" style={{ marginTop: 16 }}>
        <Card title="Model votes" action={research?.final_score !== undefined && research?.final_score !== null ?
          <span className={`num ${tone(research.final_score)}`}>final {score(research.final_score)}</span> : undefined}>
          {Object.keys(comps).length === 0 ? <div className="muted">Not researched yet - press “Run fresh research”.</div> :
            Object.entries(comps).map(([k, v]) => <DivBar key={k} label={k.replace("_", " ")} value={v as number} />)}
          {research?.day && <div className="small muted" style={{ marginTop: 8 }}>research from {dateShort(research.day)}{research.on_demand ? " (on demand)" : ""}</div>}
        </Card>
        <Card title="Forecast & volatility">
          {rep?.forecast ? (
            <dl className="kv">
              <dt>{rep.forecast.horizon}-day expected</dt><dd className={tone(rep.forecast.exp_return)}>{pct(rep.forecast.exp_return)} ± {pct(rep.forecast.std, 2, false)}</dd>
              <dt>P(up), ensemble</dt><dd>{num(rep.forecast.prob_up)}</dd>
              {(rep.forecasts ?? []).map((f: any) => <Fragment key={f.model}><dt>{f.model}</dt><dd>{pct(f.exp_return)} (t {num(f.t_stat)})</dd></Fragment>)}
              {rep.volatility && <><dt>Volatility now / fcst</dt><dd>{pct(rep.volatility.current_vol, 0, false)} / {pct(rep.volatility.forecast_vol, 0, false)}</dd></>}
              <dt>Regime</dt><dd>{rep.regime ?? "–"}</dd>
              <dt>Character</dt><dd>{rep.diagnostics?.character} (Hurst {num(rep.diagnostics?.hurst)})</dd>
            </dl>
          ) : <div className="muted">–</div>}
        </Card>
        <Card title="Machine learning">
          {rep?.ml ? (
            <dl className="kv">
              <dt>P(up in {rep.ml.horizon}d)</dt><dd><b>{num(rep.ml.prob_up)}</b></dd>
              <dt>Out-of-sample accuracy</dt><dd>{pct(rep.ml.oos_accuracy, 1, false)} vs {pct(Math.max(rep.ml.base_rate, 1 - rep.ml.base_rate), 1, false)} naive</dd>
              <dt>AUC</dt><dd>{num(rep.ml.oos_auc, 3)}</dd>
              <dt>Predictions tested</dt><dd>{rep.ml.n_oos}</dd>
              <dt>Top features</dt><dd className="small">{Object.keys(rep.ml.top_features ?? {}).slice(0, 4).join(", ")}</dd>
            </dl>
          ) : <div className="muted">–</div>}
          {(rep?.notes ?? []).map((n: string) => <div key={n} className="small muted" style={{ marginTop: 6 }}>{n}</div>)}
        </Card>
      </div>

      <div className="grid g-2" style={{ marginTop: 16 }}>
        <Card title="News" action={sent ? <span className={`num ${tone(sent.score)}`}>{score(sent.score)} · {sent.method}</span> : undefined}>
          {(sent?.headlines ?? []).length === 0 ? <div className="muted">No recent headlines.</div> :
            sent.headlines.map((h: any) => (
              <div key={h.uid} style={{ padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <b className={`num ${tone(h.sentiment)}`} style={{ minWidth: 44 }}>{score(h.sentiment)}</b>
                  <div><a href={h.url} target="_blank" rel="noreferrer">{h.title}</a>
                    <div className="small muted">{h.source} · {dateShort(h.published)}{h.rationale ? ` · ${h.rationale}` : ""}</div></div>
                </div>
              </div>
            ))}
        </Card>
        <Card title="Agent's history with this stock">
          {pos && (
            <div className="callout" style={{ marginBottom: 10 }}>
              Holding {pos.qty} since {dateShort(pos.entry_date)} at ₹{price(pos.entry_price)} · stop ₹{price(pos.stop)}
            </div>
          )}
          {data.candidate?.data?.review && (
            <div className="callout" style={{ marginBottom: 10 }}>
              <StatusBadge status={data.candidate.data.review.verdict} /> Claude: {data.candidate.data.review.rationale}
            </div>
          )}
          {data.candidate?.data?.rationale && <div className="small ink2" style={{ marginBottom: 10 }}>{data.candidate.data.rationale}</div>}
          {(data.trades ?? []).length === 0 && !pos ? <div className="muted">The agent has never traded {symbol}.</div> :
            (data.trades ?? []).map((t: any) => (
              <div key={t.id} className="row small" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <span className="num">{dateShort(t.entry_date)} → {dateShort(t.exit_date)}</span>
                <span className={`num ${tone(t.pnl)}`}>{inr(t.pnl)} ({pct(t.ret, 1)})</span>
                <span className="ink2">{t.exit_reason}</span>
              </div>
            ))}
        </Card>
      </div>
    </>
  );
}
