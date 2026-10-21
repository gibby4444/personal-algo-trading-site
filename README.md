# Algo desk

Brett's personal trading dashboard. One Python service serves the pages and the JSON API. Market regime feeds a screener, the screener feeds a strategy, and the strategy can be backtested or paper-traded. Live order placement is not implemented.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export APP_PASSWORD='choose-a-password'
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export COOKIE_SECURE=false
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000. With `DATABASE_URL` unset, the app uses SQLite at `./data/local.db` and creates the tables on startup. The free data source is [yfinance](https://github.com/ranaroussi/yfinance). No market-data key is required.

`APP_PASSWORD` empty means the site stays closed. `/health` still answers so a platform health check can pass, and the page tells you which variable is missing.

## Pages

- **Market mood** (`/`). SPY and QQQ versus their 200-day averages, VIX versus 20, a VIX-scaled sentiment proxy, and the share of a 10-ETF basket above its 50-day average. Today's screener flags, strategy P&L, the pipeline run button, and the kill switch.
- **Screener lab** (`/screeners`). Save a screen and run it. The seeded screen is `SmallBioGap`: small-cap biotech watchlist, price 2–20, gap above 10%, relative volume above 3, only when the regime is risk-on.
- **Strategy library** (`/strategies`). Seeded strategies and their latest backtest stats. Each has a backtest / paper / live switch.
- **Backtest runner** (`/backtests`). Pick a strategy and symbols such as `TSLA` or `QQQ`, a date range, and a cost in basis points. The result is an equity curve, drawdown, stats, a trade list, and a CSV.

## Pipeline

1. **Regime.** Risk-on when SPY and QQQ are both above their 200-day averages and VIX is under 20. Risk-off when both are below the average, or VIX is at least 25. Anything else is mixed. A pipeline whose rule requires `risk_on` stops here when the regime does not match.
2. **Screener.** The saved filters run on the latest daily bar of the watchlist. Hits are stored in `screener_results`.
3. **Strategy.** Those names become orders, sized by the strategy's max position. Risk checks (kill switch, position cap, total exposure, orders per minute, daily loss) run next.
4. **Broker.** Paper mode fills in the local simulator, or at Alpaca's paper API when `ALPACA_PAPER_KEY` and `ALPACA_PAPER_SECRET` are set. The paper URL must contain `paper`. Live mode records a rejection. This build has no code path that posts to the live trading host.

## Seeded strategies

| Name | What it does |
|---|---|
| TQQQ/GLD 200-DMA Rotation | At the close, hold TQQQ when QQQ is above its 200-day average, otherwise hold GLD. Cash until the average exists. |
| Gap-Up Momentum | Daily version of gap-and-go: a gap above 10% that held the open, bought at the next open. The 5-minute opening-range rule needs intraday bars. |
| SMA Crossover | Long when the 20-day average is above the 50-day average. |

Backtests take the signal at the close and fill at the next open. The default cost is 5 bps of turnover. Every run checks that each sampled signal is unchanged when later bars are removed, and that prices and signals contain no NaN or inf.

## Auth

One password, compared with argon2 against a hash made at process start. The cookie is HttpOnly, SameSite=Strict, and Secure when `COOKIE_SECURE=true` (the default on Railway). Idle sessions end after 12 hours. Eight failed logins from one address lock the form for 15 minutes.

Going live, when you choose to unlock the switch, also requires the typed phrase `GO LIVE <strategy name>`, a finished backtest, and `PAPER_DAYS_REQUIRED` distinct days that already have paper fills (default 5). If `TOTP_SECRET` is set, the form also requires a valid code. Resetting the kill switch requires the phrase `RESET KILL SWITCH`. Live strategies are not turned back on by the reset.

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `APP_PASSWORD` | yes, or the site stays closed | Login password |
| `SESSION_SECRET` | yes on Railway | Cookie signing key |
| `COOKIE_SECURE` | no | `true` on Railway, set `false` for local http |
| `PORT` | set by Railway | HTTP port |
| `DATABASE_URL` | on Railway | Postgres URL. `postgres://` is rewritten to `postgresql+psycopg://` |
| `DATA_PROVIDER` | no | `yfinance` (default), `alpaca`, `massive`, `databento`, `ccxt`, `thetadata` |
| `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET` | no | Paper orders and, if selected, Alpaca data |
| `ALPACA_PAPER_BASE_URL` | no | Default `https://paper-api.alpaca.markets` |
| `LIVE_TRADING_ENABLED` | no | Must be `true` to flag a strategy live. Default `false` |
| `ALPACA_LIVE_KEY` / `ALPACA_LIVE_SECRET` | no | Required together with the flag above. Not used to send orders |
| `TOTP_SECRET` | no | If set, the live switch also requires a TOTP code |
| `MASSIVE_API_KEY` | no | Massive (formerly Polygon) aggregates |
| `DATABENTO_API_KEY` / `DATABENTO_DATASET` | no | Futures history. Needs `pip install databento` |
| `THETADATA_USER` / `THETADATA_PASS` / `THETADATA_BASE_URL` | no | Theta Terminal sidecar, default `http://127.0.0.1:25510` |
| `CCXT_EXCHANGE` / `CCXT_API_KEY` / `CCXT_API_SECRET` | no | Crypto bars. Needs `pip install ccxt` |
| `MAX_DAILY_LOSS_USD` | no | Desk-wide daily loss limit. Default `500` |
| `MAX_TOTAL_EXPOSURE_USD` | no | Default `100000` |
| `MAX_ORDERS_PER_MINUTE` | no | Default `10` |
| `PAPER_DAYS_REQUIRED` | no | Default `5`. Set `0` to skip that live-switch gate |
| `COST_BPS` | no | Default backtest cost. Default `5` |
| `ALERT_WEBHOOK` | no | POST target for kill-switch, loss-limit, and backtest errors |

Do not commit a filled-in `.env`. `.env.example` lists the same names with empty secrets.

## Railway

The repo includes a `Dockerfile` and `railway.json`. Railway should build the Dockerfile with no custom start command. The process listens on `$PORT` and serves `/health`.

1. New project from this GitHub repo, branch `main`.
2. Add the Postgres plugin. Railway injects `DATABASE_URL`.
3. Set `APP_PASSWORD` and `SESSION_SECRET`. Leave `LIVE_TRADING_ENABLED` unset.
4. Deploy. Tables are created on startup. The three strategies, `SmallBioGap`, and the biotech pipeline are inserted when the strategy table is empty.

Cron is a separate Railway service using the same repo and the same variables, with a start command and a UTC schedule. Railway cron does not know US holidays or daylight-saving time. These UTC times match Eastern Daylight Time (mid-March through early November). In winter, add one hour.

| Job | Command | EDT meaning | UTC cron |
|---|---|---|---|
| Pre-market screens and pipelines | `python -m app.jobs premarket` | 08:00 ET | `0 12 * * 1-5` |
| Regime snapshot | `python -m app.jobs regime` | 09:25 ET | `25 13 * * 1-5` |
| End-of-day snapshot | `python -m app.jobs eod` | 16:30 ET | `30 20 * * 1-5` |
| Nightly backtests | `python -m app.jobs nightly` | 01:00 ET | `0 5 * * 1-5` |

`python -m app.jobs --help` prints the command list. Each command runs once and exits.

## Tests

```bash
pytest
```

The suite does not call Yahoo. It uses a deterministic price fixture. Covered cases include the one-bar fill lag, a 5 bp cost on a hand-built series, a delayed signal that must not improve that series, a future-bar signal that the lookahead check rejects, NaN and inf rejection, login, a closed desk, the screener-to-paper-order pipeline, and the live-order lock.

## Known gaps

- One web process. Backtests run inside the request. There is no Redis queue and no separate worker.
- The WebSocket at `/ws` sends the current mood to a logged-in browser and then stops. It is not a live quote stream.
- Gap-and-go in the original spec buys a break of the first 5-minute high and is out by 15:55. The seeded strategy is the daily analogue, because the default feed is daily.
- Float and news headlines are empty. The screener universe is a static watchlist, not every US small cap. Names with no bars (SAVA is one Yahoo no longer lists) are skipped. The planted SAVA gap exists in the test fixture so the screen can be demoed without a network.
- Sentiment is a VIX scale, not the CNN Fear & Greed index. Breadth is ten ETFs, not NYSE advance/decline.
- Live mode can be flagged when the env gate, keys, confirm phrase, backtest, and paper-day rule all pass. No order is sent to a live broker. `RobinhoodBroker` and `WebullBroker` in `app/execution/stubs.py` raise if called. Their retail APIs were not verified.
- Databento and CCXT adapters import their libraries only when selected. ThetaData talks to a local terminal. None of those paths are exercised by the test suite.
- Passkeys and an IP allowlist are not in this version. The login lock is per process, not shared across replicas.
- Startup migration creates tables and adds any missing columns. It is not an Alembic history.
- Vendor prices are whoever is listed today. A screen of current tickers has survivorship bias, and split adjustments can embed later factors. The backtest page says so.
- yfinance is unofficial and delayed. It is the research default, not a live trading feed.
- The local paper simulator fills at the last close and charges 5 bps. That is not the next-open fill the backtest uses. Alpaca paper fills are whatever the paper API reports; this app does not poll them.
- Trade-list P&L is share-based. The equity curve compounds bar returns. They can differ by a small amount.
