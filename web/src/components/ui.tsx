import { useMemo, useState, type ReactNode } from "react";
import { score, tone } from "../lib";

export function Card({ title, action, children, pad = true, className = "" }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; pad?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="card-head">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {action}
        </div>
      )}
      <div className={pad ? "card-body" : ""}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, subTone, hint }: {
  label: string; value: ReactNode; sub?: ReactNode; subTone?: string; hint?: string;
}) {
  return (
    <div className="card kpi" title={hint}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub !== undefined && <div className={`sub ${subTone ?? ""}`}>{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <div className="spinner" aria-label="loading" />;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="empty row" style={{ justifyContent: "center" }}><Spinner /> {what}…</div>;
}

/** Diverging bar in [-1, 1]: blue = bullish, red = bearish, neutral zero line. */
export function DivBar({ label, value, title }: { label: string; value: number | null | undefined; title?: string }) {
  const v = value ?? 0;
  const w = Math.min(Math.abs(v), 1) * 50;
  return (
    <div className="hbar" title={title}>
      <span className="ink2">{label}</span>
      <div className="track">
        <div className="mid" />
        {value !== null && value !== undefined && (
          <div className="fill" style={{ left: v >= 0 ? "50%" : `${50 - w}%`, width: `${w}%`,
            background: v >= 0 ? "var(--s1)" : "var(--neg-mark)" }} />
        )}
      </div>
      <span className={`num right ${tone(value)}`}>{score(value)}</span>
    </div>
  );
}

export function Meter({ value, max = 1 }: { value: number | null | undefined; max?: number }) {
  const w = Math.max(0, Math.min(1, (value ?? 0) / max)) * 100;
  return <div className="meter"><div style={{ width: `${w}%` }} /></div>;
}

export function StatusBadge({ status }: { status?: string | null }) {
  const map: Record<string, string> = {
    COMPLETE: "good", done: "good", pass: "good", approve: "good", submitted: "blue", OPEN: "blue", running: "blue",
    planned: "blue", REJECTED: "bad", BLOCKED: "bad", failed: "bad", fail: "bad", reject: "bad", CANCELLED: "",
    EXPIRED: "", skipped: "", void: "", warn: "warn", pending: "",
  };
  return <span className={`badge ${map[status ?? ""] ?? ""}`}>{status ?? "–"}</span>;
}

export type Col<T> = {
  key: string; label: ReactNode; render?: (row: T) => ReactNode; sort?: (row: T) => any; right?: boolean; width?: number;
};

export function DataTable<T extends Record<string, any>>({ rows, cols, onRow, empty = "Nothing yet.", initialSort, limit }: {
  rows: T[]; cols: Col<T>[]; onRow?: (r: T) => void; empty?: ReactNode; initialSort?: [string, "asc" | "desc"]; limit?: number;
}) {
  const [sort, setSort] = useState<[string, "asc" | "desc"] | null>(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = cols.find((c) => c.key === sort[0]);
    if (!col) return rows;
    const get = col.sort ?? ((r: T) => r[col.key]);
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      const c = x === y ? 0 : x === null || x === undefined ? 1 : y === null || y === undefined ? -1 : x > y ? 1 : -1;
      return sort[1] === "asc" ? c : -c;
    });
  }, [rows, sort, cols]);
  if (!rows.length) return <Empty>{empty}</Empty>;
  const shown = limit ? sorted.slice(0, limit) : sorted;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={`sortable ${c.right ? "r" : ""}`} style={c.width ? { width: c.width } : undefined}
                onClick={() => setSort((s) => [c.key, s && s[0] === c.key && s[1] === "desc" ? "asc" : "desc"])}>
                {c.label}{sort?.[0] === c.key ? (sort[1] === "desc" ? " ↓" : " ↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i} className={onRow ? "clickable" : ""} onClick={onRow ? () => onRow(r) : undefined}>
              {cols.map((c) => (
                <td key={c.key} className={c.right ? "r" : ""}>{c.render ? c.render(r) : r[c.key] ?? "–"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t} role="tab" className={t === active ? "active" : ""} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  );
}

const paths: Record<string, string> = {
  overview: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  portfolio: "M20 7h-4V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2H4a2 2 0 00-2 2v10a2 2 0 002 2h16a2 2 0 002-2V9a2 2 0 00-2-2zM10 5h4v2h-4V5z",
  research: "M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16a6.47 6.47 0 004.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
  activity: "M13 3a9 9 0 00-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 112.05 4.95l-1.42 1.42A9 9 0 1013 3zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  evaluation: "M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z",
  backtest: "M12 2a10 10 0 100 20 10 10 0 000-20zm-1 15v-2H9v-2h2v-2H9V9h2V7h2v2h2v2h-2v2h2v2h-2v2h-2z",
  settings: "M19.14 12.94a7.07 7.07 0 000-1.88l2.03-1.58-1.92-3.32-2.39.96a7.03 7.03 0 00-1.62-.94L14.88 3h-3.84l-.36 2.18c-.58.24-1.12.55-1.62.94l-2.39-.96L4.75 8.48l2.03 1.58a7.07 7.07 0 000 1.88l-2.03 1.58 1.92 3.32 2.39-.96c.5.39 1.04.7 1.62.94l.36 2.18h3.84l.36-2.18c.58-.24 1.12-.55 1.62-.94l2.39.96 1.92-3.32-2.03-1.58zM12 15.5A3.5 3.5 0 1112 8.5a3.5 3.5 0 010 7z",
  search: "M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16a6.47 6.47 0 004.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
  theme: "M12 3a9 9 0 109 9c0-.46-.04-.92-.1-1.36a5.4 5.4 0 01-4.4 2.26 5.4 5.4 0 01-3.14-9.8A9.4 9.4 0 0012 3z",
  shield: "M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z",
};

export function Icon({ name, size = 18 }: { name: keyof typeof paths | string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden>
      <path d={paths[name] ?? paths.overview} />
    </svg>
  );
}
