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
# Raised 1000 -> 2500 on 2026-09-01 when the notional gate was first wired in:
# an ATM SPY straddle costs ~$1,750-2,050, so $1,000 would have banned
# straddles outright. 2500 keeps single straddles tradeable while Gate B
# (MAX_POSITION_QTY_PER_LEG) caps duplication regardless of notional.
MAX_NOTIONAL_PER_TRADE = 2500
# Max contracts already held per individual leg symbol before the engine
# refuses to propose a structure touching that symbol again. Added 2026-09-01
# after Monday's live session: the engine re-proposed the same K=777 straddle
# every 15 minutes (the upside-wing model artifact persisted all afternoon)
# and NINE of them filled — ~$21k of premium in one repeated position,
# because nothing ever checked existing holdings. See logs/2026-08-31.jsonl.
MAX_POSITION_QTY_PER_LEG = 2
# Aggregate cap on dollar exposure across ALL currently-held long straddle
# positions (verticals are unaffected — typically short-vol/defined-risk and
# already capped differently). Added 2026-09-02 after the Mon-Wed live data
# showed the moneyness-artifact band producing a new tradeable strike nearly
# every day (774 Mon, 775/776/777/778 Tue-Wed) — MAX_POSITION_QTY_PER_LEG
# caps each individual strike, but nothing previously stopped the SUM across
# strikes from growing indefinitely as the artifact keeps finding fresh room.
MAX_AGGREGATE_LONG_VOL_NOTIONAL = 15000

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
# Only strikes within spot * (1 +/- TRADABLE_MONEYNESS_BAND) may become the
# winning trade candidate. The unshifted 2-component mixture (both lognormals
# sharing one forward) produces a roughly symmetric smile around the forward,
# not SPY's monotonic put skew — Brigo's MDD lecture notes state this
# explicitly (the shifted extension is the proper fix, not implemented this
# week). Outside this band, large residuals measure that model limitation,
# not market mispricing, so the decision engine must not act on them. Strikes
# outside the band still participate in the fit and the RMSE noise floor.
# Tightened 0.05 -> 0.02 -> 0.015 on 2026-08-31: three consecutive live
# cycles (14:41, 14:56, 15:12 UK) all proposed straddles within 1.87-2.05%
# of spot, tracking spot's movement rather than reflecting an independent
# signal — confirmed structural artifact of the unshifted 2-component
# mixture (documented above: it can't capture SPY's downward skew, so it
# systematically underprices the upside wing at a roughly constant moneyness
# offset). 0.015 excludes this cluster with margin.
TRADABLE_MONEYNESS_BAND = 0.015
# A residual must exceed this multiple of the strike's own half-spread
# ((ask - bid) / 2) to count as signal rather than quote noise.
HALF_SPREAD_MULTIPLE = 0.5
# A residual must also exceed this multiple of the fit's own RMSE across all
# strikes — the model's noise floor. Below it we'd be trading our own
# estimation error.
RMSE_MULTIPLE = 1.0

# --- Regime gate ----------------------------------------------------------------
# Manual event note passed to the LLM regime gate each cycle, e.g.
# "FOMC in 18h". Human-entered only — no live calendar fetching (by design).
# None = no known upcoming event.
EVENT_FLAG = None

# --- Execution -----------------------------------------------------------------
# Safety default: dry-run unless explicitly disabled with WINGMAN_DRY_RUN=0.
DRY_RUN = os.environ.get("WINGMAN_DRY_RUN", "1").strip().lower() not in ("0", "false", "no")
