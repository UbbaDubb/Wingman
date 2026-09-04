# Wingman

A volatility-smile trading agent for SPY options, built on Alpaca's Trading API and CLI.

**Alpaca AI Trading Agents Hackathon — Aug 31–Sep 3 2026 (official scoring window)**
Paper account: `PA3ICB1OURR1`

---

## What it does

Every 15 minutes, during market hours, Wingman:

1. **Fetches** the live SPY option chain (expiry 2026-09-18), filtered to strikes with
   open interest ≥ 500 on both legs.
2. **Fits** a two-component lognormal mixture model — Brigo–Mercurio *Mixture Dynamics* —
   to the live quotes, via nonlinear least squares in price space.
3. **Decides**: for every strike, compares the market's price to the model's price. If the
   gap clears both a spread-based and an RMSE-based materiality gate, it proposes a trade —
   a long straddle if the market looks cheap, a defined-risk short call vertical if it looks
   rich (options approval level 3 forbids naked shorts).
4. **Gates** the proposal through five independent deterministic checks (spread, per-trade
   notional, aggregate long-vol notional, per-symbol position cap, direction-conflict) plus
   an LLM-based regime layer (Gemini) that can only veto or halve a proposal — never
   originate one — based on current fit quality, drawdown, and gate activity.
5. **Executes** via the Alpaca CLI (subprocess), and **logs** every stage of every cycle,
   trade or no trade, to a daily JSONL file.

The model is arbitrage-free by construction: each mixture component individually prices at
the forward, and the two weights sum to one.

## The model

```
V_mix(K,T) = λ · V_BS(F,K,T,σ1) + (1−λ) · V_BS(F,K,T,σ2)
```

Two components — a "calm" volatility σ1 and a "stress" volatility σ2, blended by weight λ.
Brigo's own notes show N=2 is sufficient to generate a genuine smile; more components risk
overfitting a live, noisy sample refit every cycle.

## What actually happened this week

- **Monday** — the symmetric mixture couldn't capture SPY's real downward skew, so it
  systematically underpriced the upside wing. With no position awareness, the engine
  re-proposed the same signal repeatedly: 12 duplicate straddles filled, ~$21,000
  concentrated in one repeated artifact.
- **Tuesday** — three gates built and deployed before the next open: a per-symbol
  position-duplication cap, a per-trade notional cap, and the LLM regime layer. The same
  bias recurred, capped at 3 per leg instead of 9.
- **Wednesday** — the caps absorbed 19 rejected proposals against 2 fills, confirmed by
  direct replay of production data to be the same artifact meeting saturated caps, not a
  new fault. The same session surfaced a genuine gap: Alpaca's own API rejected an order as
  a potential wash trade — opposing-direction exposure on a symbol already held, which no
  internal gate checked. A direction-conflict guard and an aggregate long-vol notional cap
  were built, tested against a replay of the real incident, and deployed the same day.
- **Thursday** — zero degenerate fits, the week's best fit quality, and the regime gate
  correctly stood down three real proposals against a real intraday drawdown. Zero new
  trades submitted.

Full incident detail, numbers, and rationale quotes are in `writeup/wingman.tex`.

## Results (Mon 31 Aug 09:30 ET → Thu 3 Sep close)

| | |
|---|---|
| Cycles run | 110 |
| Orders submitted / filled | 25 / 20 |
| Regime-gate stand-downs | 3 |
| Equity | $99,998.60 → $97,086.02 (−2.91%) |

**Nearly all of the above is unrealized mark-to-market — every position expires 18 Sep,
after this hackathon closes.** One exception: on 1 Sep, after diagnosing Monday's
duplicate-position bug, 8 of 9 duplicated positions were closed manually via the CLI as an
immediate risk-reduction step, before the automated caps that now prevent this were built
later that same day. This realized +$714 and was the only manual trade placed during the
judged window.

## Known limitations

- The position cap is per-symbol, not per-cluster — the underlying bias can still spread
  across several adjacent strikes rather than being fully contained at one.
- No portfolio-level long/short volatility balancing yet.
- The symmetric mixture structurally struggles with SPY's persistent skew; a shift-extended
  MDD is the documented fix, not yet built.

## Repo structure

```
wingman/
├── loop.py                    # scheduler + main cycle orchestration
├── config.py                  # constants (gate thresholds, expiry, cadence)
├── data/fetch_snapshot.py     # live chain fetch, liquidity filtering
├── models/mixture_dynamics.py # fit_mixture, mixture_price
├── engine/
│   ├── decision.py            # residual scan, all five deterministic gates
│   └── regime_gate.py         # LLM regime layer (Gemini)
├── execution/
│   ├── order_builder.py       # straddle/vertical payload construction
│   └── cli_client.py          # Alpaca CLI subprocess wrapper
├── logging/
│   ├── logger.py              # per-cycle JSONL logging
│   ├── account_tracker.py     # per-cycle account/position snapshots
│   └── report.py              # chart + report generation from logs
├── demo_replay.py             # read-only replay tool for demo recording
└── logs/                      # daily JSONL (gitignored — local only)
```

## Setup

```
pip install -r requirements.txt
```

`.env` requires `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `GEMINI_API_KEY`. Alpaca CLI must be
separately installed and authenticated (`alpaca profile login`).

```
python -m wingman.loop --schedule          # run live (paper), respects market hours
python -m wingman.logging.report           # regenerate charts/tables from logs/
python -m wingman.demo_replay --mode summary   # replay real logged data, read-only
```

`WINGMAN_DRY_RUN` (env var, default `1`) gates whether orders actually submit.

## Infrastructure notes

Execution runs through the Alpaca CLI via subprocess, not the MCP server — the CLI needs no
client host and suits a scheduled, unattended process. Multi-leg orders (straddles,
verticals) are submitted via the raw `POST /v2/orders` escape hatch, piping a JSON payload
from a temporary file: Windows cmd's quote-escaping made the CLI's own `--legs` flag
unreliable for structured JSON, confirmed empirically before relying on it.

## Further reading

- **`writeup/wingman.tex`** — one-page write-up (AI logic, risk gates, Alpaca
  infrastructure), compiles with `pdflatex writeup/wingman.tex`.
- **`wingman_summary.html`** — full week write-up with charts.
- **`wingman_deck.pptx`** — presentation slides.
