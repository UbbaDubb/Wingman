"""
Standalone, read-only demo-recording script. NOT part of the live pipeline.

Reads logs/*.jsonl and reports what's in them; never imports loop.py,
decision.py, or any other production module, never calls Alpaca, never
writes anywhere. Safe to run at any time, including while the scheduler
is live and appending to today's log file.

Usage:
    python -m wingman.demo_replay summary
        Prints the boxed startup summary (Part 1).

    python -m wingman.demo_replay cycle <timestamp-prefix> [--date YYYY-MM-DD]
                                        [--pause SECONDS] [--instant]
        Replays one real cycle matching <timestamp-prefix> (Part 2).
        <timestamp-prefix> matches the start of a record's "timestamp"
        field, e.g. "2026-08-31T09:57" or the full ISO string.
        --date restricts the search to logs/<date>.jsonl (recommended
        once you know which file the cycle is in).

Every number printed comes directly from a logged record. This script
only formats; it computes nothing new except plain arithmetic already
implied by the numbers on screen (e.g. equity change = current - start).
"""

import argparse
import glob
import json
import os
import sys
import time

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PACKAGE_ROOT, "logs")

BOX_WIDTH = 66

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
    lines rather than failing the whole read — a demo script must never
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
    return rec.get("record_type") == "account_snapshot"


# --- classification (mirrors loop.py / decision.py's own logic) ---------------

def classify_cycle(rec):
    """
    Bucket a cycle record by outcome, using only the same signals loop.py
    and decision.py themselves write into the record (error-string
    prefixes, the "degenerate_fit" key, decision presence). Returns a
    short machine-readable tag.
    """
    errors = rec.get("errors") or []
    if any("usable strikes" in e for e in errors):
        return "insufficient_strikes"
    if any("did not converge" in e for e in errors):
        return "fit_no_converge"
    if any(e.startswith("fetch:") for e in errors):
        return "fetch_error"
    if any(e.startswith("fit:") for e in errors):
        return "fit_error_other"
    if any(e.startswith("decide:") for e in errors):
        return "decide_error"
    if any(e.startswith("execute:") for e in errors):
        return "execute_error"

    fit_result = rec.get("fit_result") or {}
    if fit_result.get("degenerate_fit"):
        return "degenerate_fit"
    if rec.get("decision") is not None:
        return "traded"
    if fit_result.get("success") is True:
        return "no_gate_cleared"
    return "other"


_LABELS = {
    "traded": "trade proposed",
    "no_gate_cleared": "no residual cleared the gates",
    "degenerate_fit": "degenerate-fit guard fired",
    "insufficient_strikes": "insufficient usable strikes",
    "fit_no_converge": "fit did not converge",
    "fetch_error": "fetch error",
    "fit_error_other": "fit error (other)",
    "decide_error": "decision-stage error",
    "execute_error": "execution-stage error",
    "other": "other / unclassified",
}


# --- box drawing ----------------------------------------------------------------

def _box_top():
    print(_CH["tl"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["tr"])


def _box_bottom():
    print(_CH["bl"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["br"])


def _box_line(text=""):
    text = text[: BOX_WIDTH - 4]
    print(_CH["v"] + " " + text.ljust(BOX_WIDTH - 4) + " " + _CH["v"])


def _box_rule():
    print(_CH["lm"] + _CH["h"] * (BOX_WIDTH - 2) + _CH["rm"])


# --- Part 1: startup summary ---------------------------------------------------

def print_summary():
    all_recs = list(_load_records())
    if not all_recs:
        print("No log files found under wingman/logs/ — nothing to summarize.")
        return

    cycles = [r for _, _, r in all_recs if _is_cycle_record(r)]
    snapshots = [r for _, _, r in all_recs if _is_account_snapshot(r)]

    _box_top()
    _box_line("WINGMAN - STATUS SUMMARY (from logged data)")
    _box_rule()

    # --- account equity -----------------------------------------------------
    if snapshots:
        snapshots_sorted = sorted(snapshots, key=lambda r: r["timestamp"])
        first = snapshots_sorted[0]
        last = snapshots_sorted[-1]
        first_eq = (first.get("account") or {}).get("equity")
        last_eq = (last.get("account") or {}).get("equity")
        _box_line(f"Account equity (as of {last['timestamp'][:19]}):")
        if last_eq is not None:
            _box_line(f"  ${last_eq:,.2f}")
            if first_eq is not None:
                delta = last_eq - first_eq
                sign = "+" if delta >= 0 else ""
                _box_line(f"  {sign}${delta:,.2f} since first snapshot ({first['timestamp'][:10]})")
                _box_line(f"    (start ${first_eq:,.2f})")
        else:
            _box_line("  equity unavailable (snapshot had no account data)")
    else:
        _box_line("Account equity: no account_snapshot records found")

    _box_rule()

    # --- track record ---------------------------------------------------------
    outcome_counts = {}
    for rec in cycles:
        tag = classify_cycle(rec)
        outcome_counts[tag] = outcome_counts.get(tag, 0) + 1
    n_traded = outcome_counts.get("traded", 0)
    n_cycles = len(cycles)
    _box_line(f"Cycles logged: {n_cycles}  |  trades proposed: {n_traded}")
    for tag, count in sorted(outcome_counts.items(), key=lambda kv: -kv[1]):
        if tag == "traded":
            continue
        _box_line(f"  {_LABELS.get(tag, tag)}: {count}")

    _box_rule()

    # --- open positions ---------------------------------------------------------
    if snapshots:
        last_positions = snapshots_sorted[-1].get("positions") or []
        _box_line(f"Open positions (as of last snapshot): {len(last_positions)}")
        if not last_positions:
            _box_line("  (none)")
        for pos in last_positions:
            symbol = pos.get("symbol", "?")
            qty = pos.get("qty")
            upl = pos.get("unrealized_pl")
            qty_str = f"{qty:g}" if isinstance(qty, (int, float)) else str(qty)
            upl_str = f"{upl:+,.2f}" if isinstance(upl, (int, float)) else "n/a"
            _box_line(f"  {symbol:<22} qty {qty_str:>6}   unrealized P&L {upl_str}")
    else:
        _box_line("Open positions: no account_snapshot records found")

    _box_bottom()


# --- Part 2: styled cycle replay -------------------------------------------------

def _find_cycle(prefix, date=None):
    files = [os.path.join(LOG_DIR, f"{date}.jsonl")] if date else None
    if files and not os.path.exists(files[0]):
        print(f"No such log file: {files[0]}")
        sys.exit(1)
    for fname, lineno, rec in _load_records(files):
        if not _is_cycle_record(rec):
            continue
        if rec.get("timestamp", "").startswith(prefix):
            return fname, lineno, rec
    return None


def _pause(seconds):
    if seconds > 0:
        time.sleep(seconds)


def replay_cycle(prefix, date=None, pause=1.2):
    found = _find_cycle(prefix, date)
    if not found:
        scope = f"logs/{date}.jsonl" if date else "any log file"
        print(f"No cycle record found matching timestamp prefix '{prefix}' in {scope}.")
        sys.exit(1)

    fname, lineno, rec = found
    print(f"(replaying {fname}:{lineno})\n")
    _pause(pause)

    # --- 1. fetch ---------------------------------------------------------------
    print("=" * BOX_WIDTH)
    print(f" CYCLE  {rec.get('timestamp')}   (dry_run={rec.get('dry_run')})")
    print("=" * BOX_WIDTH)
    _pause(pause)

    snap = rec.get("snapshot") or {}
    print("\n[1/5] FETCH")
    if rec.get("snapshot_source"):
        print(f"  source           : {rec['snapshot_source']}")
    print(f"  spot             : {snap.get('spot')}")
    print(f"  market_open      : {snap.get('market_open')}")
    print(f"  strikes_available: {snap.get('strikes_available')}")
    print(f"  strikes_used_fit : {snap.get('strikes_used_in_fit')}")
    for w in snap.get("warnings") or []:
        print(f"  warning          : {w}")
    _pause(pause)

    # --- 2. fit ------------------------------------------------------------------
    fit = rec.get("fit_result")
    print("\n[2/5] FIT (2-component lognormal mixture)")
    if fit:
        def _f(key, dp=4):
            v = fit.get(key)
            return f"{v:.{dp}f}" if isinstance(v, (int, float)) else v
        print(f"  lam    = {_f('lam')}")
        print(f"  sigma1 = {_f('sigma1')}")
        print(f"  sigma2 = {_f('sigma2')}")
        print(f"  success= {fit.get('success')}   cost = {_f('cost', 4)}")
        if fit.get("degenerate_fit"):
            print(f"  DEGENERATE FIT: {fit['degenerate_fit']}")
    else:
        print("  (no fit result for this cycle)")
    _pause(pause)

    # --- 3. decision ---------------------------------------------------------------
    decision = rec.get("decision")
    print("\n[3/5] DECISION")
    if decision:
        def _d(key):
            v = decision.get(key)
            return f"{v:.2f}" if isinstance(v, (int, float)) else v
        print(f"  structure  : {decision.get('structure')}")
        print(f"  strike     : {decision.get('strike')}")
        if decision.get("far_strike") is not None:
            print(f"  far_strike : {decision.get('far_strike')}")
        print(f"  residual   : {_d('residual')}")
        print(f"  market_mid : {_d('market_mid')}   model_price: {_d('model_price')}")
        print(f"  rationale  : {decision.get('rationale')}")
    else:
        tag = classify_cycle(rec)
        print(f"  no trade — {_LABELS.get(tag, tag)}")
    _pause(pause)

    # --- 4. regime gate --------------------------------------------------------------
    print("\n[4/5] REGIME GATE")
    print(f"  {rec.get('regime_gate')}")
    _pause(pause)

    # --- 5. order result ---------------------------------------------------------------
    order_result = rec.get("order_result")
    print("\n[5/5] ORDER RESULT")
    if order_result:
        print(f"  dry_run   : {order_result.get('dry_run')}")
        print(f"  success   : {order_result.get('success')}")
        payload = order_result.get("payload") or {}
        if payload:
            print(f"  limit_price: {payload.get('limit_price')}")
        response = order_result.get("response")
        if isinstance(response, dict):
            print(f"  order id  : {response.get('id')}")
            print(f"  status    : {response.get('status')}")
        if order_result.get("stderr"):
            print(f"  stderr    : {order_result['stderr']}")
    else:
        print("  (no order submitted this cycle)")

    if rec.get("errors"):
        print("\nERRORS logged this cycle:")
        for e in rec["errors"]:
            print(f"  - {e}")

    print()


# --- CLI --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="print the boxed startup summary")

    p_cycle = sub.add_parser("cycle", help="replay one real logged cycle")
    p_cycle.add_argument("timestamp", help="timestamp prefix to match, e.g. 2026-08-31T09:57")
    p_cycle.add_argument("--date", help="restrict search to logs/<date>.jsonl")
    p_cycle.add_argument("--pause", type=float, default=1.2, help="seconds between stages (default 1.2)")
    p_cycle.add_argument("--instant", action="store_true", help="no pause between stages")

    args = parser.parse_args()

    if args.command == "summary":
        print_summary()
    elif args.command == "cycle":
        pause = 0.0 if args.instant else args.pause
        replay_cycle(args.timestamp, date=args.date, pause=pause)


if __name__ == "__main__":
    main()
