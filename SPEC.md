# NSE Intraday Algo — Build Spec

Strategy name: **Sentinel ORB** — "Stocks-in-Play Opening-Range Breakout with VWAP trend,
market-sentiment bias and live news guard".

Context: one-day NSE (India) intraday algo-trading competition. Judged on **return + presentation**,
so every rule must be explainable, and every decision the bot makes must be logged with its reason.
Long and short (MIS) allowed. Broker: **Kotak Neo Trade API** (`neo_api_client`). Language: Python 3.11+
(machine has 3.14; use a venv at `NSE-Algo/.venv`). All times are **Asia/Kolkata**.

## 1. Hard rules (non-negotiable)

| Rule | Value |
|---|---|
| Risk per trade | 1.25% of day-start equity (before multipliers) |
| Daily loss limit | −5% of day-start equity (realized + unrealized) → flatten everything, halt for the day |
| Entry window | 09:30 – 14:15 |
| Forced square-off | 14:50 — everything flat **before 15:00** (retry until flat, escalate to market orders) |
| Max attempts per stock | 2 |
| Max trades per day | 8 |
| Max concurrent positions | 3 |
| Max notional per position | 40% of equity × leverage cap (config, default leverage cap 3×) |
| Max gross exposure | 3× equity (config) |
| Min stop distance | 0.30% of price (prevents huge qty on tiny stops) |
| Kill switch | `STOP` file in project root or Ctrl+C → flatten all, exit |

All values live in `config.yaml`; the table above is the default.

## 2. Daily timeline

| Time (IST) | Step |
|---|---|
| 08:30 | Start, login to broker, load instrument master, load universe & 20-day daily history |
| 08:30–09:15 | **Pre-market sentiment** score (§3) + overnight news sweep (§4) |
| 09:00–09:08 | NSE pre-open: capture indicative open → gap % per stock |
| 09:15–09:30 | Build **opening range** (OR) for all universe stocks; compute stock-in-play ranking |
| 09:30 | Select top N (default 5) **stocks in play** (§5) |
| 09:30–14:15 | Evaluate entries on every 5-min candle close (§6); manage open trades on every tick/candle (§7) |
| every 90 s | News poll → update stock scores, market news score, bias, watchlist (§4) |
| every 15 min | Re-rank watchlist (may swap a non-held stock in/out) |
| 14:15 | No new entries |
| 14:50 | Square off all. Write end-of-day report. |

## 3. Pre-market sentiment (day bias)

Inputs (each converted to a component score in [−1, 1] with `tanh(x / scale)`):

| Input | Source (with fallback) | Weight | Scale |
|---|---|---|---|
| GIFT Nifty gap vs Nifty prev close | scrape (NSE IX / public page) → fallback CLI `--gift-nifty <value>` → fallback skip & renormalize | 0.35 | 0.6% |
| US index futures change (ES=F, NQ=F) | yfinance | 0.15 | 0.6% |
| US prior close change (^GSPC, ^IXIC, ^DJI avg) | yfinance | 0.10 | 1.0% |
| Asia (^N225, ^HSI, ^KS11 avg, current) | yfinance | 0.15 | 0.8% |
| India VIX level vs 20-day avg (negative) | yfinance ^INDIAVIX | 0.10 | 10% |
| USD/INR change (INR=X, negative) | yfinance | 0.05 | 0.3% |
| Brent crude change (BZ=F, negative) | yfinance | 0.05 | 2% |
| FII net cash flow prev day | NSE fiidii API (cookie session) → skip | 0.05 | ₹3000 cr |

Missing inputs are skipped and the remaining weights renormalized. Every component, its raw value
and score are logged and saved to `reports/<date>/premarket.json` (great for the presentation).

`premarket_score` ∈ [−1, 1]. Live: `day_bias_score = 0.7 × premarket_score + 0.3 × market_news_score`
(recomputed after each news poll). Also intraday, after 09:30, blend in Nifty vs its VWAP:
`bias = 0.5 × day_bias_score + 0.5 × nifty_trend_score` where nifty_trend_score = tanh((nifty−vwap)/vwap / 0.3%).

Regime from bias: `> +0.25` BULLISH, `< −0.25` BEARISH, else NEUTRAL.

Size multipliers:
- Direction aligned with regime (or NEUTRAL): 1.0; against regime: 0.5.
- VIX multiplier: VIX ≤ 15 → 1.0; 15–20 → 0.8; > 20 → 0.6.

## 4. News engine

- Sources (configurable list): Moneycontrol, Economic Times Markets, Business Standard markets,
  LiveMint markets RSS; Google News RSS queries ("NSE stock", "Sensex Nifty", per watchlist symbol
  name); NSE corporate announcements API (cookie session). Poll every 90 s, timeouts, never crash
  the trading loop (run in a background thread; trading continues on stale scores).
- Dedupe by normalized-title hash. Ignore items older than 18 h.
- Symbol mapping: `universe.csv` has symbol, company name, aliases (e.g. "RIL", "Reliance Industries").
  Match aliases in title/summary. Market-wide tags: RBI, repo rate, Fed, inflation, CPI, GDP, budget,
  war, crash, tariff, sanctions, etc.
- **Stage 1 keyword scorer** (always on, instant, transparent): weighted positive/negative finance
  lexicon (e.g. "order win", "beats estimates", "upgrade", "raises guidance" vs "downgrade",
  "fraud", "probe", "SEBI ban", "misses", "resigns", "default", "fire", "raid"). Produces score [−1,1].
- **Stage 2 Gemini** (google-genai SDK, model from config, default `gemini-3.8-flash`, key from
  `GEMINI_API_KEY`): only for headlines that map to a watchlist/universe stock or are market-wide and
  have |keyword score| ≥ 0.2 or contain a trigger keyword. Batch up to 10 headlines per call,
  strict JSON output: `[{id, symbols[], market_wide, sentiment(-1..1), severity(0..1), horizon("intraday"|"longer"), reason}]`.
  Rate-limit (config, default 10 calls/min), cache by hash, on any failure fall back to keyword score.
- Final headline score = Gemini score if available else keyword score; weight by severity.
- Per-stock news score: sum of headline scores with exponential decay (half-life 90 min), clipped [−1,1].
  Market news score: same over market-wide headlines.
- **How news changes trading (filter + bias + watchlist; news never opens a trade by itself):**
  - stock score ≤ −0.5 → block longs; if long open → exit if severity ≥ 0.7, else tighten stop to breakeven or −0.5R.
  - stock score ≥ +0.5 → block shorts; mirror rule for open shorts.
  - |stock score| ≥ 0.4 → ranking boost (§5).
  - market news score feeds day bias (§3). A severe market-wide negative (severity ≥ 0.8, score ≤ −0.6)
    → pause new longs 15 min.
- Every headline used, its score and the action taken is logged to `reports/<date>/news.csv`.

## 5. Universe & stocks-in-play selection

- Universe: `universe.csv` — Nifty 50 + liquid F&O stocks (~100). Include symbol, yfinance ticker (`X.NS`),
  company name, aliases. Kotak instrument token resolved at runtime from scrip master.
- Pre-filters: price ≥ ₹100, 20-day avg daily traded value ≥ ₹200 cr.
- Ranking score at 09:30 (z-scored across universe):
  `0.35·|gap%| / ATR%_daily + 0.35·RVOL_OR + 0.15·OR_width/ATR_5m-normalized + 0.15·|news score|`
  where RVOL_OR = OR volume / avg first-15-min volume (fallback: / (avg daily vol × 0.12)).
- Exclude: OR width > 2.5 × daily ATR% (too wild) or < 0.25% (dead).
- Pick top N=5. Re-rank every 15 min; held positions always stay.

## 6. Entry rules (evaluated on 5-min candle close, symbols in watchlist only)

Indicators per stock: session VWAP, ATR(14) on 5-min candles (seeded with previous days' candles where
available), EMA(9) on 5-min closes, RVOL of candle = candle volume / avg volume of the same time-of-day
slot over last 10 days (fallback: avg 5-min volume of last 10 days).

LONG when all:
1. close > OR high
2. close > VWAP
3. candle RVOL ≥ 1.5
4. regime is not BEARISH, **or** stock news score ≥ +0.4 (counter-trend allowed only with news support, at 0.5 size)
5. stock news score > −0.3, and no long-pause active
6. not extended: close − OR high ≤ 0.6 × ATR_5m
7. attempt/trade/position limits OK, time in entry window, not halted

SHORT: exact mirror (close < OR low, close < VWAP, regime not BULLISH or news ≤ −0.4, news < +0.3, etc.).

Entry order: market (MIS). Record `reason` string listing every condition value.

## 7. Stops, sizing, management

- Initial stop (long): `max(OR low, entry − 1.0 × ATR_5m)`, but stop distance ≥ 0.30% of entry. Mirror for short.
- R = |entry − stop|.
- Qty = floor( risk_amount / R ), risk_amount = 1.25% × day-start equity × direction multiplier × VIX multiplier.
  Then cap by max notional per position and gross exposure. Skip if qty < 1.
- Stop is held **by the bot** (checked on every tick; in backtest on candle high/low, stop assumed hit first
  if both stop and target in same candle — conservative). Optionally also place a broker SL-M order
  (config `broker_side_stops: true`) as crash protection; cancel/modify it on exit/trail.
- At +1R: move stop to breakeven (+ costs).
- At +2R: book 50%; remainder trails: stop = max(current stop, min(VWAP, EMA9) − 0.1·ATR) for long
  (updated on candle close). Mirror for short.
- Exit on: stop, trail, news rule, daily-loss halt, 14:50 square-off, kill switch.

## 8. Costs (backtest + paper P&L)

Configurable; defaults for NSE equity intraday: brokerage ₹0/order (Kotak Neo intraday; make configurable),
STT 0.025% on sell value, exchange txn 0.00297%, SEBI ₹10/crore, stamp 0.003% on buy, GST 18% on
(brokerage + exchange + SEBI), slippage 0.03% per side.

## 9. Architecture

```
NSE-Algo/
  config.yaml            all parameters (§1–§8)
  .env.example           KOTAK_CONSUMER_KEY, KOTAK_CONSUMER_SECRET, KOTAK_MOBILE, KOTAK_UCC,
                         KOTAK_MPIN, KOTAK_TOTP_SECRET, GEMINI_API_KEY   (.env is git-ignored; never log secrets)
  requirements.txt
  universe.csv
  algo/
    config.py            load yaml + env, dataclass config
    clock.py             IST clock; injectable (backtest uses simulated time)
    indicators.py        VWAP, ATR, EMA, RVOL, opening range, candle builder (ticks → 5-min candles)
    sentiment/
      premarket.py       §3
      news.py            §4 fetchers, dedupe, symbol mapping, decay store
      keyword.py         lexicon scorer
      gemini.py          batch LLM scorer with cache + rate limit + fallback
    selection.py         §5
    strategy.py          PURE functions: given state → signals (shared by live and backtest)
    risk.py              sizing, limits, daily-loss guard, square-off
    portfolio.py         positions, P&L, costs
    broker/
      base.py            Broker interface: login, ltp/quotes, subscribe, place_order, cancel, positions, funds
      paper.py           simulated fills at LTP ± slippage (uses real live data feed)
      kotak_neo.py       neo_api_client adapter (TOTP login: totp_login → totp_validate with MPIN, TOTP via pyotp;
                         scrip master for tokens; websocket subscribe for ticks; place_order MIS/MKT/nse_cm)
    engine.py            live scheduler/event loop (§2), threads for news; heartbeat; reconnects
    report.py            EOD report + decision log → reports/<date>/
  backtest/
    data.py              yfinance 5-min (last ~60 days) + daily; cache to data/ as parquet/csv
    run.py               replay day by day through the SAME strategy/risk code; premarket proxy for history:
                         Nifty open gap (as GIFT proxy) + historical US/Asia/VIX daily closes; news not
                         replayed (state this clearly in output)
    metrics.py           total return, per-day returns, win rate, profit factor, avg R, max drawdown,
                         Sharpe (daily), best/worst day, trades/day, % days hitting loss limit
    plots.py             equity curve, daily P&L bars, R-multiple histogram, 3 example trade charts
                         (candles + OR + VWAP + entry/stop/exit markers) → reports/backtest/
  run_live.py            `python run_live.py --mode paper|live [--gift-nifty 24850] [--dry-run]`
                         live mode requires typing CONFIRM at startup
  run_backtest.py        `python run_backtest.py --days 59`
  tests/                 pytest: indicators, sizing, stop/trail logic, daily-loss halt, square-off time,
                         news scoring & decay, selection, strategy signals on synthetic candles,
                         backtest smoke on synthetic data. No network in tests.
```

Engineering requirements:
- Strategy/risk logic is pure and shared by backtest and live — no duplicate logic.
- Every decision (enter / skip-with-reason / exit / news action / bias change) → structured log line
  (JSON lines in `logs/<date>.jsonl`) + human-readable console line.
- Network calls: timeouts, retries with backoff, never crash the loop; broker order errors logged and retried
  (max 3) for exits; entries are not retried after 1 failure.
- Kotak Neo SDK: install from the official GitHub repo (Kotak-Neo/kotak-neo-api). Read the installed SDK
  source to confirm exact method names/params (login flow changed across versions); keep all SDK calls
  inside `kotak_neo.py`.
- Paper mode must work with zero broker credentials (use yfinance 1-min polling as the price feed fallback).
- No secrets in code, logs or reports.
