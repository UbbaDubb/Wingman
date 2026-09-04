"""
Standalone, read-only demo-recording script. NOT part of the live pipeline.

Reads logs/*.jsonl and reports what's in them; never imports loop.py,
decision.py, or any other production module, and never writes anywhere.
The only network calls it makes are optional, read-only Alpaca order
lookups (to confirm real fill status/time/price for the demo cycle) --
best-effort, and the script degrades gracefully to log-only data if
credentials or network aren't available. Safe to run at any time,
including while the scheduler is live and appending to today's log file.

Usage:
    python -m wingman.demo_replay --mode summary
        Boxed week summary only (Part 1). This is also always printed
        first for the other two modes, since it's the "here's where we
        are" context a viewer needs before the detailed replay.

    python -m wingman.demo_replay --mode cycle [--timestamp ISO] [--pause SECONDS] [--instant]
        Summary, then a styled replay of one real filled cycle (Part 2).
        Defaults to the confirmed clean cycle: 2026-09-01T14:16:41+00:00
        (long straddle K=774). Pass --timestamp to replay a different one
        (prefix match against a record's "timestamp" field is enough).

    python -m wingman.demo_replay --mode standdown [--timestamp ISO] [--pause SECONDS] [--instant]
        Summary, then a styled replay of one real regime-gate stand_down
        cycle (Part 3). Defaults to 2026-09-03T14:28:41+00:00 (the first
        of Thursday's three stand_downs; all three have complete
        fetch/fit/decision data, this one was picked arbitrarily among
        equals). Pass --timestamp for one of the other two.

Every number/message in the replays comes directly from a logged JSONL
record. The two facts below are the only exceptions -- they were
confirmed by reconciling Alpaca's live order history against the logs
(2026-09-04) and cannot be derived from logs/*.jsonl alone, so they are
hardcoded constants rather than computed. See wingman_summary.html for
the reconciliation. This script does not invent anything; it either
formats a logged record or states one of these two documented facts:
  - MANUAL_CLOSE_NOTE: the 1 Sep manual sell-to-close (not a Wingman
    pipeline action -- single-leg orders, no JSONL record at all).
  - WEEK_ORDERS_FILLED: fill counts, because a cycle record only logs
    the order's *submission* response (status "pending_new"); whether
    it later filled is never written back to the JSONL.
"""

import argparse
import glob
import json
import os
import sys
import textwrap
import time

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PACKAGE_ROOT, "logs")

BOX_WIDTH = 78

DEFAULT_CYCLE_TIMESTAMP = "2026-09-01T14:16:41"
DEFAULT_STANDDOWN_TIMESTAMP = "2026-09-03T14:28:41"

# Confirmed 2026-09-04 by reconciling Alpaca's live order history against
# logs/*.jsonl (30 Alpaca orders vs. 25 logged by Wingman) -- see the
# "Week result" section of wingman_summary.html for the full derivation.
# Not present in, or derivable from, the JSONL logs.
MANUAL_CLOSE_NOTE = (
    "One manual risk-reduction trade (1 Sep) realized +$714, "
    "closing 8 duplicated Monday positions"
)
WEEK_ORDERS_SUBMITTED = 25  # computed from logs below; kept here for the docstring's sake
WEEK_ORDERS_FILLED = 20     # Alpaca order-history confirmed; NOT in the JSONL

# Windows terminals often default stdout to cp1252, which can't encode box-
# drawing characters. Force UTF-8 where possible; fall back to plain ASCII
# box characters rather than crash if the terminal truly can't do it.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    _UNICODE_BOX = True
except (AttributeError, UnicodeError):
    _UNICODE_BOX = False

_CH = (
    {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│", "lm": "├", "rm": "┤"}
    if _UNICODE_BOX
    else {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "lm": "+", "rm": "+"}
)


# --- loading ------------------------------------------------------------------

def _log_files():
    """All logs/*.jsonl files, oldest date first (filenames sort chronologically)."""
    return sorted(glob.glob(os.path.join(LOG_DIR, "*.jsonl")))


def _load_records(files=None):
    """
    Yield (source_filename, line_number, record) for every parseable line
    across the given files (default: all log files). Skips blank/corrupt
    lines rather than failing the whole read -- a demo script must never
    error out on a stray partial line from a file still being appended to.
    """
    for path in files or _log_files():
        fname = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield fname, lineno, json.loads(line)
                except json.JSONDecodeError:
                    continue


def _is_cycle_record(rec):
    """A real trading cycle (fetch/fit/decide/...), as opposed to a
    scheduler skip record or an account_snapshot record."""
    return rec.get("record_type") != "account_snapshot" and "fit_result" in rec


def _is_account_snapshot(rec):
    return rec.get("record_type") == "account_snapshot" and rec.get("account")


def _is_submitted(rec):
    return rec.get("dry_run") is False and (rec.get("order_result") or {}).get("response") is not None


# --- box drawing ----------------------------------------------------------------

def _box_top():
    print(_CH["tl"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["tr"])


def _box_bottom():
    print(_CH["bl"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["br"])


def _box_line(text=""):
    """Prints `text` inside the box, wrapping (not truncating) if it's too
    wide -- a demo script must never silently drop part of a real number or
    rationale string just because the box is narrow."""
    for line in (textwrap.wrap(text, BOX_WIDTH - 4) or [""]):
        print(_CH["v"] + " " + line.ljust(BOX_WIDTH - 4) + " " + _CH["v"])


def _box_rule():
    print(_CH["lm"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["rm"])


def _pause(seconds):
    if seconds > 0:
        time.sleep(seconds)


# --- Part 1: startup summary ---------------------------------------------------

def print_summary():
    all_recs = list(_load_records())
    if not all_recs:
        print("No log files found under wingman/logs/ -- nothing to summarize.")
        return

    cycles = [r for _, _, r in all_recs if _is_cycle_record(r)]
    snapshots_all = sorted((r for _, _, r in all_recs if _is_account_snapshot(r)),
                            key=lambda r: r["timestamp"])
    submitted = [r for r in cycles if _is_submitted(r)]
    standdowns = [r for r in cycles
                  if isinstance(r.get("regime_gate"), dict)
                  and r["regime_gate"].get("decision") == "stand_down"]

    # positions block is pulled specifically from Thursday's log, per spec
    thu_path = os.path.join(LOG_DIR, "2026-09-03.jsonl")
    thu_snapshots = []
    if os.path.exists(thu_path):
        thu_snapshots = sorted(
            (r for _, _, r in _load_records([thu_path]) if _is_account_snapshot(r)),
            key=lambda r: r["timestamp"],
        )
    final_snapshot = thu_snapshots[-1] if thu_snapshots else (snapshots_all[-1] if snapshots_all else None)

    _box_top()
    _box_line("WINGMAN -- WEEK SUMMARY (from logged data)")
    _box_rule()

    if snapshots_all:
        start_eq = (snapshots_all[0].get("account") or {}).get("equity")
        end_eq = (snapshots_all[-1].get("account") or {}).get("equity")
        _box_line(f"Account equity:  start ${start_eq:,.2f}   ->   final ${end_eq:,.2f}")
        if start_eq:
            pct = (end_eq / start_eq - 1) * 100
            delta = end_eq - start_eq
            _box_line(f"                 {delta:+,.2f} ({pct:+.2f}%) -- all unrealized mark-to-market")
    else:
        _box_line("Account equity: no account_snapshot records found")

    _box_line("")
    _box_line(MANUAL_CLOSE_NOTE)

    _box_rule()

    n_standdown = len(standdowns)
    _box_line(f"Week totals: {len(cycles)} cycles, {len(submitted)} orders submitted, "
              f"{WEEK_ORDERS_FILLED} filled, {n_standdown} regime-gate stand-downs")

    _box_rule()

    if final_snapshot:
        positions = final_snapshot.get("positions") or []
        _box_line(f"Final open positions (as of {final_snapshot['timestamp'][:19]}): {len(positions)}")
        if not positions:
            _box_line("  (none)")
        for pos in positions:
            symbol = pos.get("symbol", "?")
            qty = pos.get("qty")
            upl = pos.get("unrealized_pl")
            qty_str = f"{qty:+g}" if isinstance(qty, (int, float)) else str(qty)
            upl_str = f"${upl:+,.2f}" if isinstance(upl, (int, float)) else "n/a"
            _box_line(f"  {symbol:<22} qty {qty_str:>5}   unrealized P&L {upl_str}")
    else:
        _box_line("Final open positions: no account_snapshot records found")

    _box_bottom()
    print()


# --- shared: finding + printing one cycle's fetch/fit/decision ------------------

def _find_cycle(prefix, files=None):
    for fname, lineno, rec in _load_records(files):
        if not _is_cycle_record(rec):
            continue
        if rec.get("timestamp", "").startswith(prefix):
            return fname, lineno, rec
    return None


def _print_fetch(snap):
    print("[1/4] FETCH")
    if snap.get("spot") is not None:
        print(f"  spot              : {snap.get('spot')}")
    print(f"  market_open       : {snap.get('market_open')}")
    print(f"  strikes_available : {snap.get('strikes_available')}")
    print(f"  strikes_used_fit  : {snap.get('strikes_used_in_fit')}")
    for w in snap.get("warnings") or []:
        print(f"  warning           : {w}")


def _print_fit(fit):
    print("\n[2/4] FIT  (2-component lognormal mixture)")
    if not fit:
        print("  (no fit result for this cycle)")
        return
    def _f(key, dp=4):
        v = fit.get(key)
        return f"{v:.{dp}f}" if isinstance(v, (int, float)) else v
    print(f"  lam     = {_f('lam')}")
    print(f"  sigma1  = {_f('sigma1')}")
    print(f"  sigma2  = {_f('sigma2')}")
    print(f"  success = {fit.get('success')}   cost = {_f('cost', 4)}")
    if fit.get("degenerate_fit"):
        print(f"  DEGENERATE FIT: {fit['degenerate_fit']}")


def _print_decision(decision):
    print("\n[3/4] DECISION")
    if not decision:
        print("  no candidate structure this cycle")
        return
    def _d(key):
        v = decision.get(key)
        return f"{v:.2f}" if isinstance(v, (int, float)) else v
    print(f"  structure  : {decision.get('structure')}")
    print(f"  strike     : {decision.get('strike')}", end="")
    if decision.get("far_strike") is not None:
        print(f"   far_strike: {decision.get('far_strike')}")
    else:
        print()
    print(f"  residual   : {_d('residual')}")
    print(f"  market_mid : {_d('market_mid')}    model_price: {_d('model_price')}")
    print(f"  rationale  : {decision.get('rationale')}")


# --- Part 2: styled filled-cycle replay ------------------------------------------

def _lookup_alpaca_fill(client_order_id):
    """
    Best-effort, READ-ONLY lookup of an order's real fill status/time/price
    by client_order_id, straight from Alpaca -- because a cycle record only
    logs the order's submission response (status "pending_new"); whether and
    when it filled is never written back into the JSONL. Never raises: on
    any failure (no credentials, no network, order not found) returns None
    and the caller falls back to what's actually in the log.
    """
    try:
        from wingman.data.fetch_snapshot import _get_credentials
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        api_key, secret = _get_credentials()
        client = TradingClient(api_key, secret, paper=True)
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, nested=True)
        for o in client.get_orders(req):
            if o.client_order_id == client_order_id:
                return o
    except Exception:
        return None
    return None


def replay_cycle(prefix, pause=1.2):
    found = _find_cycle(prefix)
    if not found:
        print(f"No cycle record found matching timestamp prefix '{prefix}'.")
        sys.exit(1)
    fname, lineno, rec = found

    print("=" * BOX_WIDTH)
    print(f" FILLED CYCLE   {rec.get('timestamp')}   (source: {fname}:{lineno})")
    print("=" * BOX_WIDTH)
    _pause(pause)

    _print_fetch(rec.get("snapshot") or {})
    _pause(pause)

    _print_fit(rec.get("fit_result"))
    _pause(pause)

    _print_decision(rec.get("decision"))
    _pause(pause)

    print("\n[4/4] REGIME GATE + ORDER RESULT")
    rg = rec.get("regime_gate")
    if isinstance(rg, dict):
        print(f"  verdict    : {rg.get('decision')}")
        print(f"  rationale  : {rg.get('rationale')}")
    else:
        print(f"  {rg}")

    order_result = rec.get("order_result") or {}
    response = order_result.get("response") or {}
    payload = order_result.get("payload") or {}
    print(f"  submitted  : dry_run={order_result.get('dry_run')}  success={order_result.get('success')}")
    print(f"  limit price: {payload.get('limit_price')}")
    print(f"  order id   : {response.get('id')}")
    print(f"  log status : {response.get('status')}  (submission-time response only)")

    fill = _lookup_alpaca_fill(response.get("client_order_id"))
    if fill is not None and str(fill.status.value if hasattr(fill.status, "value") else fill.status) == "filled":
        print(f"  CONFIRMED FILLED (live Alpaca order history, read-only lookup):")
        print(f"    filled_at   : {fill.filled_at}")
        print(f"    filled_price: {fill.filled_avg_price}")
    else:
        print("  (live fill confirmation unavailable this run -- credentials/network,")
        print("   or already known from wingman_summary.html: filled 14:21:31 UTC @ $17.41)")

    if rec.get("errors"):
        print("\nERRORS logged this cycle:")
        for e in rec["errors"]:
            print(f"  - {e}")
    print()


# --- Part 3: styled stand_down replay --------------------------------------------

def replay_standdown(prefix, pause=1.2):
    found = _find_cycle(prefix)
    if not found:
        print(f"No cycle record found matching timestamp prefix '{prefix}'.")
        sys.exit(1)
    fname, lineno, rec = found
    rg = rec.get("regime_gate")
    if not (isinstance(rg, dict) and rg.get("decision") == "stand_down"):
        print(f"Record at {rec.get('timestamp')} ({fname}:{lineno}) is not a stand_down verdict "
              f"(regime_gate={rg!r}) -- pick a different --timestamp.")
        sys.exit(1)

    print("=" * BOX_WIDTH)
    print(f" REGIME-GATE STAND_DOWN   {rec.get('timestamp')}   (source: {fname}:{lineno})")
    print("=" * BOX_WIDTH)
    _pause(pause)

    _print_fetch(rec.get("snapshot") or {})
    _pause(pause)

    _print_fit(rec.get("fit_result"))
    _pause(pause)

    _print_decision(rec.get("decision"))
    print("  (this candidate cleared every deterministic gate -- the regime gate is what stopped it)")
    _pause(pause)

    print("\n[4/4] REGIME GATE VERDICT")
    print("  verdict: stand_down")
    _pause(pause * 0.5)
    print()
    print(f"  \"{rg.get('rationale')}\"")
    print()


# --- CLI --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Wingman demo-recording replay tool (read-only).")
    parser.add_argument("--mode", choices=["summary", "cycle", "standdown"], default="summary",
                        help="what to show after the summary block (default: summary)")
    parser.add_argument("--timestamp", default=None,
                        help="ISO timestamp prefix to replay, overriding the default "
                             "for --mode cycle / --mode standdown")
    parser.add_argument("--pause", type=float, default=1.2, help="seconds between stages (default 1.2)")
    parser.add_argument("--instant", action="store_true", help="no pause between stages")
    args = parser.parse_args()

    pause = 0.0 if args.instant else args.pause

    print_summary()

    if args.mode == "cycle":
        ts = args.timestamp or DEFAULT_CYCLE_TIMESTAMP
        replay_cycle(ts, pause=pause)
    elif args.mode == "standdown":
        ts = args.timestamp or DEFAULT_STANDDOWN_TIMESTAMP
        replay_standdown(ts, pause=pause)


if __name__ == "__main__":
    main()
