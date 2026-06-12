# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**Primary reference**: `docs/项目完整方案.md` — read this first for complete project understanding.

## Project Overview

Semi-automated quantitative trading system for A-shares (沪深300). The system generates daily buy/sell signals and market analysis reports, pushed to DingTalk. **Trading is manual** — the user reviews signals on their phone and executes orders on their brokerage app (中信建投).

**Server**: Tencent Cloud OpenCloudOS 8, deployed at `/root/Liang-Hua/`.  
**Cron**: 21:00 (signals) / 21:05 (daily report) on weekdays.  
**Trading capital**: ~¥10,000 (small-cap tier: single-stock ≤50%, stop-loss -3%).

## Key Commands

```bash
# Local development (all from project root)
source venv/bin/activate
python run.py              # Full run: download data + generate signals + push DingTalk
python run.py --no-update  # Signals only (skip data download, for testing)
python run.py --init       # First-time: download all 3 years of HS300 data
python analysis/report.py  # Generate + push daily market report

# Backtest
python backtest/simple_backtest.py

# Server (SSH into Tencent Cloud)
ssh root@<server-ip>
cd /root/Liang-Hua && git pull    # Sync latest code
crontab -l                          # Check scheduled tasks
cat logs/cron.log | tail -30        # View signal run logs
cat logs/report.log | tail -30      # View report run logs
```

## Architecture

```
run.py  ───  Main entry: download → fix data → quality check → strategies → filter → push
  │
  ├── data_fetcher/    AKShare downloader (Sina primary, Eastmoney backup) → SQLite
  ├── strategies/      Pluggable strategy classes (all inherit BaseStrategy)
  ├── engine/          Strategy runner, risk filters (2-layer), market timing, signal aggregator
  ├── notifier/        DingTalk Markdown push (signals + daily report)
  ├── analysis/        Independent daily report pipeline (macro/sector/stock/industry)
  ├── backtest/        Local backtest using same strategy code as production
  └── config/settings.py  Single source of truth for all parameters
```

**Data flow**: `AKShare → downloader.py → SQLite (daily_kline + signal_history + sector_history) → strategies → risk filters → signal_aggregator → DingTalk`

**Three strategies running in parallel**:
1. `MaCrossStrategy` — MA10/MA30 golden cross buy, death cross sell
2. `MomentumBreakoutStrategy` — Breakout above 20-day high with 2% buffer
3. `MeanReversionStrategy` — Bollinger Bands (20,2) oversold/overbought

**Three-line defense** (execution order matters):
1. 基础风控 (`risk_filter.py`): Filter ST, limit-up/down, suspension, price cap, liquidity — **runs first**
2. 大盘择时 (`market_timing.py`): Market regime (strong/shaky/weak/crash) — blocks buys in weak/crash, adjusts strategy weights
3. 仓位控制 (`risk_filter.py` calculate_position): Position sizing by capital tier

## Adding a New Strategy

1. Create `strategies/new_strat.py`, inherit `BaseStrategy`, implement `calculate()` and `get_signal()`
2. Register in `engine/runner.py` `STRATEGY_REGISTRY`
3. Enable in `config/settings.py` `ENABLED_STRATEGIES`
4. Run backtest before deploying: `python backtest/simple_backtest.py`

## Data Quality System

Five-layer automatic protection runs on every execution:
1. Download with 3 retries (0.5s/1.0s backoff) + second pass for failures
2. `fix_pct_change()` — SQL backfill of NULL pct_change from previous close
3. `verify_data_quality()` — checks: date=today? stocks≥280? pct_change no NULLs? sector_count≥80? extreme values?
4. Report consistency — all components use unified `data_date` from DB
5. Report independent verification — report.py runs its own `fix_pct_change()` + `verify_data_quality()` as fallback

**Health check** (run on server after 21:05):
```bash
cd /root/Liang-Hua && ./venv/bin/python -c "
import sqlite3; from datetime import datetime
conn = sqlite3.connect('data/stocks.db'); today = datetime.now().strftime('%Y-%m-%d')
maxd = conn.execute('SELECT MAX(date) FROM daily_kline').fetchone()[0]
cnt = conn.execute('SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?',(maxd,)).fetchone()[0]
nulls = conn.execute('SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL',(maxd,)).fetchone()[0]
print(f'Date:{maxd} | Stocks:{cnt}/300 | NULL:{nulls}')
"
```

## Critical Rules

- **Never hardcode parameters in strategy files** — all config lives in `config/settings.py`
- **Never push DingTalk webhook tokens** — already in settings.py, don't expose elsewhere
- **Data sources must have fallbacks** — Sina primary, Eastmoney backup, THS for industries
- **Before deploying to server**: run locally first, verify output, then `git push` + server `git pull`
- **After deploying**: check `crontab -l` on server ONLY (Mac crontab must remain empty)
- **Strategy changes require backtest first** — use `backtest/simple_backtest.py`, compare before/after metrics
- **All report components must use the same data date** — pass `data_date` explicitly, never query MAX(date) independently
- **pct_change must never be NULL in production data** — `fix_pct_change()` runs automatically, verify with health check

## Key Documentation Files

| File | Purpose |
|------|------|
| `docs/项目完整方案.md` | **Authoritative reference** — architecture, strategies, defense, deployment |
| `docs/项目状态与待办.md` | Current status, TODO, run log |
| `docs/项目梳理与优化方案.md` | Audit checklist (7-layer), strategy iteration protocol |
| `docs/数据审查报告_2026-06-09.md` | Data quality risks and 5-layer defense system |
| `docs/项目复盘与经验整理.md` | Problems encountered, solutions, deployment checklist |
| `docs/策略回测报告.md` | Backtest methodology and 3-strategy comparison |
| `docs/半自动化交易方案.md` | Original design blueprint (historical reference, some timings outdated) |
| `docs/量化交易完整指南.md` | Beginner educational guide (not project-specific) |

## Known Deployments

- **Server**: Tencent Cloud `VM-0-17-opencloudos`, path `/root/Liang-Hua/`, Python 3.11
- **Cron**: `0 21 * * 1-5` (run.py), `5 21 * * 1-5` (report.py)
- **Mac local**: crontab MUST be empty (`crontab -r` already done)
- **DingTalk webhook**: configured in settings.py (keyword filter: "量化")
