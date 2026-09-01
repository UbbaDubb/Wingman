"""
LLM regime gate: qualitative veto/shrink layer between decision.py's proposal
and order_builder.py, on top of (never instead of) the deterministic gates.

Contract (consumed by loop.py, which does the wiring):
  - "full"       -> pass the proposal through unchanged.
  - "half"       -> order_builder should be given HALF the proposed qty,
                    rounded down, minimum 1 (qty is currently always 1, so
                    "half" behaves like "full" until sizing grows — the
                    contract is defined now so sizing can grow later).
  - "stand_down" -> no order is submitted this cycle at all.

The gate can only REDUCE what decision.py proposed. It can never originate a
trade, never change structure/strike, never increase size. If decision.py
proposed nothing there is nothing to gate: check_regime() short-circuits
without an API call, and loop.py should not even call it in that case (the
skip saves an API call every no-trade cycle; the short-circuit here is just
belt-and-braces for other callers).

FAIL-SAFE: any API failure, timeout, or malformed response (despite JSON
mode) returns "stand_down" with the failure in the rationale. An unavailable
safety check must never become permissive. check_regime never raises.
"""

import json
from wingman.data.fetch_snapshot import _load_env_file
import os

# Gemini model for the gate. Flash-tier: this runs every 15 minutes for days,
# latency/cost matter more than depth, and the task is bounded classification.
GEMINI_MODEL = "gemini-3.6-flash"
_TIMEOUT_MS = 45_000

_VALID_DECISIONS = ("full", "half", "stand_down")

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(_VALID_DECISIONS)},
        "rationale": {"type": "string"},
    },
    "required": ["decision", "rationale"],
}

_SYSTEM_PROMPT = """\
You are the risk-regime gate of "Wingman", an automated SPY options
paper-trading agent. Each 15-minute cycle the agent fits a two-component
lognormal mixture to the SPY implied-vol smile, and a decision engine may
propose ONE defined-risk trade (a long straddle or a short call vertical,
qty 1). Deterministic gates (per-trade notional cap, per-leg position cap,
bid-ask spread checks) have ALREADY passed this proposal. You are the final,
qualitative check.

Your only power is to reduce: respond "full" (let the proposed trade through
unchanged), "half" (cut its size in half), or "stand_down" (submit nothing
this cycle). You cannot originate, enlarge, or modify trades.

Your job is caution, not cleverness. Judge the CONDITIONS for trading, not
the trade idea itself — the model already made the trade decision.
  - "full": only when nothing in the context looks concerning.
  - "half": a real but not severe reason for caution — e.g. fit RMSE
    noticeably elevated vs the ~0.5-0.65 range that is normal for this model,
    a meaningful unrealized drawdown building (roughly 1-2% of equity), or
    the deterministic gates having rejected a couple of candidates this cycle.
  - "stand_down": clear red flags — fit_success false, a scheduled market
    event in the event_flag (e.g. FOMC), a large unrealized drawdown (>~2% of
    equity), or the deterministic gates rejecting several (3+) candidates
    this cycle, which signals a noisy or unstable market where the model's
    residuals are not trustworthy.

The rationale (one or two sentences) will be quoted verbatim in a written
report: reference the SPECIFIC numbers you were given (RMSE values, dollar
P&L, equity, rejection counts, the event text). Never use generic filler
like "market conditions warrant caution" without the numbers that show it.
"""


def _call_gemini(context: dict) -> dict:
    """One structured-output Gemini call. Raises on any failure — check_regime
    owns the fail-safe. Split out so tests can simulate API failure."""
    from google import genai
    from google.genai import types

    _load_env_file()  # populates GEMINI_API_KEY from wingman/.env if needed
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set (environment or wingman/.env)")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
    )
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            "Cycle context (JSON):\n"
            + json.dumps(context, indent=2, default=str)
            + "\n\nReturn your gating decision."
        ),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0.2,  # consistency over creativity; runs unattended
        ),
    )
    return json.loads(resp.text)


def check_regime(context: dict) -> dict:
    """
    Ask Gemini to assess the current cycle's conditions and gate the proposed
    trade's sizing. See module docstring for the full contract.

    Args:
        context: {
          "fit_rmse": float, "fit_success": bool,
          "proposed_trade": dict | None,       # decision.py output
          "current_positions": dict,           # symbol -> qty
          "unrealized_pl_total": float,        # dollars, across all positions
          "account_equity": float,
          "gates_binding_this_cycle": dict,    # e.g. {"notional_gate_hits": 1,
                                               #       "position_gate_hits": 2}
          "event_flag": str | None,            # e.g. "FOMC in 18h"
        }

    Returns:
        {"decision": "full" | "half" | "stand_down", "rationale": str}
        Never raises; every failure path returns "stand_down".
    """
    # Nothing proposed -> nothing to gate; don't spend an API call. loop.py
    # skips the call entirely in this case — this branch covers other callers.
    if context.get("proposed_trade") is None:
        return {
            "decision": "stand_down",
            "rationale": "No trade proposed this cycle — nothing to gate (no API call made).",
        }

    try:
        result = _call_gemini(context)
        decision = result.get("decision")
        rationale = str(result.get("rationale", "")).strip()
        if decision not in _VALID_DECISIONS or not rationale:
            raise ValueError(f"malformed gate response: {result!r}")
        return {"decision": decision, "rationale": rationale}
    except Exception as exc:  # noqa: BLE001 — FAIL SAFE, never permissive
        return {
            "decision": "stand_down",
            "rationale": (
                "Regime gate unavailable "
                f"({type(exc).__name__}: {exc}) — failing safe to stand_down; "
                "an unavailable safety check must never become permissive."
            ),
        }
