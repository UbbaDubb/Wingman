import numpy as np
from scipy.optimize import least_squares
from scipy.stats import norm


def _bs_call_price(forward: float, strike: float, tte: float, vol: float, r: float = 0.0) -> float:
    """
    Standard Black-Scholes call price, quoted off the forward (so no separate
    dividend-yield term needed — using the forward already nets that out).

    V_BS = e^{-rT} [ F * N(d1) - K * N(d2) ]
    d1 = [ln(F/K) + 0.5*vol^2*T] / (vol*sqrt(T))
    d2 = d1 - vol*sqrt(T)
    """
    if tte <= 0 or vol <= 0:
        # Degenerate case: no time value left, or zero vol -> just intrinsic value
        return max(forward - strike, 0.0) * np.exp(-r * tte)

    sqrt_t = np.sqrt(tte)
    d1 = (np.log(forward / strike) + 0.5 * vol ** 2 * tte) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    return np.exp(-r * tte) * (forward * norm.cdf(d1) - strike * norm.cdf(d2))


def mixture_price(params: dict, strike: float, forward: float, tte: float, r: float = 0.0) -> float:
    """
    Compute the model-implied CALL PRICE for a given strike under a
    previously fitted 2-component mixture model.

    This implements the Brigo-Mercurio result directly:
    V_mix(K, T) = lambda * V_BS(F, K, T, sigma1, r)
                + (1 - lambda) * V_BS(F, K, T, sigma2, r)
    i.e. a weighted sum of two ordinary Black-Scholes call prices.
    Because each component individually prices at S0*e^{rT} in expectation
    and the weights sum to 1, this sum is automatically arbitrage-free.

    NOTE: this returns a PRICE, not an IV. We're fitting in price-space
    (see fit_mixture), so there's no need to invert back to IV unless you
    want it for display/comparison purposes later.

    Args:
        params: dict with keys 'lam', 'sigma1', 'sigma2' (as returned by fit_mixture).
        strike: Strike price to evaluate.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.
        r: Risk-free rate (only matters for discounting; using forward already
           handles the drift/dividend part).

    Returns:
        float: Model-implied call price at the given strike.
    """
    lam = params["lam"]
    sigma1 = params["sigma1"]
    sigma2 = params["sigma2"]

    price1 = _bs_call_price(forward, strike, tte, sigma1, r)
    price2 = _bs_call_price(forward, strike, tte, sigma2, r)

    return lam * price1 + (1.0 - lam) * price2


def fit_mixture(strikes, market_ivs, forward, tte, r: float = 0.0, weights=None) -> dict:
    """
    Fit a 2-component lognormal mixture (Brigo-Mercurio MDD) to the observed
    implied vol smile, via nonlinear least squares in PRICE space.

    Steps:
    1. Convert each market IV to a market PRICE using plain Black-Scholes
       (this is just forward pricing, not an inversion — cheap, no solver needed).
    2. Define a residual function: for a candidate (lam, sigma1, sigma2),
       compute the mixture's price at every strike and subtract the market
       price. This gives one residual per strike.
    3. Hand that residual function to scipy's least_squares, which runs
       Levenberg-Marquardt (a multivariate generalisation of the
       Newton-Raphson-with-vega approach you used for single-strike IV) to
       find the (lam, sigma1, sigma2) that minimises the sum of squared
       residuals across all strikes simultaneously.

    Bounds enforce the model's validity constraints directly:
        lam in [0, 1]      (mixture weight)
        sigma1, sigma2 > 0 (volatilities must be positive)
    Enforcing lam in [0,1] and requiring sigma_i > 0 is what keeps the
    no-arbitrage / valid-density property intact - no extra constraint
    needed, it falls out of the model's own structure (see lecture notes).

    Args:
        strikes: Sequence of strike prices.
        market_ivs: Sequence of observed market implied vols, aligned with strikes.
        forward: Forward price of the underlying for the relevant expiry.
        tte: Time to expiry, in years.
        r: Risk-free rate for discounting.
        weights: Optional per-strike weights for the residuals (e.g. vega-based,
                 so liquid near-the-money strikes matter more than thin wings).
                 Defaults to equal weighting (all ones) if not provided.

    Returns:
        dict: {'lam': float, 'sigma1': float, 'sigma2': float,
               'success': bool, 'cost': float}
              'success' and 'cost' are scipy diagnostics — check 'success'
              is True and 'cost' looks sane before trusting the fit.
    """
    strikes = np.asarray(strikes, dtype=float)
    market_ivs = np.asarray(market_ivs, dtype=float)

    if weights is None:
        weights = np.ones_like(strikes)
    else:
        weights = np.asarray(weights, dtype=float)

    # --- Step 1: convert market IVs to market PRICES (plain BS, no inversion) ---
    market_prices = np.array([
        _bs_call_price(forward, k, tte, iv, r)
        for k, iv in zip(strikes, market_ivs)
    ])

    # --- Step 2: residual function for the optimizer ---
    # x = [lam, sigma1, sigma2] — the vector of unknowns LM will search over
    def residuals(x):
        lam, sigma1, sigma2 = x
        params = {"lam": lam, "sigma1": sigma1, "sigma2": sigma2}
        model_prices = np.array([
            mixture_price(params, k, forward, tte, r) for k in strikes
        ])
        return weights * (model_prices - market_prices)

    # --- Step 3: initial guess ---
    # Start near the average market IV, split slightly apart so the optimizer
    # doesn't get stuck at the degenerate solution sigma1 == sigma2 (which
    # makes lambda meaningless / unidentifiable — a flat mixture is just BS).
    avg_iv = float(np.mean(market_ivs))
    x0 = [0.5, avg_iv * 0.85, avg_iv * 1.15]

    # Bounds: lam in [0,1], sigma1 and sigma2 both positive (small lower bound
    # to avoid exactly zero, generous upper bound since SPY vol won't approach it)
    lower_bounds = [0.0, 0.001, 0.001]
    upper_bounds = [1.0, 5.0, 5.0]

    result = least_squares(
        residuals,
        x0,
        bounds=(lower_bounds, upper_bounds),
        method="trf",  # trust-region reflective — handles bounds natively
    )

    lam_fit, sigma1_fit, sigma2_fit = result.x

    return {
        "lam": float(lam_fit),
        "sigma1": float(sigma1_fit),
        "sigma2": float(sigma2_fit),
        "success": bool(result.success),
        "cost": float(result.cost),
    }