import { useState } from "react";
import { Card, DataTable, Loading, StatusBadge, Tabs } from "../components/ui";
import { api, dateShort, go, price, usePoll } from "../lib";

export default function Activity() {
  const [tab, setTab] = useState("Event log");
  const [kind, setKind] = useState("");
  const ev = usePoll<any[]>(`/api/events?limit=400${kind ? `&kind=${kind}` : ""}`, 20000);
  const orders = usePoll<any[]>("/api/orders", 20000);
  const [verify, setVerify] = useState<any>(null);
  const kinds = Array.from(new Set((ev.data ?? []).map((e) => e.kind))).sort();
  return (
    <>
      <div className="page-head">
        <div><h1>Activity</h1><p>Every decision, order and fill - recorded in a hash-chained ledger that can't be quietly edited.</p></div>
        <div className="row">
          {verify && <span className={`badge ${verify.ok ? "good" : "bad"}`}>{verify.ok ? `Ledger intact · ${verify.checked} events` : `Tampered at #${verify.broken_at_id}`}</span>}
          <button className="btn" onClick={async () => setVerify(await api("/api/verify"))}>Verify integrity</button>
        </div>
      </div>
      <Tabs tabs={["Event log", "Orders"]} active={tab} onChange={setTab} />
      {tab === "Event log" && (
        <Card pad={false} title={<select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All events</option>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>}>
          {ev.loading && !ev.data ? <Loading /> : (
            <DataTable rows={ev.data ?? []} cols={[
              { key: "ts", label: "Time", render: (r) => <span className="num nowrap">{dateShort(r.ts)} {r.ts.slice(11, 19)}</span> },
              { key: "kind", label: "Event", render: (r) => <span className={`badge ${r.level === "error" ? "bad" : r.level === "warning" ? "warn" : ""}`}>{r.kind}</span> },
              { key: "message", label: "Detail", render: (r) => <span className={r.level === "error" ? "neg" : ""}>{r.message}</span> },
              { key: "hash", label: "Hash", render: (r) => <span className="mono muted" title={r.hash}>{r.hash.slice(0, 10)}</span> },
            ]} />
          )}
        </Card>
      )}
      {tab === "Orders" && (
        <Card pad={false}>
          <DataTable rows={orders.data ?? []} onRow={(r) => go(`stock/${r.symbol}`)} empty="No orders yet." cols={[
            { key: "ts", label: "Time", render: (r) => <span className="num nowrap">{dateShort(r.ts)} {r.ts.slice(11, 16)}</span> },
            { key: "purpose", label: "Purpose", render: (r) => <span className="badge">{r.purpose}</span> },
            { key: "side", label: "Side", render: (r) => <b className={r.side === "BUY" ? "pos" : "neg"}>{r.side}</b> },
            { key: "symbol", label: "Stock", render: (r) => <span className="sym">{r.symbol}</span> },
            { key: "qty", label: "Qty", right: true },
            { key: "order_type", label: "Type" },
            { key: "price", label: "Limit / trigger", right: true, render: (r) => r.price ? price(r.price) : r.trigger_price ? `trg ${price(r.trigger_price)}` : "MKT" },
            { key: "avg_price", label: "Filled at", right: true, render: (r) => r.avg_price ? price(r.avg_price) : "–" },
            { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
            { key: "reason", label: "Reason", render: (r) => <div className="sub-line">{r.reason}{r.message && r.status !== "COMPLETE" ? ` · ${r.message}` : ""}</div> },
          ]} />
        </Card>
      )}
    </>
  );
}
