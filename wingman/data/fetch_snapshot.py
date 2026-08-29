"""
Fetch a live SPY option-chain snapshot from Alpaca (alpaca-py SDK).

Why two API calls per snapshot:
  - open interest lives on the *trading* API's contracts endpoint
    (/v2/options/contracts), not on the market-data snapshot endpoint;
  - live bid/ask (and IV/greeks when available) live on the *market data*
    option-snapshot endpoint.
  So we pull contracts first (to learn OI and filter illiquid strikes cheaply)
  and only then request quotes for the survivors.

Credentials: read from ALPACA_API_KEY / ALPACA_SECRET_KEY env vars, falling
back to a wingman/.env file (parsed manually to avoid adding a python-dotenv
dependency). Paper endpoints are used throughout.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from wingman.config import (
    UNDERLYING,
    STRIKE_RANGE_PCT,
    MIN_OPEN_INTEREST,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = PACKAGE_ROOT / "snapshots"

# Alpaca caps snapshot requests well above this, but URLs have length limits
# when symbols are passed as a query string — 100 per request is safely under.
_QUOTE_BATCH_SIZE = 100


def _load_env_file() -> None:
    """Populate os.environ from wingman/.env (KEY=VALUE lines) without
    overriding anything already set in the real environment."""
    env_path = PACKAGE_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _get_credentials() -> tuple[str, str]:
    _load_env_file()
    api_key = os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
    if not api_key or not secret:
        raise RuntimeError(
            "Alpaca credentials not found. Set ALPACA_API_KEY / ALPACA_SECRET_KEY "
            "in the environment or in wingman/.env"
        )
    return api_key, secret


def _side_from_snapshot(symbol: str, open_interest: int, snap) -> dict:
    """Flatten one alpaca-py OptionsSnapshot into the plain-dict schema the
    rest of the pipeline consumes (JSON-serializable, no SDK objects)."""
    bid = ask = None
    quote_time = None
    if snap is not None and snap.latest_quote is not None:
        bid = float(snap.latest_quote.bid_price) if snap.latest_quote.bid_price else None
        ask = float(snap.latest_quote.ask_price) if snap.latest_quote.ask_price else None
        if snap.latest_quote.timestamp is not None:
            quote_time = snap.latest_quote.timestamp.isoformat()

    mid = None
    if bid and ask and ask >= bid:
        mid = round(0.5 * (bid + ask), 4)

    iv = None
    delta = None
    if snap is not None:
        if snap.implied_volatility is not None:
            iv = float(snap.implied_volatility)
        if snap.greeks is not None and snap.greeks.delta is not None:
            delta = float(snap.greeks.delta)

    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "open_interest": open_interest,
        "iv": iv,
        "delta": delta,
        "quote_time": quote_time,
    }


def fetch_spy_chain_snapshot(expiry: str) -> dict:
    """
    Pull the current SPY option chain for `expiry` with live quotes, filter it
    to a liquid strike band, persist it as timestamped JSON under snapshots/,
    and return the same structure as a dict.

    Returned schema:
        {
          "underlying": "SPY", "expiry": "...", "timestamp": iso-utc,
          "spot": float, "spot_time": iso, "market_open": bool | None,
          "warnings": [str, ...],
          "strikes": [
            {"strike": 770.0,
             "call": {"symbol","bid","ask","mid","open_interest","iv","delta","quote_time"},
             "put":  {...same keys...}},
            ...sorted ascending by strike, only strikes where BOTH the call
            and the put pass the OI filter AND have a two-sided quote — the
            straddle needs both legs quoted, and the parity conversion in the
            fit needs the put side below spot...
          ]
        }

    Market-closed behavior: never raises for missing/stale quotes — strikes
    without a usable two-sided quote are dropped and a warning is appended to
    snapshot["warnings"] so the caller (and the daily log) can see why the
    cycle produced no candidates.
    """
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import OptionSnapshotRequest, StockLatestTradeRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest

    api_key, secret = _get_credentials()
    warnings: list[str] = []

    stock_client = StockHistoricalDataClient(api_key, secret)
    option_client = OptionHistoricalDataClient(api_key, secret)
    trading_client = TradingClient(api_key, secret, paper=True)

    # --- Spot: latest trade, not a daily bar — we need where SPY is NOW ------
    latest = stock_client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=UNDERLYING)
    )[UNDERLYING]
    spot = float(latest.price)
    spot_time = latest.timestamp.isoformat()

    # Best-effort market clock; purely informational (we still proceed on
    # whatever quotes exist, per the market-closed handling contract).
    market_open = None
    try:
        market_open = bool(trading_client.get_clock().is_open)
    except Exception as exc:  # noqa: BLE001 — clock is non-essential
        warnings.append(f"could not read market clock: {exc}")
    if market_open is False:
        warnings.append("market is CLOSED — quotes below are the last posted, may be stale")

    lo_strike = spot * (1.0 - STRIKE_RANGE_PCT)
    hi_strike = spot * (1.0 + STRIKE_RANGE_PCT)

    # --- Contracts (paginated) for OI + symbols -------------------------------
    contracts = []
    page_token = None
    while True:
        resp = trading_client.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=[UNDERLYING],
            expiration_date=expiry,
            strike_price_gte=str(round(lo_strike, 2)),
            strike_price_lte=str(round(hi_strike, 2)),
            limit=10000,
            page_token=page_token,
        ))
        contracts.extend(resp.option_contracts or [])
        page_token = resp.next_page_token
        if not page_token:
            break

    if not contracts:
        warnings.append(f"no contracts returned for {UNDERLYING} {expiry} in strike band")

    # OI filter first: it's free (already in the contracts response) and cuts
    # the quote request down to strikes someone actually trades.
    by_strike: dict[float, dict] = {}
    for c in contracts:
        oi = int(c.open_interest) if c.open_interest else 0
        if oi < MIN_OPEN_INTEREST:
            continue
        strike = float(c.strike_price)
        ctype = getattr(c.type, "value", str(c.type))  # enum or plain string
        by_strike.setdefault(strike, {})[ctype] = {"symbol": c.symbol, "oi": oi}

    # Keep only strikes with BOTH legs surviving the OI filter (see docstring).
    complete = {k: v for k, v in by_strike.items() if "call" in v and "put" in v}
    dropped_oi = len(by_strike) - len(complete)
    if dropped_oi:
        warnings.append(f"{dropped_oi} strikes dropped: one leg under OI threshold {MIN_OPEN_INTEREST}")

    # --- Live quotes for the survivors, in batches ----------------------------
    symbols = [leg["symbol"] for legs in complete.values() for leg in legs.values()]
    snapshots = {}
    for i in range(0, len(symbols), _QUOTE_BATCH_SIZE):
        batch = symbols[i:i + _QUOTE_BATCH_SIZE]
        try:
            snapshots.update(option_client.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=batch)
            ))
        except Exception as exc:  # noqa: BLE001 — partial data beats no data
            warnings.append(f"quote batch failed ({batch[0]}..): {exc}")

    strikes_out = []
    dropped_quotes = 0
    for strike in sorted(complete):
        legs = complete[strike]
        row = {"strike": strike}
        for side in ("call", "put"):
            sym = legs[side]["symbol"]
            row[side] = _side_from_snapshot(sym, legs[side]["oi"], snapshots.get(sym))
        # Two-sided quotes on both legs or the strike is unusable downstream.
        if row["call"]["mid"] is None or row["put"]["mid"] is None:
            dropped_quotes += 1
            continue
        strikes_out.append(row)
    if dropped_quotes:
        warnings.append(f"{dropped_quotes} strikes dropped: no two-sided quote (market closed or illiquid)")

    snapshot = {
        "underlying": UNDERLYING,
        "expiry": expiry,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spot": spot,
        "spot_time": spot_time,
        "market_open": market_open,
        "warnings": warnings,
        "strikes": strikes_out,
    }

    # --- Persist --------------------------------------------------------------
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    fname = SNAPSHOT_DIR / f"spy_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    fname.write_text(json.dumps(snapshot, indent=2))

    for w in warnings:
        print(f"[fetch_snapshot] WARNING: {w}")
    print(f"[fetch_snapshot] spot={spot} strikes={len(strikes_out)} -> {fname.name}")

    return snapshot
