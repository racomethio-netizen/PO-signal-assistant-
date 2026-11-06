# Pocket Option Signal Assistant

A **technical-analysis and signal assistant** for 2-minute, 3-minute, and 15-minute
setups. It turns OHLCV candle data you provide into a structured, explainable,
probabilistic technical read — trend, momentum, volatility, price action,
candle-level quality, support/resistance, market regime (ADX-based trend
strength + possible-reversal detection), and multi-timeframe confirmation —
with transparent, configurable scoring and a strong preference for
**NO TRADE** when evidence conflicts.

## What this is NOT

- **Not an auto-trader.** Nothing in this codebase places, modifies, or cancels
  a trade on Pocket Option or any other broker. There is no browser automation,
  no clicking, no CAPTCHA bypass, and no mechanism to circumvent any platform's
  protections.
- **Not a credential store.** The app never asks for, accepts, or stores a
  Pocket Option password, 2FA code, session cookie, or auth token. There is no
  field for any of that anywhere in the code or the `.env` file.
- **Not a guarantee.** Every signal is explicitly labeled as probabilistic
  technical analysis. No indicator combination and no confidence level implies
  a certain outcome. See the risk warning attached to every signal.
- **Not a martingale system.** Stake size is always derived from the *current*
  paper balance (or a fixed amount capped by a configurable % of that balance)
  and is never increased after a loss. There is no martingale logic anywhere
  in this project.

PAPER/DEMO mode is the only mode. There is no "live" mode and no code path
that could be flipped into one.

## Architecture

```
data/        OHLCV model, validation (duplicates/gaps/stale/invalid OHLC/unclosed candle), CSV/JSON/manual loaders, Binance adapter
instruments/ The currency-pair/asset registry: what an instrument IS (forex/crypto, standard/OTC, data source) — never market data itself
indicators/  Pure deterministic indicator math: SMA/EMA(incl. 200)/slope/separation, RSI, MACD, Stochastic, Williams %R, Bollinger, ATR, Keltner, Parabolic SAR, ADX/+DI/-DI, candle-quality analysis, support/resistance
strategy/    Configurable indicator parameters, per-timeframe (2m/3m/15m) scoring weights and thresholds
signals/     The signal engine: 8 independent evidence scores -> weighted composite -> Direction/Confidence/Score, NO-TRADE filters, data-quality confidence dampening, explainable Signal model
backtest/    No-lookahead historical simulation using the exact same signal engine, metrics (incl. EV-per-stake and confidence calibration), walk-forward evaluation
paper/       PAPER (simulated) trading bookkeeping — never places a real trade
risk/        Risk manager: max stake %, max daily loss %, max consecutive losses, cooldown, ACTIVE/CAUTION/TRADING PAUSED status
llm/         LLM provider interface; Claude narrates already-computed analysis, never calculates indicators or overrides risk
alerts/      Optional Telegram alerts (rate-limited, read-only) + flags for browser/sound alerts, all analysis-only
api/         FastAPI backend wiring everything together, in-memory app state
frontend/    Vanilla HTML/CSS/JS dashboard (no build step) serving the sections below
tests/       pytest suite with deterministic fixtures (no unseeded randomness)
```

Each layer only talks to the layer below it. The LLM layer can never reach
into `risk/` or `paper/` — it only ever receives an already-finalized
`Signal` or backtest report to narrate in plain language.

### The eight evidence categories

Every signal's score is a transparent weighted sum of eight independent,
configurable-weight categories — `trend`, `momentum`, `volatility`,
`price_action`, `support_resistance`, `candle_quality`, `market_regime`,
`multi_timeframe`. Each indicator votes in exactly ONE category so
correlated indicators (e.g. RSI, Stochastic, Williams %R — all momentum
oscillators) never silently stack their influence: they're averaged
together within `momentum`, not each given a separate weighted slot.
`market_regime` is where ADX/+DI/-DI and Parabolic SAR vote (trend
strength/direction and possible-reversal detection); `candle_quality` is
where body/wick ratios, pin bars, consecutive-candle momentum, gaps, and
rejection/false-breakout-at-a-level vote.

A ninth signal — **data quality** (validation warnings, confirmation-
timeframe availability, support/resistance data sufficiency) — is
deliberately NOT a tenth weighted directional vote (it isn't inherently
bullish or bearish). Instead it's reported as `data_quality_score`
(0-100) on every signal and dampens **confidence**, not the technical
score itself: identical indicator evidence on thinner data earns a lower
confidence label, never a different score.

### Candle-closed confirmation

By default (`require_candle_closed=True`), the engine refuses to score a
candle whose time window hasn't fully elapsed yet — scoring a still-
forming candle would use a close price that can still change. This has
no effect on historical/backtest data (every candle there is already
closed by definition) and only ever triggers for genuinely live/in-
progress data, where it correctly returns `DATA UNAVAILABLE — NO SIGNAL`.

### Currency pair / instrument registry

`instruments/registry.py` ships a default catalog: the 7 forex majors, 20
forex crosses, OTC variants of the 7 majors (`EURUSD_OTC`, etc.), and a
handful of common crypto pairs. Every instrument carries explicit metadata
— base/quote currency, category, **standard vs. OTC**, data source, and
supported timeframes — and `availability_status`/`last_successful_data_timestamp`
are always computed fresh from whatever candles are actually loaded, never
cached or assumed.

Key guarantees:
- **Standard and OTC are distinct instruments** (`EURUSD` vs. `EURUSD_OTC`),
  stored and analyzed completely independently — candles for one are never
  used for the other. Generating a signal for an OTC pair with no OTC data
  loaded returns `OTC DATA UNAVAILABLE — NO SIGNAL` naming the instrument,
  not a silent fallback to standard-market candles.
- **Binance can only ever be used for its actual crypto pairs.** A fetch
  request for a forex or OTC symbol is rejected with a clear 400 before any
  network call — Binance's exchange feed is never treated as a stand-in for
  a forex or OTC price.
- **The catalog is extensible, not fixed.** `POST /api/instruments/register`
  adds any instrument not in the defaults (the "user-provided list" path),
  and uploading data under an unrecognized symbol auto-registers a generic
  entry for it rather than being rejected — existing free-form asset-string
  workflows keep working unchanged.
- **Pair comparison** (`POST /api/instruments/compare`) ranks instruments
  that currently have loaded data by data quality, trend clarity,
  confidence, volatility suitability, and distance from the nearest
  support/resistance level — explicitly never by raw win count — and
  returns `NO SUITABLE SETUP — WAIT` if nothing qualifies.

**Honesty note:** there is still no *direct* connection to Pocket Option
anywhere in this app, and none is planned — see
[Market data providers](#market-data-providers-generic-and-pluggable) below.
Standard-market forex and crypto pairs get a verified live source
(Twelve Data) **when `MARKET_DATA_PROVIDER` is configured** — until
then, forex `data_source` stays `manual` (you supply candles yourself via
CSV/JSON/manual entry, same as before) and crypto falls back to the
Binance endpoint, which Binance refuses from US-based servers such as
Render's. **OTC instruments have no live
source at all, by design** — no configured provider is ever substituted
for OTC data; you must supply OTC candles yourself. Nothing here claims
broader Pocket Option instrument coverage than what's actually been
verified.

### Known limitations (by design, for this version)

- **In-memory state.** Uploaded candles, the paper-trading session, signal
  history, and the instrument registry all live in the API process's memory
  and reset on restart. There is no database. Adding persistence is
  straightforward future work that would not change any analysis/risk logic.
- **Live data covers standard forex and crypto, when a provider is configured.**
  `data/binance_adapter.py` wires up Binance for crypto (blocked from US
  servers). `instruments/providers/` adds a generic, pluggable interface;
  its Twelve Data provider serves both standard-market forex and crypto
  once you configure `MARKET_DATA_PROVIDER` (see
  [Market data providers](#market-data-providers-generic-and-pluggable)).
  OTC pairs, stocks, and commodities still require CSV/JSON/manual entry.
- **No spread/price-quality feed.** OHLCV has no standard "spread" field, so
  the spread-based NO-TRADE filter described in the spec is not active for
  CSV/JSON/manual data; it would only become relevant if a future adapter
  supplies real spread data.

## Installation

```bash
cd pocket-option-signal-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt       # or requirements-dev.txt to also run tests
cp .env.example .env                  # fill in only what you need; everything has a safe default
```

Requires Python 3.11+.

## Environment variables

See `.env.example` for the full list with defaults. Highlights:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Optional. Enables LLM-generated plain-language explanations of signals. Without it, the app works fully and shows `AI EXPLANATION UNAVAILABLE`. |
| `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional read-only Telegram alerts. Never used to place a trade. |
| `PAPER_STARTING_BALANCE` | Default paper account balance (default `1000`). |
| `RISK_MAX_STAKE_PCT`, `RISK_MAX_DAILY_LOSS_PCT`, `RISK_MAX_CONSECUTIVE_LOSSES`, `RISK_COOLDOWN_TRADES` | Risk limits enforced by `risk/manager.py`. |
| `SIGNAL_MIN_SCORE_THRESHOLD` | Minimum composite score (0-100) required before any directional signal is produced. |
| `CORS_ORIGINS` | Allowed origins for the dashboard to call the API. |
| `MARKET_DATA_PROVIDER` | Optional. `twelvedata` or `binance`. Selects the generic live data source for `/api/data/fetch/provider`. Unset by default — the app works fully without it. See [Market data providers](#market-data-providers-generic-and-pluggable). |
| `MARKET_DATA_API_KEY` | Required when `MARKET_DATA_PROVIDER=twelvedata`. Never logged, never returned by any endpoint. |
| `MARKET_DATA_BASE_URL` | Optional override of the provider's base URL (e.g. a self-hosted proxy for testing). |

Never commit a real `.env` file — it's already in `.gitignore`.

## Running the application

```bash
python run.py
# or: python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` for the dashboard. The API itself is
browsable at `http://localhost:8000/docs` (FastAPI's automatic OpenAPI UI).

### With Docker

```bash
docker compose up --build
```

This builds the image from the included `Dockerfile` and reads configuration
from your local `.env` file (not baked into the image).

## How to import market data

Four ways, all available from the dashboard's **Market** panel or directly
via the API:

1. **CSV upload** — a header row `timestamp,open,high,low,close,volume`
   (volume optional). See `sample_data/sample_ohlcv_2m.csv`.
   ```bash
   curl -X POST http://localhost:8000/api/data/upload/csv \
     -F "asset=EURUSD" -F "timeframe=2m" -F "file=@sample_data/sample_ohlcv_2m.csv"
   ```
2. **JSON OHLCV** — an array of `{timestamp, open, high, low, close, volume}`
   objects. See `sample_data/sample_ohlcv_2m.json`.
   ```bash
   curl -X POST http://localhost:8000/api/data/upload/json \
     -H "Content-Type: application/json" \
     -d "{\"asset\":\"EURUSD\",\"timeframe\":\"2m\",\"candles\": $(cat sample_data/sample_ohlcv_2m.json)}"
   ```
3. **Manual entry** — same shape as JSON, typed directly into the dashboard or
   posted to `/api/data/upload/manual`.
4. **Fetch live from Binance (crypto pairs)** — `data/binance_adapter.py`
   implements the `MarketDataAdapter` interface against Binance's public,
   no-login, read-only klines endpoint. This has nothing to do with Pocket
   Option — it's simply a real public data source for the same underlying
   crypto pair (e.g. BTC/USDT) Pocket Option also offers, so you don't have
   to transcribe candles by hand. Binance has no native 2-minute interval,
   so a `2m` request fetches real 1-minute candles and aggregates
   (resamples) them locally — it never invents a price.
   ```bash
   curl -X POST http://localhost:8000/api/data/fetch/binance \
     -H "Content-Type: application/json" \
     -d '{"asset":"BTCUSDT","timeframe":"3m","limit":200}'
   ```
   A network or upstream failure returns a clean `502` with the reason —
   it never falls back to fabricated data.
5. **Fetch live from a configured market-data provider (standard forex and crypto pairs)**
   — only available once `MARKET_DATA_PROVIDER` is set (see
   [Market data providers](#market-data-providers-generic-and-pluggable)).
   ```bash
   curl -X POST http://localhost:8000/api/data/fetch/provider \
     -H "Content-Type: application/json" \
     -d '{"asset":"EURUSD","timeframe":"3m","limit":200}'
   ```
   Returns `503` if no provider is configured, `400` for an OTC symbol or a
   symbol the configured provider doesn't cover (checked **before** any
   network call), `401`/`429`/`502` for auth/rate-limit/network failures.

   **On the dashboard this is automatic.** Opening the page, pressing
   **Refresh**, or picking a pair/timeframe fetches live candles for all
   three timeframes (2m/3m/15m) from the right source — the configured
   provider for forex and crypto (Binance's own endpoint for crypto only
   if no provider is configured), nothing for OTC (with a message saying
   so). With **Auto-update** on (the default), the dashboard checks
   every 20 seconds but only calls the data API for a timeframe once its
   next candle has actually closed, then regenerates the signal — about
   54 API calls per hour for one pair, so Twelve Data's free tier (8 per
   minute, 800 per day) covers roughly 14 hours of continuous use. When
   the market is closed (weekends), it re-checks only every 10 minutes.

   Candles you upload yourself are never replaced by this automatic
   fetching (the dashboard sends `"keep_uploads": true`, enforced on the
   server), and uploaded and live candles are never used to confirm each
   other in multi-timeframe analysis — they usually cover different
   periods. An explicit fetch (the fetch buttons, or the API without
   `keep_uploads`) still replaces them, as before.

Valid timeframe labels: `1m, 2m, 3m, 5m, 15m, 30m, 1h`. Uploading a
confirmation timeframe (e.g. `5m` or `15m` alongside `2m`) for the same asset
lets the signal engine use real multi-timeframe confirmation — it never
fabricates a higher timeframe you didn't supply.

Every upload (including a Binance fetch) is validated for duplicate
timestamps, invalid OHLC relationships (e.g. high below low), out-of-order
candles, and gaps — bad data is rejected with a `422`/`502` and a clear
reason; nothing is silently repaired or invented.

## Market data providers (generic, pluggable)

This is **not** a connection to Pocket Option. There is no such thing
anywhere in this app, and this feature doesn't add one — it connects to an
independent market-data **vendor** whose prices track the same underlying
instruments Pocket Option also offers, the same way the existing Binance
adapter already does for crypto. Nothing below changes the signal engine,
the indicators, the currency-pair registry, the database (there isn't
one), the dashboard's other features, or the configuration format for
anything that existed before this feature.

**Explicit safety constraints (always true, not configurable):**
- Never automates login to Pocket Option.
- Never extracts, stores, or touches Pocket Option session cookies or
  auth tokens — it has no code path that could, because it never talks to
  Pocket Option at all.
- Never bypasses any platform's protections.
- Never executes a live trade automatically, or at all — every endpoint
  here is read-only market data in, nothing out.
- Never sends anything to an undocumented endpoint — only each vendor's
  own public, documented REST API.
- Never commits a credential or prints an API key; `MARKET_DATA_API_KEY`
  is held only in memory for the life of the process.

### Architecture

`instruments/providers/base.py` defines one abstract interface,
`MarketDataProvider`, that any live data source implements:

```python
get_available_instruments() -> List[ProviderInstrument]   # the vendor's own catalogue
get_latest_price(instrument) -> float
get_candles(instrument, timeframe_seconds, limit) -> List[Candle]  # closed candles only
subscribe_to_candles(instrument, timeframe_seconds)        # optional; raises NotImplementedError by default
get_data_status(instrument=None) -> DataStatus             # STEP 8 connection status
```

Two implementations ship today:
- `instruments/providers/binance_provider.py` — wraps the existing,
  unchanged `BinanceAdapter` for crypto pairs.
- `instruments/providers/twelvedata.py` — calls Twelve Data's public REST
  API for standard-market forex and crypto pairs. Details that matter for
  live use:
  - Twelve Data quotes crypto in **USD**, not USDT, so a USDT symbol maps
    to its USD pair (`BTCUSDT` → `BTC/USD`, `ETHUSDT` → `ETH/USD`). USDT
    tracks the dollar closely, and Pocket Option's own crypto instruments
    are USD-quoted, but the prices are not Binance's USDT order book. If
    Twelve Data doesn't carry a symbol, the status shows
    `UNSUPPORTED INSTRUMENT` with its message — nothing is substituted.
  - Twelve Data's newest row is always the **still-forming** current
    candle; it is dropped, so the signal engine only ever sees closed
    candles (otherwise every live signal would be refused as
    `DATA UNAVAILABLE`).
  - Twelve Data has no native 2- or 3-minute interval, so those are built
    from real 1-minute candles grouped on **clock boundaries** — 2m
    candles start on even minutes, 3m on :00/:03/:06 — matching a broker
    chart. A bucket missing any minute is dropped, never padded.
  - If the newest closed candle is old (market closed for the weekend or a
    holiday), the status becomes `STALE DATA` with an explanation; the
    real candles are still returned, never replaced.

`instruments/providers/config.py` reads `MARKET_DATA_PROVIDER` once at
startup (lazily, cached on `AppState`) and builds whichever provider is
named, raising a clear, non-crashing error for a missing API key or an
unrecognized provider name.

### Instrument safety

`check_provider_fetch_allowed()` runs **before any network call**: an OTC
symbol (`EURUSD_OTC`) is always rejected for every provider — no live
source is ever substituted for OTC data — and an instrument whose
category a provider doesn't supply is rejected (a forex symbol for
Binance; anything other than forex or crypto for Twelve Data), so a
provider can never be asked to return data for an instrument it doesn't
actually serve.

### Connection status

`GET /api/market-data/status` reflects the outcome of the most recent real
provider call — never a fabricated "ok" — as one of exactly six values:
`CONNECTED`, `DISCONNECTED`, `STALE DATA`, `AUTHENTICATION ERROR`,
`RATE LIMIT`, `UNSUPPORTED INSTRUMENT`. If the provider hasn't been called
yet since the server started, the endpoint first makes one cheap price
lookup (1 API credit) so the status is real rather than a placeholder;
`?probe=true` forces a fresh check. The dashboard shows this as a pill in
the top bar next to the risk status, with the provider's message and the
last successful update time written out underneath (tooltips don't work
on phones). Tap the pill to re-test the connection.

`GET /api/market-data/instruments` returns the provider's own instrument
catalogue (not the app's registry) — `503` if nothing is configured.

## Generating signals

```bash
# 2-minute setup
curl -X POST http://localhost:8000/api/signal/generate \
  -H "Content-Type: application/json" -d '{"asset":"EURUSD","timeframe":"2m"}'

# 3-minute setup
curl -X POST http://localhost:8000/api/signal/generate \
  -H "Content-Type: application/json" -d '{"asset":"EURUSD","timeframe":"3m"}'

# 15-minute setup
curl -X POST http://localhost:8000/api/signal/generate \
  -H "Content-Type: application/json" -d '{"asset":"EURUSD","timeframe":"15m"}'
```

Add `"explain_with_llm": true` to also get a Claude-generated plain-language
explanation (requires `ANTHROPIC_API_KEY`; otherwise you'll see
`AI EXPLANATION UNAVAILABLE` and the deterministic analysis is unaffected).

Each response contains the full `Signal` object: direction (one of
`STRONG CALL / CALL / WEAK CALL / NO TRADE / WEAK PUT / PUT / STRONG PUT`),
confidence label, 0-100 score, market regime, every indicator card, reasons
for and against, an invalidation condition, a suggested expiry window, and a
risk warning.

`GET /api/signal/multi-timeframe?asset=EURUSD` returns the 2m/3m/15m view
used by the dashboard's Multi-Timeframe section (only for timeframes that
have data uploaded).

## Backtesting

```bash
curl -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"asset":"EURUSD","timeframe":"2m","payout_ratio":0.8}'
```

The backtester runs the **exact same signal engine** used for live analysis,
one candle at a time, using only `candles[:i+1]` at each step — never a
future candle. Add `"walk_forward": true` (with optional `"n_windows"`,
`"train_ratio"`) to evaluate on held-out windows instead of the whole
dataset at once.

Results include signal counts by direction, win/loss counts, win rate,
average outcome, max losing streak, max drawdown, profit factor, and
breakdowns by timeframe/asset/market regime/confidence level — always
returned with a disclaimer that this is **historical simulation**, not
evidence of future profitability. Avoid tuning weights/thresholds against
the same data you then evaluate them on; use walk-forward windows or a
separate hold-out period instead.

## Paper trading

Paper trading is bookkeeping only — it never touches a real broker.

```bash
# 1. Generate a signal first (see above), then open a trade against it —
#    confirm:true is required.
curl -X POST http://localhost:8000/api/paper/open \
  -H "Content-Type: application/json" \
  -d '{"asset":"EURUSD","timeframe":"2m","entry_price":1.0850,"confirm":true}'

# 2. Later, settle it with the observed exit price (never fabricated).
curl -X POST http://localhost:8000/api/paper/settle \
  -H "Content-Type: application/json" \
  -d '{"trade_id":"<id from step 1>","exit_price":1.0862}'

# 3. Check the account.
curl http://localhost:8000/api/paper/account
```

Default starting balance is **$1,000**. Configure stake mode, risk limits,
and payout assumption via `POST /api/paper/config`
(`starting_balance, stake_mode, fixed_stake_amount, max_stake_pct,
max_daily_loss_pct, max_consecutive_losses, cooldown_trades,
max_trades_per_day, payout_ratio`). When a limit is hit, `GET
/api/risk/status` reports `TRADING PAUSED` with the specific reason, and no
further paper trades are opened until a new day, the cooldown elapses, or
you call `POST /api/paper/reset` with `{"confirm": true}`.

## Claude (LLM) integration

`llm/provider.py` defines a minimal `LLMProvider` interface; `llm/
anthropic_provider.py` implements it against the Claude API. Claude is used
**only** to turn an already-computed `Signal` or backtest report into plain
language — it never calculates RSI/MACD/Bollinger/etc. (all deterministic,
in `indicators/`), never decides Direction/Confidence/Score (deterministic,
in `signals/`), and can never override a risk limit or place a trade. Any
failure (missing key, network error, timeout, malformed response) falls back
to the fixed string `AI EXPLANATION UNAVAILABLE` and deterministic analysis
continues unaffected.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

159 tests cover indicator math (including ADX/+DI/-DI, Stochastic, Williams
%R, candle-quality metrics), data validation (duplicates/gaps/stale/invalid
OHLC/insufficient history/unclosed last candle), the signal engine
(including NO-TRADE conditions, that future candles never change a past
decision, the market-regime classification, and the data-quality
confidence dampener), the instrument registry (standard-vs-OTC isolation,
unsupported-pair rejection, Binance/forex/OTC source-consistency guards,
auto-registration, and pair-comparison ranking), risk limits (including
that stake never increases after a loss), paper trading, backtesting
(including a direct no-lookahead equivalence check against the live
engine, the EV-per-stake formula, and confidence calibration), the LLM
provider's failure/malformed-response handling, the Binance adapter
(symbol normalization, resampling, pagination, unclosed-candle filtering,
and upstream-failure handling — all with HTTP mocked, so the suite never
depends on live network access), and the API layer (including that paper
trades require explicit confirmation and that risk/config validation
rejects bad input).

## Limitations & risk warnings

- This is a **probabilistic technical-analysis opinion**, not a prediction.
  No signal, confidence level, or indicator combination guarantees a
  winning trade.
- Backtest and paper-trading results are **historical/hypothetical
  simulations**. They do not account for real slippage, execution latency,
  or changing broker payout rates, and are not evidence of future
  performance — even a good out-of-sample result only describes the past.
- The system intentionally prefers **NO TRADE** whenever evidence conflicts,
  data is insufficient or stale, volatility looks abnormal relative to its
  own recent history, or price sits directly at a major level.
- Nothing here automates interaction with Pocket Option or bypasses any of
  its protections. You are responsible for how you use the information this
  tool produces.
