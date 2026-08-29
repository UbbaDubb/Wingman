"""
Shared pricing/fitting helpers used by both the fit stage (loop.py) and the
decision stage (engine/decision.py), so the two stages are guaranteed to be
looking at the *same* market numbers.
"""

import numpy as np
from scipy.optimize import brentq

from wingman.models.mixture_dynamics import _bs_call_price


def implied_vol_call(price: float, forward: float, strike: float, tte: float,
                     r: float = 0.0) -> float | None:
    """
    Invert Black-Scholes to get the implied vol of a CALL from its price.

    Uses brentq (bracketing root-finder) rather than Newton because it cannot
    diverge: BS price is strictly increasing in vol, so if the target price is
    achievable at all it lies between the prices at the two vol brackets.

    Returns None when the price is outside the arbitrage-consistent range
    (below intrinsic / discounted-forward parity, or above the forward) —
    that happens routinely with stale or crossed quotes and the caller should
    just drop the strike rather than crash.
    """
    if tte <= 0 or price <= 0:
        return None

    lo, hi = 1e-4, 5.0
    # Price must sit strictly between the vol->0 limit (intrinsic on the
    # forward) and the vol->inf limit (the discounted forward itself),
    # otherwise no root exists.
    p_lo = _bs_call_price(forward, strike, tte, lo, r)
    p_hi = _bs_call_price(forward, strike, tte, hi, r)
    if not (p_lo < price < p_hi):
        return None

    try:
        return float(brentq(
            lambda vol: _bs_call_price(forward, strike, tte, vol, r) - price,
            lo, hi, xtol=1e-8,
        ))
    except ValueError:
        return None


def call_equivalent_quote(row: dict, spot: float, forward: float, tte: float,
                          r: float = 0.0) -> dict | None:
    """
    Produce the CALL price we compare against the mixture model at one strike,
    always taken from the *liquid, out-of-the-money* side of the chain:

      - strike >= spot: use the call quote directly (call is OTM there).
      - strike <  spot: the call is ITM (wide, thin); the OTM put is the
        liquid quote. Convert it to a call price via put-call parity:
            C = P + e^{-rT} * (F - K)
        Caveat, stated honestly: SPY options are American, so parity is
        strictly an inequality. With ~3 weeks of carry (~0.2%) and the only
        dividend going ex ON expiry day, the early-exercise premium in the
        strikes we keep (deep ITM already excluded by the ±12% band) is small
        relative to our decision gates, so the European approximation is
        acceptable — and the mixture model itself is European anyway.

    Returns {'mid': call-equivalent mid, 'half_spread': half-spread of the
    side actually quoted, 'source': 'call'|'put'} or None if that side has no
    usable two-sided quote.
    """
    strike = float(row["strike"])
    side_name = "call" if strike >= spot else "put"
    side = row.get(side_name)
    if not side:
        return None

    bid, ask = side.get("bid"), side.get("ask")
    # A one-sided or crossed quote is not a price, it's an absence of one.
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None

    mid = 0.5 * (bid + ask)
    half_spread = 0.5 * (ask - bid)

    if side_name == "put":
        # Parity shift; the half-spread carries over unchanged because the
        # parity term e^{-rT}(F - K) is deterministic (no quote noise in it).
        mid = mid + np.exp(-r * tte) * (forward - strike)
        if mid <= 0:
            return None

    return {"mid": float(mid), "half_spread": float(half_spread), "source": side_name}
