"""
Synthetic tests for propose_trade(), focused on the 2026-09-01 risk gates.

NOTE: the pre-existing synthetic suite referenced in earlier sessions (clean,
cheap770, rich785, lowvol_sigma1) was never committed to this repo — this file
rebuilds equivalent scenarios from scratch so the repo carries its own
regression coverage, then adds the Gate A (notional cap) and Gate B
(duplicate-position) tests replicating Monday 2026-08-31's live failure mode
(9 identical K=777 straddles filled because holdings were never checked).

Run from the repo root:
    python -m wingman.tests.test_decision_gates

Plain asserts, no pytest dependency. Exit code 0 = all passed.
"""

import wingman.engine.decision as decision
from wingman.engine.decision import propose_trade
from wingman.models.mixture_dynamics import mixture_price

# Fixed synthetic market: spot exactly at forward, r=0 so put-call parity is
# simply C = P + (F - K) and the numbers are easy to verify by hand.
SPOT = 770.0
FORWARD = 770.0
TTE = 0.05
R = 0.0
PARAMS = {"lam": 0.9, "sigma1": 0.12, "sigma2": 0.4}
STRIKES = [750.0, 755.0, 760.0, 765.0, 770.0, 775.0, 780.0, 785.0, 790.0]
HALF_SPREAD = 0.02  # tight quotes so the spread gates don't interfere


def _sym(kind: str, strike: float) -> str:
    return f"SPY260918{kind}{int(round(strike * 1000)):08d}"


def make_snapshot(perturb: dict | None = None) -> dict:
    """
    Chain whose quoted mids sit exactly ON the mixture model (residual 0
    everywhere), except strikes listed in `perturb`, whose *quoted side's*
    mid is shifted by the given amount (negative = market cheap vs model).
    """
    perturb = perturb or {}
    rows = []
    for k in STRIKES:
        c = mixture_price(PARAMS, k, FORWARD, TTE, R)
        p = c - (FORWARD - k)  # parity at r=0
        delta = perturb.get(k, 0.0)
        c_mid, p_mid = c, p
        if k >= SPOT:
            c_mid += delta  # call side is the quoted side here
        else:
            p_mid += delta  # put side is the quoted side here
        rows.append({
            "strike": k,
            "call": {"symbol": _sym("C", k), "bid": round(c_mid - HALF_SPREAD, 4),
                     "ask": round(c_mid + HALF_SPREAD, 4), "mid": round(c_mid, 4),
                     "open_interest": 1000, "iv": None, "delta": None, "quote_time": None},
            "put": {"symbol": _sym("P", k), "bid": round(p_mid - HALF_SPREAD, 4),
                    "ask": round(p_mid + HALF_SPREAD, 4), "mid": round(p_mid, 4),
                    "open_interest": 1000, "iv": None, "delta": None, "quote_time": None},
        })
    return {"underlying": "SPY", "expiry": "2026-09-18", "spot": SPOT,
            "timestamp": "2026-09-01T12:00:00+00:00", "strikes": rows}


def make_fit(sigma1: float = PARAMS["sigma1"]) -> dict:
    return {"lam": PARAMS["lam"], "sigma1": sigma1, "sigma2": PARAMS["sigma2"],
            "success": True, "cost": 0.0, "forward": FORWARD, "tte": TTE, "r": R}


PASS = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS
    assert cond, f"FAIL {name}: {detail}"
    PASS += 1
    print(f"  ok  {name}")


def run():
    saved_cap = decision.MAX_NOTIONAL_PER_TRADE

    print("== scenario suite (rebuilt; original files were never committed) ==")

    ZERO_HITS = {"spread_gate_hits": 0, "notional_gate_hits": 0,
                 "position_gate_hits": 0}

    # clean: every quote on-model -> nothing clears the gates -> no trade,
    # and no gate rejections counted.
    prop, hits = propose_trade(make_snapshot(), make_fit())
    check("clean -> no trade, zero gate hits",
          prop is None and hits == ZERO_HITS, f"got {prop}, {hits}")

    # cheap ATM vol (Monday-style long-straddle path). The ~$1,933 ATM
    # straddle sits under the real $2,500 cap, so no patching needed here.
    snap_cheap = make_snapshot({770.0: -1.0})
    prop, hits = propose_trade(snap_cheap, make_fit())  # NB: 2-arg call, old signature
    check("cheap@770 -> long_straddle K=770 (old 2-arg signature still works)",
          prop is not None and prop["structure"] == "long_straddle" and prop["strike"] == 770.0,
          f"got {prop}")

    # rich ATM vol -> short call vertical with hedge at next strike up.
    prop, hits = propose_trade(make_snapshot({770.0: +1.0}), make_fit())
    check("rich@770 -> short_call_vertical 770/775",
          prop is not None and prop["structure"] == "short_call_vertical"
          and prop["strike"] == 770.0 and prop["far_strike"] == 775.0,
          f"got {prop}")

    # degenerate low-vol component -> guard fires, no trade.
    fit_lowvol = make_fit(sigma1=0.01)
    prop, hits = propose_trade(make_snapshot({770.0: -1.0}), fit_lowvol)
    check("lowvol sigma1=0.01 -> degenerate guard, no trade",
          prop is None and "sigma1" in fit_lowvol.get("degenerate_fit", ""),
          f"got {prop}, fit={fit_lowvol.get('degenerate_fit')}")

    print("== GATE A: per-trade notional cap ==")
    # Under the real cap ($2,500 since 2026-09-01) the ~$1,933 ATM straddle
    # must PASS...
    prop, hits = propose_trade(snap_cheap, make_fit())
    check(f"straddle notional ~$1,933 < ${saved_cap} -> proposed",
          prop is not None and prop["structure"] == "long_straddle", f"got {prop}")
    # ...while a genuinely oversized trade is still rejected AND counted: with
    # the cap at $1,000 (its pre-2026-09-01 value) the same straddle must be
    # refused, nothing else qualifies -> None, notional_gate_hits == 1.
    decision.MAX_NOTIONAL_PER_TRADE = 1000
    prop, hits = propose_trade(snap_cheap, make_fit())
    check("same straddle vs $1,000 cap -> rejected (None), hit counted",
          prop is None and hits["notional_gate_hits"] == 1, f"got {prop}, {hits}")
    decision.MAX_NOTIONAL_PER_TRADE = saved_cap
    # The vertical's net CREDIT notional (~$220) is far under the cap -> passes.
    prop, hits = propose_trade(make_snapshot({770.0: +1.0}), make_fit())
    check("vertical net credit ~$220 under cap -> still proposed",
          prop is not None and prop["structure"] == "short_call_vertical", f"got {prop}")

    print("== GATE B: duplicate-position check (Monday's failure mode) ==")
    call770, put770 = _sym("C", 770.0), _sym("P", 770.0)

    # Replicates Monday literally: engine wants the identical straddle it
    # already holds at/above the cap -> must NOT re-propose it.
    for qty in (decision.MAX_POSITION_QTY_PER_LEG, 9):  # at-cap and Monday's 9
        prop, hits = propose_trade(snap_cheap, make_fit(),
                                   current_positions=[{"symbol": call770, "qty": qty}])
        check(f"held qty {qty} on {call770} -> duplicate rejected (None), hit counted",
              prop is None and hits["position_gate_hits"] == 1, f"got {prop}, {hits}")

    # Below the cap -> still allowed.
    prop, hits = propose_trade(snap_cheap, make_fit(),
                               current_positions=[{"symbol": call770, "qty": 1}])
    check("held qty 1 (below cap 2) -> straddle still proposed",
          prop is not None and prop["strike"] == 770.0, f"got {prop}")

    # Fall-through: 770 blocked at the cap, weaker signal at 775 -> engine
    # must fall through to the 775 straddle, not return the duplicate — and
    # the rejection of 770 must show up in the counts alongside the winner.
    snap_two = make_snapshot({770.0: -1.0, 775.0: -0.8})
    prop, hits = propose_trade(snap_two, make_fit(),
                               current_positions={put770: 2})  # dict form also accepted
    check("770 blocked -> falls through to next-best straddle K=775, hit counted",
          prop is not None and prop["structure"] == "long_straddle"
          and prop["strike"] == 775.0 and hits["position_gate_hits"] == 1,
          f"got {prop}, {hits}")

    # dict + empty forms of current_positions behave like None.
    prop, hits = propose_trade(snap_cheap, make_fit(), current_positions={})
    check("empty positions dict == no positions", prop is not None and prop["strike"] == 770.0)

    print("== GATE A (aggregate): long-vol exposure cap across strikes ==")
    saved_agg_cap = decision.MAX_AGGREGATE_LONG_VOL_NOTIONAL
    # Three existing long straddles (750/755/760), $2,500/leg market_value ->
    # $15,000 aggregate == the real cap. A fresh straddle candidate (785,
    # never held before, passes every OTHER gate) must still be rejected.
    at_cap_positions = [
        {"symbol": _sym("C", k), "qty": 1, "market_value": 2500.0}
        for k in (750.0, 755.0, 760.0)
    ] + [
        {"symbol": _sym("P", k), "qty": 1, "market_value": 2500.0}
        for k in (750.0, 755.0, 760.0)
    ]
    prop, hits = propose_trade(make_snapshot({780.0: -1.0}), make_fit(),
                               current_positions=at_cap_positions)
    check("aggregate long-vol exposure at $15,000 cap -> fresh straddle (K=780) rejected",
          prop is None and hits["notional_gate_hits"] == 1, f"got {prop}, {hits}")
    # Verticals are explicitly unaffected by this check: same at-cap holdings,
    # a rich-vol candidate (vertical) in the same cycle must still pass.
    prop, hits = propose_trade(make_snapshot({770.0: +1.0}), make_fit(),
                               current_positions=at_cap_positions)
    check("vertical unaffected by straddle aggregate cap -> still proposed",
          prop is not None and prop["structure"] == "short_call_vertical", f"got {prop}, {hits}")
    # Comfortably under the cap ($12,000) -> a fresh straddle still proposed.
    under_cap_positions = [
        {"symbol": _sym(k2, k), "qty": 1, "market_value": 2000.0}
        for k in (750.0, 755.0, 760.0) for k2 in ("C", "P")
    ]
    prop, hits = propose_trade(make_snapshot({780.0: -1.0}), make_fit(),
                               current_positions=under_cap_positions)
    check("aggregate long-vol exposure at $12,000 (under cap) -> straddle still proposed",
          prop is not None and prop["strike"] == 780.0, f"got {prop}, {hits}")
    decision.MAX_AGGREGATE_LONG_VOL_NOTIONAL = saved_agg_cap

    print("== WASH-TRADE GUARD: opposing-direction conflict (2026-09-02 incident) ==")
    call780 = _sym("C", 780.0)
    # A fresh straddle wants to BUY call780+put780, but call780 is already
    # held SHORT — direction conflict, must reject (not just skip on notional
    # or the qty cap, which wouldn't catch this since qty magnitude is 1).
    prop, hits = propose_trade(make_snapshot({780.0: -1.0}), make_fit(),
                               current_positions={call780: -1})
    check("straddle vs already-short call780 -> direction conflict rejected",
          prop is None and hits["position_gate_hits"] == 1, f"got {prop}, {hits}")
    # Replicates the live incident exactly: a vertical's SHORT leg (770) is
    # already held LONG (e.g. bought as another trade's hedge) -> reject.
    prop, hits = propose_trade(make_snapshot({770.0: +1.0}), make_fit(),
                               current_positions={call770: 1})
    check("vertical short-leg already held LONG -> direction conflict rejected (2026-09-02 replay)",
          prop is None and hits["position_gate_hits"] == 1, f"got {prop}, {hits}")

    decision.MAX_NOTIONAL_PER_TRADE = saved_cap
    print(f"\nALL {PASS} CHECKS PASSED")


if __name__ == "__main__":
    run()
