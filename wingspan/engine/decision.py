def propose_trade(snapshot, fit_result) -> dict | None:
    """
    Decide whether the current mispricing between market and model implied
    vols is significant enough to act on, and if so, select a trade structure.

    Intended logic: compute per-strike residuals between market IVs and the
    fitted mixture model's IVs (fit_result), filter for residuals that clear
    a significance threshold, and select an appropriate options structure
    (e.g. vertical spread, straddle) that expresses the identified mispricing.

    Args:
        snapshot: Option chain snapshot, as returned by fetch_spy_chain_snapshot.
        fit_result: Fitted model parameters, as returned by fit_mixture.

    Returns:
        dict | None: A proposed trade description, or None if no trade
        clears the significance threshold.
    """
    raise NotImplementedError("TODO: implement")
