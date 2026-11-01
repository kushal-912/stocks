import { useCallback, useEffect, useRef, useState } from "react";

// ------------------------------------------------------------------ api
export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = j.detail || j.message || msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json();
}

export const post = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export function usePoll<T = any>(path: string | null, intervalMs = 15000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message || String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    if (!intervalMs) return () => { alive.current = false; };
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    return () => {
      alive.current = false;
      window.clearInterval(id);
    };
  }, [load, intervalMs]);
  return { data, error, loading, refresh: load };
}

// ------------------------------------------------------------------ routing (hash)
export function useRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "").split("/").filter(Boolean);
  const [parts, setParts] = useState(parse);
  useEffect(() => {
    const f = () => {
      setParts(parse());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return parts;
}

export const go = (path: string) => {
  window.location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\//, "")}`;
};

// ------------------------------------------------------------------ theme
export function useThemeVersion(): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const bump = () => setV((x) => x + 1);
    mq.addEventListener("change", bump);
    const mo = new MutationObserver(bump);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", bump);
      mo.disconnect();
    };
  }, []);
  return v;
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function isDark(): boolean {
  const t = document.documentElement.dataset.theme;
  if (t) return t === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("qd-theme", next);
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------ formatting
const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const n2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const bad = (v: any) => v === null || v === undefined || Number.isNaN(v);

export const inr = (v: any, decimals = 0) => (bad(v) ? "–" : (decimals ? inr2 : inr0).format(v));
export const signedInr = (v: any) => (bad(v) ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${inr0.format(Math.abs(v))}`);
export const price = (v: any) => (bad(v) ? "–" : n2.format(v));
export const pct = (v: any, digits = 2, signed = true) =>
  bad(v) ? "–" : `${signed && v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(digits)}%`;
export const num = (v: any, digits = 2) => (bad(v) ? "–" : Number(v).toFixed(digits));
export const score = (v: any) => (bad(v) ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`);
export const tone = (v: any) => (bad(v) || v === 0 ? "" : v > 0 ? "pos" : "neg");

export function timeAgo(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export const hhmm = (iso?: string | null) => (iso ? iso.slice(11, 16) : "");
export const dateShort = (iso?: string | null) =>
  iso ? new Date(iso.length <= 10 ? iso + "T00:00:00" : iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "–";
