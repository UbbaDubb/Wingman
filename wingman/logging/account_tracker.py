"""
Account/position snapshotting for P&L reconstruction.

One record per cycle, tagged "record_type": "account_snapshot", appended to
the same daily JSONL as the decision records (report.py filters on the tag).
Across the week this gives a 15-minute-resolution time series of account
equity and open-position mark-to-market.

NOTE ON FRAMING: nothing in the pipeline currently CLOSES a position —
decision.py only opens new structures, and every contract traded this week
expires 2026-09-18, after the hackathon ends. Any "P&L" reconstructed from
these snapshots is therefore UNREALIZED mark-to-market (equity moves +
unrealized_pl on open positions), not realized closed-trade profit. The
Wednesday report must present it as such.
"""

from datetime import datetime, timezone

from wingman.config import DRY_RUN
from wingman.data.fetch_snapshot import _get_credentials
from wingman.logging.logger import log_cycle


def snapshot_account() -> dict:
    """
    Pull account state (equity, last_equity, portfolio_value) and every open
    position (symbol, qty, cost_basis, market_value, unrealized_pl,
    unrealized_plpc) from the Alpaca paper account, log it as one
    "account_snapshot" JSONL record, and return the record.

    Never raises: an API failure is captured in the record's "error" field
    and the (partial) record is still logged, so the scheduler stays alive
    and the gap is visible in the data.
    """
    record: dict = {
        "record_type": "account_snapshot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": DRY_RUN,
        "account": None,
        "positions": [],
        "error": None,
    }

    try:
        from alpaca.trading.client import TradingClient

        api_key, secret = _get_credentials()
        client = TradingClient(api_key, secret, paper=True)

        acct = client.get_account()
        record["account"] = {
            "equity": float(acct.equity),
            "last_equity": float(acct.last_equity),
            "portfolio_value": float(acct.portfolio_value),
        }

        for pos in client.get_all_positions():
            record["positions"].append({
                "symbol": pos.symbol,
                "qty": float(pos.qty),
                "cost_basis": float(pos.cost_basis),
                "market_value": float(pos.market_value) if pos.market_value is not None else None,
                "unrealized_pl": float(pos.unrealized_pl) if pos.unrealized_pl is not None else None,
                "unrealized_plpc": float(pos.unrealized_plpc) if pos.unrealized_plpc is not None else None,
            })
    except Exception as exc:  # noqa: BLE001 — must never break the scheduler
        record["error"] = f"{type(exc).__name__}: {exc}"

    try:
        log_cycle(record)
    except Exception as exc:  # noqa: BLE001 — last resort: keep the loop alive
        record["error"] = (record["error"] or "") + f" | log failed: {exc}"

    return record
