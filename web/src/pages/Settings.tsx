import { Fragment } from "react";
import { Card, Loading } from "../components/ui";
import { inr, usePoll } from "../lib";

const LABELS: Record<string, string> = {
  initial_capital: "Starting capital", max_positions: "Max positions", min_position_value: "Min position size",
  max_per_sector: "Max per sector", shortlist_size: "Stocks deep-researched daily", min_final_score: "Min conviction to buy",
  exit_score: "Sell below conviction", swap_margin: "Swap margin", min_hold_days: "Min holding (days)",
  max_hold_days: "Time stop (days)", stop_atr: "Initial stop (× ATR)", trail_atr: "Trailing stop (× ATR)",
  max_gap_up_pct: "Skip gap-ups above %", regime_filter: "Index regime filter", risk_off_max_exposure_pct: "Risk-off max exposure %",
  llm_review: "Claude reviews buys", risk_per_trade_pct: "Risk per trade %", max_position_pct: "Max position % of equity",
  max_daily_loss_pct: "Daily loss limit → kill switch %", max_orders_per_second: "Orders per second (SEBI < 10)",
  max_orders_per_day: "Orders per day", fat_finger_pct: "Fat-finger limit %", min_traded_value_cr: "Min ₹ Cr traded/day",
  min_price: "Min price ₹",
};

function Table({ obj, keys }: { obj: any; keys: string[] }) {
  return (
    <dl className="kv">
      {keys.filter((k) => obj?.[k] !== undefined).map((k) => (
        <Fragment key={k}><dt>{LABELS[k] ?? k}</dt><dd>{typeof obj[k] === "boolean" ? (obj[k] ? "on" : "off") :
          k.includes("capital") || k.includes("value") ? inr(obj[k]) : String(obj[k])}</dd></Fragment>
      ))}
    </dl>
  );
}

export default function Settings({ status }: { status: any }) {
  const { data, loading } = usePoll<any>("/api/settings", 0);
  if (loading && !data) return <Loading />;
  const live = status?.mode === "live";
  return (
    <>
      <div className="page-head">
        <div><h1>Settings</h1><p>Read-only here. Change values in <span className="mono">config/settings.local.yaml</span> and restart <span className="mono">qd start</span>.</p></div>
      </div>
      <div className="grid g-3">
        <Card title="Agent policy">
          <Table obj={data?.agent} keys={["initial_capital", "max_positions", "min_position_value", "max_per_sector", "shortlist_size",
            "min_final_score", "exit_score", "swap_margin", "min_hold_days", "max_hold_days", "stop_atr", "trail_atr", "max_gap_up_pct",
            "regime_filter", "risk_off_max_exposure_pct", "llm_review"]} />
        </Card>
        <Card title="Hard risk limits (every order)">
          <Table obj={data?.risk} keys={["risk_per_trade_pct", "max_position_pct", "max_daily_loss_pct", "max_orders_per_second",
            "max_orders_per_day", "fat_finger_pct"]} />
          <h3 style={{ margin: "16px 0 8px" }}>Universe</h3>
          <Table obj={data?.universe} keys={["min_price", "min_traded_value_cr"]} />
        </Card>
        <Card title="Execution">
          <dl className="kv">
            <dt>Mode</dt><dd><span className={`badge ${live ? "bad" : "blue"}`}>{live ? "LIVE" : "PAPER"}</span></dd>
            <dt>Broker</dt><dd>{live ? data?.execution?.broker : "paper exchange simulator"}</dd>
            <dt>Charges model</dt><dd>{data?.costs?.profile}</dd>
            <dt>Claude</dt><dd>{status?.llm?.configured ? `${data?.llm?.model} (key found)` : "no API key - quant-only decisions"}</dd>
            <dt>Order tag</dt><dd className="mono">{data?.execution?.algo_tag}</dd>
          </dl>
        </Card>
      </div>
      <div style={{ height: 16 }} />
      <Card title="Moving from paper to Groww (after the evaluation)">
        <ol className="small ink2" style={{ lineHeight: 1.8, margin: 0, paddingLeft: 18 }}>
          <li>Subscribe to the Groww Trading API and create an API key (or TOTP credentials) - put them in <span className="mono">.env</span>.</li>
          <li>Whitelist the static IP of the machine running QuantDesk in Groww's API settings (SEBI requirement).</li>
          <li><span className="mono">pip install -e ".[groww]"</span>, then <span className="mono">qd broker login groww</span> and <span className="mono">qd broker test groww</span> (read-only).</li>
          <li>In <span className="mono">settings.local.yaml</span>: <span className="mono">execution: {"{"}mode: live, broker: groww{"}"}</span> and set <span className="mono">agent.initial_capital</span> to the amount you allot.</li>
          <li>Add <span className="mono">QD_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK</span> to <span className="mono">.env</span>. Without it every live order is refused.</li>
        </ol>
        <div className="callout" style={{ marginTop: 12 }}>
          Nothing else changes: the agent, policy, risk guard and schedule are the exact same code that ran the paper evaluation.
          The live account is tracked as its own sub-account, so the agent never touches holdings it didn't buy.
        </div>
      </Card>
    </>
  );
}
