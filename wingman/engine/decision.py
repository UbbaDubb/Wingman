"""
Decision engine: turn (snapshot, fitted mixture) into at most ONE trade
proposal per cycle.

Residual convention (fixed): residual = market_mid - model_price.
  - residual < 0  => market is CHEAP vs model  => buy vol => LONG STRADDLE
  - residual > 0  => market is RICH  vs model  => sell vol => SHORT CALL
                     VERTICAL (defined-risk; approval level 3 forbids naked
                     short legs, so the short call is always paired with a
                     long call further OTM in the same mleg order)
"""

import numpy as np

from wingman.config import (
    HALF_SPREAD_MULTIPLE,
    MAX_NOTIONAL_PER_TRADE,
    MAX_POSITION_QTY_PER_LEG,
    RMSE_MULTIPLE,
    TRADABLE_MONEYNESS_BAND,
)
from wingman.models.fit_utils import call_equivalent_quote
from wingman.models.mixture_dynamics import mixture_price


# Degenerate-fit guard: economically-motivated plausibility band for SPY
# implied vol. These are NOT the optimizer's search bounds (fit_mixture uses
# [0.001, 5.0] purely for numerical validity) — they encode what a realistic
# SPY vol regime can look like: below ~5% annualized has never printed even in
# the calmest markets, and above 100% would exceed the 2008/2020 panic peaks.
# A fitted component outside this band is a calibration artifact (e.g. a
# near-zero-vol component acting as a point mass), not a real vol regime.
PLAUSIBLE_SIGMA_MIN = 0.05
PLAUSIBLE_SIGMA_MAX = 1.0


def _degenerate_fit_reason(fit_result: dict) -> str | None:
    """
    A sigma outside the plausibility band means the fit "explained" the smile
    with an artifact rather than a genuine two-regime mixture — its residuals
    are not to be trusted as trading signals. Returns a human-readable reason
    if the fit is degenerate, else None.
    """
    for name in ("sigma1", "sigma2"):
        sigma = fit_result[name]
        if not (PLAUSIBLE_SIGMA_MIN <= sigma <= PLAUSIBLE_SIGMA_MAX):
            return (
                f"{name}={sigma:.4f} outside plausible SPY vol band "
                f"[{PLAUSIBLE_SIGMA_MIN}, {PLAUSIBLE_SIGMA_MAX}]"
            )
    return None


def _leg_from_side(side: dict) -> dict:
    """Copy only what order_builder needs from a quote side."""
    return {
        "symbol": side["symbol"],
        "bid": side["bid"],
        "ask": side["ask"],
        "mid": side["mid"],
    }


def _wide_leg_reason(residual: float, legs: dict) -> str | None:
    """
    Execution-spread gate on the ACTUAL legs to be traded (the residual gate
    upstream only checks the call-equivalent reference quote, which can be a
    different, tighter instrument than what we execute — e.g. a straddle's
    deep-ITM put). A leg whose own half-spread exceeds
    HALF_SPREAD_MULTIPLE * |residual| would eat the modeled edge just crossing
    the market: reject. Returns a reason string if any leg is too wide, else
    None.
    """
    edge = abs(residual)
    for name, side in legs.items():
        half_spread = 0.5 * (side["ask"] - side["bid"])
        if half_spread > HALF_SPREAD_MULTIPLE * edge:
            return (
                f"{name} leg half-spread {half_spread:.2f} > "
                f"{HALF_SPREAD_MULTIPLE} x edge {edge:.2f}"
            )
    return None


def _held_qty(current_positions) -> dict:
    """
    Normalize the current_positions argument into {symbol: abs(qty)}.
    Accepts None/empty (no positions known), a {symbol: qty} dict, or a list
    of position dicts with 'symbol' and 'qty' keys (the shape
    account_tracker's fetch returns). Quantities are taken as absolute so a
    short holding blocks re-proposal the same way a long one does.
    """
    if not current_positions:
        return {}
    if isinstance(current_positions, dict):
        return {s: abs(float(q)) for s, q in current_positions.items()}
    return {p["symbol"]: abs(float(p["qty"])) for p in current_positions}


def _notional_reason(legs_signed: dict, qty: int) -> str | None:
    """
    GATE A — per-trade notional limit (config: MAX_NOTIONAL_PER_TRADE).
    Estimated notional = |sum of signed leg mids| * qty * 100 (options
    multiplier): both mids positive for a debit structure (straddle), the
    short leg negative for a credit structure (vertical). Returns a reject
    reason if the cap is exceeded, else None. Wired in 2026-09-01 — the
    constant existed since scaffold but was enforced nowhere, and Monday's
    live session filled ~$1,750 straddles against a $1,000 cap 12 times over.
    """
    net = sum(sign * side["mid"] for sign, side in legs_signed.values())
    notional = abs(net) * qty * 100.0
    if notional > MAX_NOTIONAL_PER_TRADE:
        return f"notional ${notional:,.0f} > MAX_NOTIONAL_PER_TRADE ${MAX_NOTIONAL_PER_TRADE:,.0f}"
    return None


def _position_reason(leg_symbols, held: dict) -> str | None:
    """
    GATE B — existing-position check (config: MAX_POSITION_QTY_PER_LEG).
    The direct fix for Monday's failure: the identical K=777 straddle was
    re-proposed and filled 9 times because nothing consulted current
    holdings. If ANY leg symbol of the candidate structure is already held
    at or above the per-leg cap, reject the candidate.
    """
    for sym in leg_symbols:
        qty = held.get(sym, 0.0)
        if qty >= MAX_POSITION_QTY_PER_LEG:
            return f"already holding qty {qty:g} >= limit {MAX_POSITION_QTY_PER_LEG} on {sym}"
    return None


def propose_trade(snapshot: dict, fit_result: dict,
                  current_positions=None) -> tuple[dict | None, dict]:
    """
    Scan every strike, compute residual = market call-equivalent mid minus
    mixture model price, restrict candidates to the tradable moneyness band
    (the unshifted mixture misprices the skewed wings by construction), gate
    for materiality, and propose a structure at the single highest-conviction
    strike (largest |residual|) whose ACTUAL execution legs are also tight
    enough to keep the edge. Returns None when nothing clears every gate.

    Expects fit_result to have been enriched by the caller (loop.py) with
    'forward' and 'tte' (and 'r'), since the fit signature itself doesn't
    carry them. Sizing is fixed at qty=1 per leg — variable sizing belongs to
    the regime gate, which is not built yet.

    current_positions: what the account already holds — {symbol: qty} dict or
    list of {'symbol': ..., 'qty': ...} dicts (account_tracker's shape).
    Defaults to None (treated as no positions); live wiring in loop.py
    must pass real positions or Gate B is inert.

    Returns (proposal | None, gate_hits) where gate_hits counts candidates
    rejected during THIS scan, regardless of whether a winner was found:
    {"spread_gate_hits": int, "notional_gate_hits": int,
     "position_gate_hits": int}. The counts feed the regime gate's
    gates_binding_this_cycle context — several rejections in one cycle is a
    market-instability signal the LLM layer is told to react to.
    """
    gate_hits = {"spread_gate_hits": 0, "notional_gate_hits": 0,
                 "position_gate_hits": 0}
    # Degenerate-fit guard: refuse to trade off a fit whose sigmas sit against
    # the optimizer bounds. The reason is written back into fit_result so it
    # lands in the cycle's JSONL record (loop.py logs that same dict).
    degenerate = _degenerate_fit_reason(fit_result)
    if degenerate:
        fit_result["degenerate_fit"] = degenerate
        print(f"[decision] fit rejected as degenerate: {degenerate}")
        return None, gate_hits

    spot = snapshot["spot"]
    forward = fit_result["forward"]
    tte = fit_result["tte"]
    r = fit_result.get("r", 0.0)
    params = {k: fit_result[k] for k in ("lam", "sigma1", "sigma2")}

    # --- Residual per strike ---------------------------------------------------
    rows = []
    for row in snapshot.get("strikes", []):
        quote = call_equivalent_quote(row, spot, forward, tte, r)
        if quote is None:
            continue
        model = mixture_price(params, row["strike"], forward, tte, r)
        rows.append({
            "row": row,
            "strike": row["strike"],
            "market_mid": quote["mid"],
            "half_spread": quote["half_spread"],
            "model_price": model,
            "residual": quote["mid"] - model,
        })

    if not rows:
        return None, gate_hits

    # Fit noise floor: RMSE of the price residuals across ALL strikes. If an
    # individual residual doesn't beat the typical residual size, it's
    # indistinguishable from the model's own calibration error. (Computed here
    # rather than inside fit_mixture because mixture_dynamics.py is frozen.)
    rmse = float(np.sqrt(np.mean([c["residual"] ** 2 for c in rows])))

    # --- Moneyness band (model-limitation guard) --------------------------------
    # The unshifted 2-component mixture shares one forward across both
    # components, so it fits a near-symmetric smile and systematically
    # misprices the skewed wings (see TRADABLE_MONEYNESS_BAND in config.py,
    # per Brigo's MDD notes). Wing residuals therefore measure the model's
    # limitation, not an edge: strikes outside the band stay in `rows` (and in
    # the RMSE above) for diagnostics, but may never become the trade.
    in_band = [
        c for c in rows
        if abs(c["strike"] / spot - 1.0) <= TRADABLE_MONEYNESS_BAND
    ]

    # --- Materiality gates (both must pass) -------------------------------------
    # 1. beat the strike's own quote noise (half-spread),
    # 2. beat the fit's own noise floor (RMSE).
    candidates = [
        c for c in in_band
        if abs(c["residual"]) > HALF_SPREAD_MULTIPLE * c["half_spread"]
        and abs(c["residual"]) > RMSE_MULTIPLE * rmse
    ]
    if not candidates:
        return None, gate_hits

    # Highest conviction first. Strikes are ascending in the snapshot; we need
    # that ordering below to find the next-liquid-strike hedge leg.
    candidates.sort(key=lambda c: abs(c["residual"]), reverse=True)
    all_strikes = [c["strike"] for c in sorted(rows, key=lambda c: c["strike"])]

    held = _held_qty(current_positions)

    for cand in candidates:
        k = cand["strike"]
        row = cand["row"]

        if cand["residual"] < 0:
            # Market underpriced vs model -> own the vol: long straddle.
            # Execution-spread gate on BOTH legs we'd actually buy (the
            # call-equivalent quote gated above can be much tighter than the
            # executed legs — e.g. the deep-ITM put side of this straddle).
            wide = _wide_leg_reason(
                cand["residual"], {"call": row["call"], "put": row["put"]}
            )
            if wide:
                gate_hits["spread_gate_hits"] += 1
                print(f"[decision] K={k:g} straddle skipped: {wide}")
                continue
            # GATE A: both legs bought -> debit, both mids signed positive.
            reason = _notional_reason(
                {"call": (+1, row["call"]), "put": (+1, row["put"])}, qty=1
            )
            if reason:
                gate_hits["notional_gate_hits"] += 1
                print(f"[decision] K={k:g} straddle skipped: {reason}")
                continue
            # GATE B: refuse to stack onto legs already held at the cap.
            reason = _position_reason(
                (row["call"]["symbol"], row["put"]["symbol"]), held
            )
            if reason:
                gate_hits["position_gate_hits"] += 1
                print(f"[decision] K={k:g} straddle skipped: {reason}")
                continue
            return {
                "structure": "long_straddle",
                "underlying": snapshot["underlying"],
                "expiry": snapshot["expiry"],
                "strike": k,
                "qty": 1,
                "residual": cand["residual"],
                "half_spread": cand["half_spread"],
                "fit_rmse": rmse,
                "model_price": cand["model_price"],
                "market_mid": cand["market_mid"],
                "legs": {
                    "call": _leg_from_side(row["call"]),
                    "put": _leg_from_side(row["put"]),
                },
                "rationale": (
                    f"K={k:g}: market {cand['market_mid']:.2f} vs model "
                    f"{cand['model_price']:.2f} (residual {cand['residual']:+.2f}, gates: "
                    f"{HALF_SPREAD_MULTIPLE}x half-spread={HALF_SPREAD_MULTIPLE * cand['half_spread']:.2f}, "
                    f"rmse={rmse:.2f}) -> vol cheap, LONG STRADDLE"
                ),
            }, gate_hits

        # Market overpriced vs model -> sell it with defined risk: short call
        # vertical. Hedge leg = next liquid strike further OTM (higher). If the
        # winning strike is already the top of the filtered band there is no
        # hedge available, so this candidate is untradeable under approval
        # level 3 — fall through to the next-best candidate instead.
        higher = [s for s in all_strikes if s > k]
        if not higher:
            continue
        far_strike = higher[0]
        far_row = next(r_ for r_ in snapshot["strikes"] if r_["strike"] == far_strike)
        # Same execution-spread gate on the two call legs actually traded.
        wide = _wide_leg_reason(
            cand["residual"],
            {"short_call": row["call"], "long_call": far_row["call"]},
        )
        if wide:
            gate_hits["spread_gate_hits"] += 1
            print(f"[decision] K={k:g} vertical skipped: {wide}")
            continue
        # GATE A: short leg is sold (credit), long leg bought -> net credit.
        reason = _notional_reason(
            {"short_call": (-1, row["call"]), "long_call": (+1, far_row["call"])},
            qty=1,
        )
        if reason:
            gate_hits["notional_gate_hits"] += 1
            print(f"[decision] K={k:g} vertical skipped: {reason}")
            continue
        # GATE B: refuse to stack onto legs already held at the cap.
        reason = _position_reason(
            (row["call"]["symbol"], far_row["call"]["symbol"]), held
        )
        if reason:
            gate_hits["position_gate_hits"] += 1
            print(f"[decision] K={k:g} vertical skipped: {reason}")
            continue
        return {
            "structure": "short_call_vertical",
            "underlying": snapshot["underlying"],
            "expiry": snapshot["expiry"],
            "strike": k,
            "far_strike": far_strike,
            "qty": 1,
            "residual": cand["residual"],
            "half_spread": cand["half_spread"],
            "fit_rmse": rmse,
            "model_price": cand["model_price"],
            "market_mid": cand["market_mid"],
            "legs": {
                "short_call": _leg_from_side(row["call"]),
                "long_call": _leg_from_side(far_row["call"]),
            },
            "rationale": (
                f"K={k:g}: market {cand['market_mid']:.2f} vs model "
                f"{cand['model_price']:.2f} (residual {cand['residual']:+.2f}, gates: "
                f"{HALF_SPREAD_MULTIPLE}x half-spread={HALF_SPREAD_MULTIPLE * cand['half_spread']:.2f}, "
                f"rmse={rmse:.2f}) -> vol rich, SHORT CALL VERTICAL {k:g}/{far_strike:g}"
            ),
        }, gate_hits

    # Every candidate was rejected: wide execution legs, notional cap,
    # already-held legs, or (for rich-vol candidates) no hedge available.
    return None, gate_hits
