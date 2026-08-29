"""
Turn a decision-engine proposal into an Alpaca /v2/orders payload.

Both structures are submitted as multi-leg ("mleg") orders — this is not a
style choice, it's the approval-level-3 constraint: a short option leg is only
permitted if the covering long leg arrives in the SAME order, and mleg is the
only order class that guarantees that atomicity.

Payload field notes (all values strings, per Alpaca's API):
  - limit_price sign convention for mleg: positive = net DEBIT you pay,
    negative = net CREDIT you receive.
  - client_order_id: fresh uuid4 per payload, so a retry of a failed submit
    can never double-fill (Alpaca rejects duplicate client_order_ids).
"""

import uuid
from datetime import date


def occ_symbol(underlying: str, expiry: str, opt_type: str, strike: float) -> str:
    """
    Build an OCC contract symbol, e.g. SPY260918C00650000:
    root + YYMMDD + C/P + strike*1000 zero-padded to 8 digits.
    Snapshot rows already carry Alpaca's symbols; this is the fallback for
    anything constructed outside a snapshot.
    """
    d = date.fromisoformat(expiry)
    strike_int = int(round(strike * 1000))
    return f"{underlying}{d.strftime('%y%m%d')}{opt_type[0].upper()}{strike_int:08d}"


def _base_payload(limit_price: float) -> dict:
    return {
        "order_class": "mleg",
        "qty": "1",
        "type": "limit",
        # Round to the $0.01 option tick; format as string per API contract.
        "limit_price": f"{limit_price:.2f}",
        "time_in_force": "day",
        "client_order_id": str(uuid.uuid4()),
        "legs": [],
    }


def build_straddle(proposal: dict) -> dict:
    """
    Long straddle: buy call + buy put at the same strike/expiry.
    Limit = sum of the two legs' mids — we're willing to pay fair mid for the
    package, no more; if the market has moved away, the day order just expires.
    """
    call = proposal["legs"]["call"]
    put = proposal["legs"]["put"]

    net_debit = call["mid"] + put["mid"]
    payload = _base_payload(net_debit)  # positive => net debit
    payload["legs"] = [
        {"symbol": call["symbol"], "ratio_qty": "1",
         "side": "buy", "position_intent": "buy_to_open"},
        {"symbol": put["symbol"], "ratio_qty": "1",
         "side": "buy", "position_intent": "buy_to_open"},
    ]
    return payload


def build_vertical(proposal: dict) -> dict:
    """
    Short call vertical: sell the near call, buy the further-OTM call.
    Net credit = near mid - far mid; encoded as a NEGATIVE limit_price per
    Alpaca's mleg convention (negative = credit received).
    """
    short = proposal["legs"]["short_call"]
    long_ = proposal["legs"]["long_call"]

    net_credit = short["mid"] - long_["mid"]
    payload = _base_payload(-net_credit)  # negative => net credit
    payload["legs"] = [
        {"symbol": short["symbol"], "ratio_qty": "1",
         "side": "sell", "position_intent": "sell_to_open"},
        {"symbol": long_["symbol"], "ratio_qty": "1",
         "side": "buy", "position_intent": "buy_to_open"},
    ]
    return payload
