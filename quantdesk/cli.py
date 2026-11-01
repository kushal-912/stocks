"""QuantDesk command line. Run ``qd --help``."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from quantdesk.config import ROOT, get_settings, setup_logging

app = typer.Typer(add_completion=False, help="QuantDesk - autonomous quant trading agent (paper by default).")
paper_app = typer.Typer(help="Paper account administration.")
broker_app = typer.Typer(help="Broker connections (Groww / Zerodha).")
app.add_typer(paper_app, name="paper")
app.add_typer(broker_app, name="broker")
con = Console()


def _pct(v) -> str:
    return "-" if v is None or v != v else f"{v * 100:+.2f}%"


def _num(v, nd=2) -> str:
    return "-" if v is None or v != v else f"{v:,.{nd}f}"


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")):
    setup_logging(logging.DEBUG if verbose else logging.INFO)


# ------------------------------------------------------------------ run
def _port_busy(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def _is_quantdesk(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
            return "autopilot" in json.loads(r.read().decode())
    except Exception:
        return False


def _free_port(port: int) -> int:
    for p in range(port, port + 30):
        if not _port_busy(p):
            return p
    raise RuntimeError("no free port found")


@app.command()
def start(port: int = 8000, open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Run everything: the autopilot (trading) and the web app, restarting either if it crashes."""
    from quantdesk.trader.autopilot import autopilot_running

    cmds: dict[str, list[str]] = {}
    if autopilot_running():
        con.print("[yellow]An autopilot is already running - not starting a second one (it would double every order).[/]")
    else:
        cmds["autopilot"] = [sys.executable, "-m", "quantdesk", "autopilot"]
    if _port_busy(port) and _is_quantdesk(port):
        con.print(f"Web app already running at http://localhost:{port}")
    else:
        if _port_busy(port):
            new = _free_port(port + 1)
            con.print(f"[yellow]Port {port} is used by another program; using {new} instead.[/]")
            port = new
        cmds["server"] = [sys.executable, "-m", "quantdesk", "serve", "--port", str(port)]
    if not cmds:
        con.print("Everything is already running.")
        return
    con.print(f"[bold]QuantDesk[/] running - web app on http://localhost:{port}  (Ctrl+C to stop)")
    procs: dict[str, subprocess.Popen] = {}
    started: dict[str, float] = {}
    fast_fails: dict[str, int] = {}
    opened = False
    try:
        while True:
            for name, cmd in cmds.items():
                p = procs.get(name)
                if p is not None and p.poll() is None:
                    continue
                if p is not None:
                    quick = time.time() - started[name] < 15
                    fast_fails[name] = fast_fails.get(name, 0) + 1 if quick else 0
                    if fast_fails[name] >= 3:
                        con.print(f"[red]{name} keeps exiting immediately (code {p.returncode}); retrying in 60s.[/]")
                        if time.time() - started[name] < 60:
                            continue
                        fast_fails[name] = 0
                    else:
                        con.print(f"[yellow]{name} exited with code {p.returncode}; restarting[/]")
                procs[name] = subprocess.Popen(cmd, cwd=str(ROOT))
                started[name] = time.time()
            if open_browser and not opened and "server" in cmds:
                time.sleep(3)
                import webbrowser

                webbrowser.open(f"http://localhost:{port}")
                opened = True
            time.sleep(5)
    except KeyboardInterrupt:
        for p in procs.values():
            p.terminate()


@app.command()
def autopilot():
    """Run the trading agent's daily schedule (premarket, open, ticks, EOD) forever."""
    from quantdesk.trader.autopilot import Autopilot, acquire_lock

    if not acquire_lock():
        con.print("[red]Another autopilot is already running. Refusing to start a second one.[/]")
        raise typer.Exit(2)
    Autopilot().run()


@app.command()
def serve(port: int = 8000, host: str = "127.0.0.1"):
    """Serve the web app + API."""
    import uvicorn

    if _port_busy(port):
        msg = "QuantDesk is already serving" if _is_quantdesk(port) else "another program is using"
        con.print(f"[red]Port {port}: {msg} it. Open http://localhost:{port} or pass --port {_free_port(port + 1)}.[/]")
        raise typer.Exit(2)
    uvicorn.run("quantdesk.server.app:app", host=host, port=port, log_level="warning")


@app.command()
def premarket():
    """Run today's premarket research + plan now (does not place orders)."""
    from quantdesk.trader.agent import TradingAgent

    s = TradingAgent().premarket(progress=lambda f, m: con.print(f"[dim]{f * 100:5.1f}%[/] {m}"))
    f = s["funnel"]
    con.print(f"Funnel: {f['universe']} universe -> {f['liquid']} liquid -> {f['uptrend']} uptrend -> {f['eligible']} eligible "
              f"-> {f['researched']} researched -> {f['candidates']} candidates")
    con.print(f"Regime: {s['regime']['state']} (NIFTY {s['regime']['pct_vs_sma200']:+.1f}% vs 200-DMA, "
              f"breadth {s['regime']['breadth'] * 100:.0f}%) | Claude review: {s['llm_review']}")
    t = Table("action", "symbol", "qty", "price", "stop", "score", "reason")
    for a in s["plan"]:
        t.add_row(a["action"], a["symbol"], str(a["qty"]), _num(a["ref_price"]), _num(a["stop"]), _num(a["score"]),
                  a["reason"][:90])
    con.print(t)


@app.command()
def status():
    """Account snapshot, today's phases and the evaluation so far."""
    from quantdesk.server.app import account
    from quantdesk.store import db
    from quantdesk.trader.evaluation import evaluate

    acct = account()
    snap = db.kv_get(f"snapshot:{acct}") or {}
    hb = db.kv_get(f"heartbeat:{acct}")
    con.print(f"Account [bold]{acct}[/] | equity {_num(snap.get('equity'), 0)} | cash {_num(snap.get('cash'), 0)} | "
              f"since inception {_num(snap.get('return_pct'))}% | autopilot heartbeat {hb['ts'] if hb else 'never'}")
    for p in snap.get("positions", []):
        con.print(f"  {p['symbol']:<12} {p['qty']:>5} @ {p['entry_price']:>9,.2f}  ltp {p['ltp']:>9,.2f}  "
                  f"P&L {p['pnl']:>+9,.0f}  stop {p['stop']:,.2f}")
    ev = evaluate(acct)
    for v in ev.get("verdict", []):
        con.print(f"  [{v['status']}] {v['check']}: {v['detail']}")


@app.command("backtest-agent")
def backtest_agent(start: str = "2024-01-01", end: str = typer.Option(None), capital: float = 50_000,
                   quiet: bool = typer.Option(False, help="Only print the summary")):
    """Replay the agent's core policy over history, printing every trade as it happens."""
    from quantdesk.server.app import _bt_path, _bt_state, run_backtest_job
    from quantdesk.trader.backtest import min_viable_capital

    if capital < min_viable_capital():
        con.print(f"[red]Capital too small: the agent needs at least ₹{min_viable_capital():,.0f}.[/]")
        raise typer.Exit(1)

    def echo(e):
        if quiet:
            return
        if e["type"] == "buy":
            con.print(f"{e['day']}  [blue]BUY [/] {e['qty']:>4} {e['symbol']:<12} @ ₹{e['price']:>9,.2f}  stop ₹{e['stop']:,.2f}")
        elif e["type"] == "sell":
            col = "green" if e["pnl"] >= 0 else "red"
            nifty = f"  NIFTY {e['nifty_ret'] * 100:+.1f}%" if e.get("nifty_ret") is not None else ""
            con.print(f"{e['day']}  [{col}]SELL[/] {e['qty']:>4} {e['symbol']:<12} @ ₹{e['exit_price']:>9,.2f}  bought ₹{e['entry_price']:,.2f}"
                      f"  [{col}]{e['pnl']:+,.0f} ({e['ret'] * 100:+.1f}%)[/]{nifty}  {e['reason']}")
        elif e["type"] == "note":
            con.print(f"{e['day']}  [dim]{e['message']}[/]")

    run_backtest_job(start, capital, end, echo=echo)
    if _bt_state.get("status") != "done":
        con.print(f"[red]Backtest failed:[/] {_bt_state.get('message')}")
        raise typer.Exit(1)
    m = json.loads(_bt_path().read_text(encoding="utf-8"))["metrics"]
    con.rule("Result")
    con.print(f"Agent: final {_num(m['final_value'], 0)} ({_pct(m['total_return'])}), CAGR {_pct(m['cagr'])}, "
              f"Sharpe {_num(m['sharpe'])}, maxDD {_pct(m['max_drawdown'])}, trades {m['n_trades']}, "
              f"win rate {_pct(m.get('win_rate'))}, charges {_num(m['total_costs'], 0)}")
    con.print(f"NIFTY: {_pct(m.get('benchmark_return'))}, maxDD {_pct(m.get('benchmark_max_drawdown'))}")


@app.command()
def analyze(symbol: str, news: bool = typer.Option(True), llm: bool = typer.Option(True)):
    """Full multi-model analysis of one stock."""
    from quantdesk.engine.analyzer import analyze_symbol
    from quantdesk.nlp.sentiment import SentimentEngine

    r = analyze_symbol(symbol.upper(), sentiment_engine=SentimentEngine(use_llm=llm) if news else None)
    con.rule(f"[bold]{r.symbol}[/] {r.last_close:,.2f} ({r.last_date}) score {r.score:+.3f} -> {r.action}")
    for k, v in r.components.items():
        con.print(f"  {k:<15} {_num(v, 3)}")
    if r.ml:
        con.print(f"  ML P(up) {r.ml['prob_up']:.2f}, OOS AUC {_num(r.ml['oos_auc'], 3)}")
    for n in r.notes:
        con.print(f"[yellow]note:[/] {n}")


@app.command()
def news(symbol: str, llm: bool = True):
    """Recent headlines with sentiment scores."""
    from quantdesk.nlp.sentiment import SentimentEngine

    sig = SentimentEngine(use_llm=llm).for_symbol(symbol.upper())
    con.print(f"{sig.symbol}: score {sig.score:+.2f}, confidence {sig.confidence:.2f}, method {sig.method}")
    for h in sig.headlines:
        con.print(f"  {h.sentiment:+.2f}  {h.headline.title[:110]}")


@app.command()
def verify():
    """Verify the hash chain of the event log (tamper evidence for the evaluation record)."""
    from quantdesk.store import db

    v = db.verify_chain()
    con.print(("[green]Ledger intact[/]" if v["ok"] else f"[red]Ledger tampered at event {v['broken_at_id']}[/]")
              + f" - {v['checked']} events checked")


# ------------------------------------------------------------------ paper account
@paper_app.command("reset")
def paper_reset(capital: float = typer.Option(None, help="Starting cash (default agent.initial_capital)"),
                yes: bool = typer.Option(False, "--yes")):
    """Start a fresh evaluation: wipes the paper account and the agent's paper history."""
    from quantdesk.broker.paper import PaperBroker
    from quantdesk.store import db

    if not yes and not typer.confirm("This erases the paper account and its track record. Continue?"):
        raise typer.Abort()
    PaperBroker().reset(capital)
    with db.tx() as c:
        for t in ("positions", "trades", "nav", "nav_intraday", "agent_orders", "decisions", "candidates", "scans", "runs"):
            c.execute(f"DELETE FROM {t} WHERE account='paper'")
        c.execute("DELETE FROM kv WHERE key IN ('inception:paper', 'snapshot:paper')")
    db.log_event("paper", "paper_reset", f"Paper account reset with ₹{capital or get_settings().agent.initial_capital:,.0f}",
                 level="warning")
    con.print("Paper account reset. The agent records a new inception on its next run.")


# ------------------------------------------------------------------ brokers
@broker_app.command("login")
def broker_login(name: str = typer.Argument(..., help="groww | zerodha")):
    """Daily broker login (SEBI requires a fresh session every trading day)."""
    if name == "zerodha":
        from quantdesk.broker.zerodha import ZerodhaBroker

        b = ZerodhaBroker()
        con.print(f"Open and log in: {b.login_url()}")
        b.complete_login(typer.prompt("Paste the redirect URL (or request_token)"))
    elif name == "groww":
        from quantdesk.broker.groww import GrowwBroker

        GrowwBroker().login()
    else:
        raise typer.BadParameter("name must be groww or zerodha")
    con.print(f"[green]{name} session saved for {date.today()}[/]")


@broker_app.command("test")
def broker_test(name: str = typer.Argument(None, help="paper | groww | zerodha")):
    """Read-only connectivity check: funds, holdings, a quote. Places no orders."""
    from quantdesk.broker import get_broker

    b = get_broker(name)
    f = b.get_funds()
    con.print(f"[green]Connected to {b.name}[/]. Available cash: {f.available_cash:,.2f}")
    for p in b.portfolio()[:20]:
        con.print(f"  {p.symbol}: {p.quantity} @ {p.average_price:,.2f}")
    con.print(f"LTP RELIANCE: {b.get_ltp(['RELIANCE']).get('RELIANCE')}")


@app.command()
def kill(action: str = typer.Argument("status", help="on | off | status"), reason: str = "manual"):
    """Kill switch: close-only mode (no new buys; exits and stops still work)."""
    from quantdesk.risk.compliance import is_killed, kill_reason, set_kill

    if action == "on":
        set_kill(True, reason)
    elif action == "off":
        set_kill(False)
    con.print(f"Kill switch: {'[red]ON[/] (' + kill_reason() + ')' if is_killed() else '[green]off[/]'}")


if __name__ == "__main__":
    app()
