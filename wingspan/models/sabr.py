def fit_sabr(strikes, market_ivs, forward, tte) -> dict:
    """
    Fit a SABR model to the observed implied vol smile.

    NOTE: SABR here is comparison-only — a reference fit for sanity-checking
    the mixture model. This never feeds the decision engine directly.

    Args:
        strikes: Sequence of strike prices.
        market_ivs: Sequence of observed market implied vols, aligned with strikes.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.

    Returns:
        dict: Fitted SABR parameters (alpha, beta, rho, nu).
    """
    raise NotImplementedError("TODO: implement")


def sabr_iv(params, strike, forward, tte) -> float:
    """
    Compute the SABR-implied volatility for a given strike.

    NOTE: SABR here is comparison-only — a reference fit for sanity-checking
    the mixture model. This never feeds the decision engine directly.

    Args:
        params: Fitted SABR parameters, as returned by fit_sabr.
        strike: Strike price to evaluate.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.

    Returns:
        float: SABR-implied volatility at the given strike.
    """
    raise NotImplementedError("TODO: implement")
