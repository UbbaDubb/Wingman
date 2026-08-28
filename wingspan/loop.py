"""
Main cycle loop — table of contents only, not working code.

Intended call order per cycle:
    1. fetch  -> data.fetch_snapshot.fetch_spy_chain_snapshot(expiry)
    2. fit    -> models.mixture_dynamics.fit_mixture(...)
    3. decide -> engine.decision.propose_trade(snapshot, fit_result)
    4. gate   -> engine.regime_gate.check_regime(context)
    5. execute-> execution.cli_client.submit_order(payload, dry_run)
    6. log    -> logging.logger.log_cycle(record)
"""


def main():
    # 1. fetch snapshot
    pass

    # 2. fit mixture model
    pass

    # 3. decide: propose a trade (or None)
    pass

    # 4. gate: check regime before sizing/executing
    pass

    # 5. execute: submit order via Alpaca CLI (or skip if stood down / no trade)
    pass

    # 6. log this cycle's record
    pass


if __name__ == "__main__":
    main()
