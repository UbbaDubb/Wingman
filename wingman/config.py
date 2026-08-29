"""
Central configuration. Values marked "tunable" are starting points chosen for
defensibility, not calibrated optima — revisit before extending beyond paper
trading.
"""

import os

# --- What we trade -----------------------------------------------------------
UNDERLYING = "SPY"
EXPIRY = "2026-09-18"          # option expiration (YYYY-MM-DD)
CYCLE_INTERVAL_MINUTES = 15
MAX_NOTIONAL_PER_TRADE = 1000

# --- Data / liquidity filters (tunable) ---------------------------------------
# Only consider strikes within spot * (1 +/- STRIKE_RANGE_PCT). Outside ~12%
# for a ~3-week expiry, quotes are thin, spreads dominate any model signal,
# and deep-ITM calls carry early-exercise risk around the 2026-09-18 ex-div
# date — this band excludes them by construction.
STRIKE_RANGE_PCT = 0.12
# Minimum open interest per contract. Below this, the posted quote is not a
# reliable statement of where the market actually trades.
MIN_OPEN_INTEREST = 500
# Minimum number of usable strikes required before fitting. The mixture has 3
# free parameters (lam, sigma1, sigma2); we insist on a comfortably
# overdetermined fit so 'cost'/RMSE is a meaningful noise estimate.
MIN_FIT_STRIKES = 8

# --- Pricing inputs (tunable) --------------------------------------------------
# Short-tenor risk-free rate used for the forward and for discounting.
RISK_FREE_RATE = 0.04
# Expected SPY quarterly dividend. Ex-dividend date is 2026-09-18 — the SAME
# day as expiry — so the dividend falls just inside the option's life and must
# be netted out of the forward: F = S * e^{rT} - DIV. For this tenor the
# interest carry (~+$1.7) and the dividend (~-$1.9) nearly cancel, but we keep
# both terms explicit rather than assuming F = S.
DIVIDEND_ESTIMATE = 1.90

# --- Decision gates (tunable) ---------------------------------------------------
# A residual must exceed this multiple of the strike's own half-spread
# ((ask - bid) / 2) to count as signal rather than quote noise.
HALF_SPREAD_MULTIPLE = 0.5
# A residual must also exceed this multiple of the fit's own RMSE across all
# strikes — the model's noise floor. Below it we'd be trading our own
# estimation error.
RMSE_MULTIPLE = 1.0

# --- Execution -----------------------------------------------------------------
# Safety default: dry-run unless explicitly disabled with WINGMAN_DRY_RUN=0.
DRY_RUN = os.environ.get("WINGMAN_DRY_RUN", "1").strip().lower() not in ("0", "false", "no")
