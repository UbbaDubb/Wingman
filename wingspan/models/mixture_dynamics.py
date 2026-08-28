def fit_mixture(strikes, market_ivs, forward, tte) -> dict:
    """
    Fit a mixture-of-distributions model to the observed implied vol smile.

    Args:
        strikes: Sequence of strike prices.
        market_ivs: Sequence of observed market implied vols, aligned with strikes.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.

    Returns:
        dict: Fitted mixture parameters.
    """
    raise NotImplementedError("TODO: implement")


def mixture_price(params, strike, forward, tte) -> float:
    """
    Compute the model-implied price (or IV) for a given strike under a
    previously fitted mixture model.

    Args:
        params: Fitted mixture parameters, as returned by fit_mixture.
        strike: Strike price to evaluate.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.

    Returns:
        float: Model-implied price or IV at the given strike.
    """
    raise NotImplementedError("TODO: implement")
