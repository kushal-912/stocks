import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { cssVar, useThemeVersion } from "../lib";

export type Pt = { time: string; value: number };
export type LineSpec = { name: string; color: string; data: Pt[]; width?: number; dashed?: boolean };

function baseOptions(height: number) {
  return {
    height,
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: cssVar("--muted"),
      fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
      fontSize: 11,
    },
    grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--grid") } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: false },
    crosshair: {
      mode: CrosshairMode.Magnet,
      vertLine: { color: cssVar("--axis"), labelBackgroundColor: cssVar("--ink-2") },
      horzLine: { color: cssVar("--axis"), labelBackgroundColor: cssVar("--ink-2") },
    },
    handleScale: { mouseWheel: false },
  };
}

const fmtVal = (v: number, mode: "inr" | "pct" | "price") =>
  mode === "pct"
    ? `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`
    : mode === "inr"
      ? `₹${Math.round(v).toLocaleString("en-IN")}`
      : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });

/** Multi-line time series with a hover legend. The chart is built once and its data updated in
 *  place, so it can stream (e.g. a backtest growing day by day) without flicker. */
export function LineChart({ lines, height = 300, mode = "inr", area = false }: {
  lines: LineSpec[]; height?: number; mode?: "inr" | "pct" | "price"; area?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line" | "Area">[]>([]);
  const theme = useThemeVersion();
  const [hover, setHover] = useState<Record<string, number> | null>(null);
  const shape = lines.map((l) => `${l.name}|${l.color}|${l.dashed ? 1 : 0}`).join(",");
  const dataSig = lines.map((l) => `${l.data.length}:${l.data.length ? l.data[l.data.length - 1].value : ""}:${l.data.length ? l.data[0].time : ""}`).join(",");
  useEffect(() => {
    if (!el.current) return;
    const chart: IChartApi = createChart(el.current, baseOptions(height));
    chart.applyOptions({ localization: { priceFormatter: (v: number) => fmtVal(v, mode) } });
    const series: ISeriesApi<"Line" | "Area">[] = lines.map((l, i) =>
      area && i === 0
        ? chart.addAreaSeries({ lineColor: l.color, topColor: l.color + "33", bottomColor: l.color + "00", lineWidth: 2,
            priceLineVisible: false, lastValueVisible: true })
        : chart.addLineSeries({ color: l.color, lineWidth: (l.width ?? 2) as 1 | 2 | 3 | 4,
            lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, priceLineVisible: false, lastValueVisible: true }));
    chart.subscribeCrosshairMove((p) => {
      if (!p.time) return setHover(null);
      const out: Record<string, number> = {};
      series.forEach((s, i) => {
        const d: any = p.seriesData.get(s);
        if (d && typeof d.value === "number") out[lines[i].name] = d.value;
      });
      setHover(out);
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = [];
    };
  }, [shape, height, mode, theme, area]);
  useEffect(() => {
    if (!chartRef.current) return;
    seriesRef.current.forEach((s, i) => s.setData((lines[i]?.data ?? []).filter((p) => p.value !== null && p.value !== undefined) as any));
    chartRef.current.timeScale().fitContent();
  }, [dataSig, shape, height, mode, theme, area]);
  return (
    <div className="chart-box" style={{ height }}>
      <div className="chart-legend">
        {lines.map((l) => {
          const last = l.data.length ? l.data[l.data.length - 1].value : undefined;
          const v = hover?.[l.name] ?? last;
          return (
            <span key={l.name}>
              <span className="sw" style={{ background: l.color }} />
              <span className="ink2">{l.name}</span>{" "}
              <b className="num">{v !== undefined && v !== null ? fmtVal(v, mode) : "–"}</b>
            </span>
          );
        })}
      </div>
      <div ref={el} style={{ position: "absolute", inset: 0 }} />
    </div>
  );
}

export type Bar = { time: string; open: number; high: number; low: number; close: number };

/** Candles + moving averages + trade markers + horizontal price lines (stop, entry). */
export function CandleChart({ bars, overlays = [], markers = [], lines = [], height = 420 }: {
  bars: Bar[]; overlays?: LineSpec[]; markers?: SeriesMarker<Time>[];
  lines?: { price: number; title: string; color: string }[]; height?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useThemeVersion();
  const [hover, setHover] = useState<Bar | null>(null);
  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, baseOptions(height));
    const up = cssVar("--s3"), down = cssVar("--neg-mark");
    const c = chart.addCandlestickSeries({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down,
      borderVisible: false, priceLineVisible: false });
    c.setData(bars as any);
    overlays.forEach((o) => {
      const s = chart.addLineSeries({ color: o.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false });
      s.setData(o.data as any);
    });
    if (markers.length) c.setMarkers([...markers].sort((a, b) => String(a.time).localeCompare(String(b.time))));
    lines.forEach((l) => c.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LineStyle.Dashed,
      axisLabelVisible: true, title: l.title }));
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      const d: any = p.time ? p.seriesData.get(c) : null;
      setHover(d ? { time: String(p.time), ...d } : null);
    });
    return () => chart.remove();
  }, [JSON.stringify(bars.slice(-1)), bars.length, JSON.stringify(overlays.map((o) => o.data.length)),
      JSON.stringify(markers), JSON.stringify(lines), height, theme]);
  const b = hover ?? bars[bars.length - 1];
  return (
    <div className="chart-box" style={{ height }}>
      {b && (
        <div className="chart-legend num">
          <span className="ink2">{b.time}</span>
          <span>O <b>{b.open.toFixed(2)}</b></span>
          <span>H <b>{b.high.toFixed(2)}</b></span>
          <span>L <b>{b.low.toFixed(2)}</b></span>
          <span>C <b>{b.close.toFixed(2)}</b></span>
          {overlays.map((o) => (
            <span key={o.name}><span className="sw" style={{ background: o.color }} />{o.name}</span>
          ))}
        </div>
      )}
      <div ref={el} style={{ position: "absolute", inset: 0 }} />
    </div>
  );
}
