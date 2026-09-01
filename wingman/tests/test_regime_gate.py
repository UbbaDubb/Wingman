"""
Regime-gate tests: five REAL Gemini API calls over synthetic contexts, plus
one simulated API failure. Run from the repo root:

    python -m wingman.tests.test_regime_gate

Prints each decision + rationale verbatim (they get reviewed before loop.py
wiring, since the rationales end up quoted in reports). Asserts only the
hard requirements; where the spec allows "half" OR "stand_down" both pass.
"""

import wingman.engine.regime_gate as rg
from wingman.engine.regime_gate import check_regime

# A realistic proposal, shaped exactly like decision.py's output.
PROPOSAL = {
    "structure": "long_straddle", "underlying": "SPY", "expiry": "2026-09-18",
    "strike": 778.0, "qty": 1, "residual": -0.68, "half_spread": 0.02,
    "fit_rmse": 0.58, "model_price": 3.9, "market_mid": 3.22,
    "rationale": "K=778: market 3.22 vs model 3.90 -> vol cheap, LONG STRADDLE",
}

BASE = {
    "fit_rmse": 0.58,
    "fit_success": True,
    "proposed_trade": PROPOSAL,
    "current_positions": {"SPY260918C00778000": 1, "SPY260918P00778000": 1},
    "unrealized_pl_total": -150.0,
    "account_equity": 99500.0,
    "gates_binding_this_cycle": {"notional_gate_hits": 0, "position_gate_hits": 0},
    "event_flag": None,
}


def scenario(name: str, overrides: dict, allowed: tuple) -> dict:
    ctx = {**BASE, **overrides}
    out = check_regime(ctx)
    ok = out["decision"] in allowed
    print(f"\n[{name}] decision={out['decision']!r} (allowed: {allowed})"
          f"{'' if ok else '  <-- UNEXPECTED'}")
    print(f"  rationale: {out['rationale']}")
    assert ok, f"{name}: got {out['decision']!r}, expected one of {allowed}"
    return out


def run():
    # 1. clean context -> full
    scenario("1 clean", {}, ("full",))

    # 2. ~-$2,000 drawdown (matches today's real account state), clean fit
    #    -> half or stand_down, either defensible; rationale must cite numbers.
    out = scenario("2 drawdown -2000", {"unrealized_pl_total": -2000.0,
                                        "account_equity": 98000.0},
                   ("half", "stand_down"))
    assert any(tok in out["rationale"] for tok in ("2,000", "2000", "2%")), \
        f"rationale doesn't reference the drawdown numbers: {out['rationale']}"

    # 3. fit failed -> stand_down
    scenario("3 fit_success=False", {"fit_success": False}, ("stand_down",))

    # 4. event flag -> half or stand_down
    scenario("4 FOMC in 18h", {"event_flag": "FOMC in 18h"},
             ("half", "stand_down"))

    # 5. deterministic gates rejected 3+ candidates this cycle -> at least half
    scenario("5 gates binding x4", {"gates_binding_this_cycle":
                                    {"notional_gate_hits": 1, "position_gate_hits": 3}},
             ("half", "stand_down"))

    # 6. API failure -> fail-safe stand_down, no crash, no API dependency.
    real = rg._call_gemini
    rg._call_gemini = lambda ctx: (_ for _ in ()).throw(TimeoutError("simulated timeout"))
    try:
        out = check_regime(dict(BASE))
        print(f"\n[6 api failure] decision={out['decision']!r}")
        print(f"  rationale: {out['rationale']}")
        assert out["decision"] == "stand_down"
        assert "TimeoutError" in out["rationale"]
    finally:
        rg._call_gemini = real

    # bonus: no proposal -> short-circuit, no API call (patch would raise if called)
    rg._call_gemini = lambda ctx: (_ for _ in ()).throw(AssertionError("API called!"))
    try:
        out = check_regime({**BASE, "proposed_trade": None})
        assert out["decision"] == "stand_down" and "No trade proposed" in out["rationale"]
        print(f"\n[7 no proposal] short-circuited without API call: {out['rationale']}")
    finally:
        rg._call_gemini = real

    print("\nALL REGIME GATE TESTS PASSED")


if __name__ == "__main__":
    run()
