"""
Main cycle: fetch -> fit -> decide -> (regime gate: not built yet) ->
build order -> submit -> log.

Run from the REPO ROOT (see wingman/__init__.py for why):
    python -m wingman.loop                     # fetch live snapshot, one cycle
    python -m wingman.loop path/to/snap.json   # replay a saved snapshot
    python -m wingman.loop --schedule          # run every CYCLE_INTERVAL_MINUTES
                                               # during market hours, until Ctrl+C

Safety: DRY_RUN defaults to True (config.py); set WINGMAN_DRY_RUN=0 to submit
for real — the scheduler NEVER flips it itself.
"""

import json
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np

from wingman.config import (
    EXPIRY,
    DRY_RUN,
    RISK_FREE_RATE,
    DIVIDEND_ESTIMATE,
    MIN_FIT_STRIKES,
    CYCLE_INTERVAL_MINUTES,
)
from wingman.data.fetch_snapshot import fetch_spy_chain_snapshot
from wingman.engine.decision import propose_trade
from wingman.execution.cli_client import submit_order
from wingman.execution.order_builder import build_straddle, build_vertical
from wingman.logging.account_tracker import snapshot_account
from wingman.logging.logger import log_cycle
from wingman.models.fit_utils import call_equivalent_quote, implied_vol_call
from wingman.models.mixture_dynamics import fit_mixture


def _forward_and_tte(snapshot: dict) -> tuple[float, float]:
    """
    Forward and time-to-expiry as of the snapshot timestamp (not "now", so a
    replayed snapshot reproduces the original numbers).

    F = S * e^{rT} - DIV: the SPY quarterly dividend goes ex ON the expiry
    date (2026-09-18), i.e. just inside the option's life, so it must be
    subtracted from the carried spot. ACT/365 daycount.
    """
    asof = datetime.fromisoformat(snapshot["timestamp"])
    expiry_dt = datetime.fromisoformat(snapshot["expiry"] + "T21:00:00+00:00")  # ~4pm ET close
    tte = max((expiry_dt - asof).total_seconds() / (365.0 * 86400.0), 1e-6)
    forward = snapshot["spot"] * float(np.exp(RISK_FREE_RATE * tte)) - DIVIDEND_ESTIMATE
    return forward, tte


def _fit_inputs(snapshot: dict, forward: float, tte: float) -> tuple[list, list]:
    """
    Build (strikes, market_ivs) for fit_mixture from the snapshot, always off
    the liquid OTM side (calls above spot, parity-converted puts below — see
    fit_utils.call_equivalent_quote). Strikes whose mid can't be inverted to a
    valid IV (stale/arbitrage-violating quotes) are silently dropped rather
    than poisoning the fit.
    """
    strikes, ivs = [], []
    for row in snapshot.get("strikes", []):
        quote = call_equivalent_quote(row, snapshot["spot"], forward, tte, RISK_FREE_RATE)
        if quote is None:
            continue
        iv = implied_vol_call(quote["mid"], forward, row["strike"], tte, RISK_FREE_RATE)
        if iv is None:
            continue
        strikes.append(row["strike"])
        ivs.append(iv)
    return strikes, ivs


def run_cycle(snapshot_path: str | None = None) -> dict:
    """
    Execute one full cycle and return the log record. Every stage is wrapped
    so a failure logs an error and aborts the REST of the cycle — never the
    process; this has to survive unattended for days.
    """
    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": DRY_RUN,
        "snapshot": None,
        "fit_result": None,
        "regime_gate": "not yet implemented, trading unrestricted",
        "decision": None,
        "order_payload": None,
        "order_result": None,
        "errors": [],
    }

    def fail(stage: str, exc: Exception) -> dict:
        record["errors"].append(f"{stage}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        log_cycle(record)
        return record

    # --- 1. snapshot -----------------------------------------------------------
    try:
        if snapshot_path:
            with open(snapshot_path, encoding="utf-8") as f:
                snapshot = json.load(f)
            record["snapshot_source"] = snapshot_path
        else:
            snapshot = fetch_spy_chain_snapshot(EXPIRY)
            record["snapshot_source"] = "live"
        # Log a summary, not the whole chain — the full snapshot is already
        # persisted as its own JSON file by fetch_spy_chain_snapshot.
        record["snapshot"] = {
            "spot": snapshot["spot"],
            "timestamp": snapshot["timestamp"],
            "market_open": snapshot.get("market_open"),
            "strikes_available": len(snapshot.get("strikes", [])),
            "warnings": snapshot.get("warnings", []),
        }
    except Exception as exc:  # noqa: BLE001
        return fail("fetch", exc)

    # --- 2. fit ------------------------------------------------------------------
    try:
        forward, tte = _forward_and_tte(snapshot)
        strikes, ivs = _fit_inputs(snapshot, forward, tte)
        record["snapshot"]["strikes_used_in_fit"] = len(strikes)
        if len(strikes) < MIN_FIT_STRIKES:
            record["errors"].append(
                f"fit: only {len(strikes)} usable strikes (< {MIN_FIT_STRIKES}), skipping cycle"
            )
            log_cycle(record)
            return record

        fit_result = fit_mixture(strikes, ivs, forward, tte, r=RISK_FREE_RATE)
        # Enrich with the pricing context the decision stage needs (the frozen
        # fit_mixture signature doesn't return these itself).
        fit_result.update({"forward": forward, "tte": tte, "r": RISK_FREE_RATE})
        record["fit_result"] = fit_result

        if not fit_result["success"]:
            record["errors"].append("fit: least_squares did not converge, skipping decision")
            log_cycle(record)
            return record
    except Exception as exc:  # noqa: BLE001
        return fail("fit", exc)

    # --- 3. decide ----------------------------------------------------------------
    try:
        proposal = propose_trade(snapshot, fit_result)
        record["decision"] = proposal
        if proposal:
            print(f"[decision] {proposal['rationale']}")
        elif fit_result.get("degenerate_fit"):
            # propose_trade already printed the specific reason; keep the
            # summary line accurate (the gates were never evaluated).
            print("[decision] no trade — fit rejected by degenerate-fit guard")
        else:
            print("[decision] no residual cleared the materiality gates — no trade")
    except Exception as exc:  # noqa: BLE001
        return fail("decide", exc)

    # --- 4. regime gate: NOT BUILT YET ------------------------------------------
    print("[regime_gate] not yet implemented, trading unrestricted")

    # --- 5. build + submit ---------------------------------------------------------
    if proposal:
        try:
            if proposal["structure"] == "long_straddle":
                payload = build_straddle(proposal)
            else:
                payload = build_vertical(proposal)
            record["order_payload"] = payload
            record["order_result"] = submit_order(payload, dry_run=DRY_RUN)
        except Exception as exc:  # noqa: BLE001
            return fail("execute", exc)

    # --- 6. log ----------------------------------------------------------------------
    try:
        log_cycle(record)
    except Exception as exc:  # noqa: BLE001 — last resort: at least show it
        record["errors"].append(f"log: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    return record


def _market_is_open() -> bool | None:
    """
    Ask Alpaca's clock whether the market is open right now. Returns None if
    the clock itself can't be read (network/API hiccup) — the scheduler
    treats that as "don't run a cycle" but keeps the loop alive.
    """
    from alpaca.trading.client import TradingClient
    from wingman.data.fetch_snapshot import _get_credentials

    try:
        api_key, secret = _get_credentials()
        return bool(TradingClient(api_key, secret, paper=True).get_clock().is_open)
    except Exception as exc:  # noqa: BLE001 — clock failure must not kill the loop
        print(f"[scheduler] WARNING: market clock check failed: {exc}")
        return None


def run_scheduler() -> None:
    """
    Run run_cycle() every CYCLE_INTERVAL_MINUTES during market hours, forever,
    until Ctrl+C.

    Survivability contract:
      - market closed  -> log a skip record, sleep, try again (so a process
        started pre-open picks up automatically at the bell);
      - clock unreadable -> same as closed (fail safe, never fetch blind);
      - ANY exception inside a cycle -> logged, loop continues; a bad tick
        must never kill the process;
      - Ctrl+C -> finish whatever write is in flight (log_cycle writes are
        single atomic appends), log a shutdown record, exit 0.
    """
    interval_s = CYCLE_INTERVAL_MINUTES * 60
    print(f"[scheduler] started: every {CYCLE_INTERVAL_MINUTES}min during market "
          f"hours, DRY_RUN={DRY_RUN} (flip is manual, via WINGMAN_DRY_RUN)")
    try:
        while True:
            started = datetime.now(timezone.utc).isoformat()
            is_open = _market_is_open()
            if is_open:
                try:
                    run_cycle()  # logs its own record, catches its own stage errors
                except Exception as exc:  # noqa: BLE001 — belt and braces:
                    # run_cycle() already contains per-stage handling, but the
                    # loop must survive even a bug in that handling itself.
                    traceback.print_exc()
                    try:
                        log_cycle({
                            "timestamp": started,
                            "scheduler": "cycle raised outside stage handling",
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                    except Exception:  # noqa: BLE001 — nothing left to do
                        traceback.print_exc()
                # Account/position mark-to-market snapshot, once per cycle
                # whether or not a trade was proposed — builds the 15-minute
                # equity time series for the weekly report. snapshot_account()
                # never raises (errors land in its own record).
                snap = snapshot_account()
                if snap.get("error"):
                    print(f"[scheduler] WARNING: account snapshot failed: {snap['error']}")
            else:
                reason = "market closed" if is_open is False else "market clock unreadable"
                print(f"[scheduler] {started} {reason} — skipping cycle")
                log_cycle({
                    "timestamp": started,
                    "scheduler": f"skipped: {reason}",
                    "dry_run": DRY_RUN,
                })
            time.sleep(interval_s)
    except KeyboardInterrupt:
        stopped = datetime.now(timezone.utc).isoformat()
        print(f"\n[scheduler] Ctrl+C — shutting down cleanly at {stopped}")
        log_cycle({"timestamp": stopped, "scheduler": "clean shutdown (KeyboardInterrupt)"})


def main():
    # `--schedule` runs the recurring loop; otherwise one cycle (optionally
    # replaying a saved snapshot passed as the first argument).
    if "--schedule" in sys.argv[1:]:
        run_scheduler()
        return
    snapshot_path = sys.argv[1] if len(sys.argv) > 1 else None
    record = run_cycle(snapshot_path)
    print("\n=== cycle record ===")
    print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    main()
