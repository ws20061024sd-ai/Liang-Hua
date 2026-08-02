# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**Primary reference**: `docs/核心/项目完整方案.md` — read this first for complete project understanding.

## Project Overview

Semi-automated quantitative trading system for A-shares (沪深300). The system generates daily buy/sell signals and market analysis reports, pushed to DingTalk. **Trading is manual** — the user reviews signals on their phone and executes orders on their brokerage app (中信建投).

**Server**: Tencent Cloud OpenCloudOS 8, deployed at `/root/Liang-Hua/`.  
**Cron**: 21:00 signals → 21:05 report → 21:10 health check → 21:15 backup (weekdays).  
**Trading capital**: ~¥10,000 (small-cap tier: single-stock ≤50%, stop-loss -3%).

## Key Commands

```bash
# Local development (all from project root)
source venv/bin/activate
python run.py              # Full run: download data + generate signals + push DingTalk
python run.py --no-update  # Signals only (skip data download, for testing)
python run.py --init       # First-time: download all years of HS300 data
python scripts/data_check.py           # Data health check (report only)
python scripts/data_check.py --block   # Data health check (fail on block)
python analysis/report.py  # Generate + push daily market report
python -m engine.factor_engine  # Multi-factor scoring (monthly stock ranking)
python web/generate.py        # Generate dashboard static HTML (web/output/)

# Testing
python -m pytest tests/ -v  # Run all tests (26 cases)

# Backtest
python backtest/simple_backtest.py
python backtest/local_factor_backtest.py

# Server (SSH into Tencent Cloud)
ssh root@<server-ip>
cd /root/Liang-Hua && git pull    # Sync latest code
bash setup.sh                      # One-click deploy (first time or after major changes)
crontab -l                          # Check scheduled tasks
cat logs/cron.log | tail -30        # View signal run logs
cat logs/report.log | tail -30      # View report run logs
cat logs/run.log | tail -20         # View structured logs (with timestamps)
cat logs/health.log | tail -10      # View health check logs

# Server health check & backup (manual)
python scripts/health_check.py           # Check if signals were generated today
python scripts/health_check.py --backup  # Also backup database

## Architecture

```
run.py  ───  Main entry: download → fix data → quality check → strategies → filter → push
  │
  ├── config/          settings.py (params) + settings_local.py (tokens, gitignored)
  ├── data_fetcher/    AKShare downloader (Sina primary, Eastmoney backup) → SQLite
  ├── strategies/      Pluggable strategy classes (all inherit BaseStrategy)
  ├── engine/          Strategy runner, risk filters, market timing, signal aggregator, factor engine
  ├── notifier/        DingTalk Markdown push (signals + daily report)
  ├── analysis/        Independent daily report pipeline (macro/sector/stock/industry)
  ├── backtest/        Local backtest using same strategy code as production
  ├── scripts/         Health check + automated DB backup
  └── tests/           26 unit tests covering core logic
```

**Data flow**: `AKShare → downloader.py → SQLite (daily_kline + signal_history + sector_history) → strategies → risk filters → signal_aggregator → DingTalk`

**Three strategies running in parallel** (v2 params from `config/settings.py`):
1. `MaCrossStrategy` — MA20/MA60 golden cross buy, death cross sell
2. `MomentumBreakoutStrategy` — Breakout above 10-day high with 2% buffer
3. `MeanReversionStrategy` — Bollinger Bands (10,2.0) oversold/overbought

**Three-line defense** (execution order matters):
1. 基础风控 (`risk_filter.py`): Filter ST, limit-up/down, suspension, price cap, liquidity — **runs first**
2. 大盘择时 (`market_timing.py`): Market regime (strong/shaky/weak/crash) — v3: 降权不拦截, adjusts strategy weights only
3. 仓位控制 (`risk_filter.py` calculate_position): Position sizing by capital tier

## Adding a New Strategy

1. Create `strategies/new_strat.py`, inherit `BaseStrategy`, implement `calculate()` and `get_signal()`
2. Register in `engine/runner.py` `STRATEGY_REGISTRY`
3. Enable in `config/settings.py` `ENABLED_STRATEGIES`
4. Run backtest before deploying: `python backtest/simple_backtest.py`

## Data Quality System

Five-layer automatic protection runs on every execution:
1. Download with 3 retries (0.3s/0.6s backoff) + second pass for failures
2. `fix_pct_change()` — SQL backfill of NULL pct_change from previous close
3. `verify_data_quality()` — checks: date=today? stocks≥280? pct_change no NULLs? sector_count≥80? extreme values?
4. Report consistency — all components use unified `data_date` from DB
5. Report independent verification — report.py runs its own `fix_pct_change()` + `verify_data_quality()` as fallback

**Health check** — automated via `scripts/health_check.py` (cron at 21:10):
```bash
cd /root/Liang-Hua && ./venv/bin/python scripts/health_check.py
```
Or manual quick check:
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

## Data & Backtest Governance (宪法级)

**`docs/核心/数据与回测正确性保障规范.md`** 是项目的**数据与回测宪法**。任何数据操作、回测工作、策略修改都必须遵守其中的规则。

### 强制规则摘要

- **数据校验必须先于策略运行** — `python scripts/data_check.py --block`，阻断则不推送信号
- **聚宽回测 = 真理，本地回测 = 方向验证** — 上线决策必须经聚宽确认
- **因子公式/权重必须三处同步** — `engine/factors.py` ↔ `backtest/local_factor_backtest.py` ↔ 聚宽策略
- **修改因子/权重后必须**: 本地回测(5个TOP_N) → pytest(26个) → 聚宽验证(如变化>5pp)
- **`except Exception: pass` 禁止** — 必须至少 print 到 stderr
- **禁止在策略/因子文件中硬编码参数** — 必须在 settings.py 或文件头部常量区
- **新数据源必须**: 单只验证 → 10只验证 → 全量 → 覆盖率≥85% → 人工核对 → 文档记录

### 关键校验命令

```bash
python scripts/data_check.py --block   # 数据健康检查（阻断模式）
python -m pytest tests/ -v             # 26个核心逻辑测试
PYTHONPATH=. python backtest/local_factor_backtest.py  # 本地回测（5个TOP_N，40秒）
```

## Critical Rules

- **Never hardcode parameters in strategy files** — all config lives in `config/settings.py`
- **Secrets go in `config/settings_local.py`** (gitignored), never in `settings.py` — webhook tokens, API keys
- **Data sources must have fallbacks** — Sina primary, Eastmoney backup, THS for industries
- **Before deploying to server**: run locally + run tests (`python -m pytest tests/ -v`) + run data check (`python scripts/data_check.py --block`), then `git push` + server `git pull`
- **After deploying**: check `crontab -l` on server ONLY (Mac crontab must remain empty)
- **Server cron MUST include**: run.py (21:00) + report.py (21:05) + health_check.py (21:10) + backup (21:15)
- **Strategy changes require backtest first** — use `backtest/local_factor_backtest.py` for direction, JoinQuant for final confirmation
- **All report components must use the same data date** — pass `data_date` explicitly, never query MAX(date) independently
- **pct_change must never be NULL in production data** — `fix_pct_change()` runs automatically, verify with health check
- **Tokens must be rotated** if ever committed to Git — old tokens are in Git history forever

## Key Documentation Files

| File | Purpose |
|------|------|
| `docs/核心/项目完整方案.md` | **Authoritative reference** — architecture, strategies, defense, deployment |
| `docs/核心/数据与回测正确性保障规范.md` | **宪法级** — 数据标准、回测规则、红线、校验流程 |
| `docs/核心/项目下一步计划.md` | Roadmap, priorities, data source decisions (当前状态主文档) |
| `docs/核心/项目梳理与优化方案.md` | Audit checklist (7-layer), strategy iteration protocol |
| `docs/存档/项目状态与待办.md` | **已归档** (2026-06-17, 内容过时) — 历史状态记录 |
| `docs/存档/项目综合审查报告_2026-07-05.md` | **已归档** — 28 issues found, 26 resolved |
| `docs/存档/优化执行计划_2026-07-05.md` | **已归档** — Execution plan + plain-language summary of all fixes |
| `docs/架构/项目复盘与经验整理.md` | Problems encountered, solutions, deployment checklist |
| `docs/架构/半自动化交易方案.md` | Original design blueprint (historical reference) |
| `docs/架构/服务器部署指南.md` | Server setup guide with exact commands |
| `docs/策略/策略回测报告.md` | Backtest results — 3 strategies × 3 timing modes |
| `docs/策略/回测指标完全解释.md` | Beginner's guide to backtest metrics |
| `docs/策略/002_多因子选股_回测分析报告.md` | 002 strategy detailed analysis |
| `docs/策略/聚宽策略编码须知.md` | JoinQuant API correct usage, common errors |
| `docs/策略/策略与回测代码审查规范.md` | Code review checklist for strategies/backtests |
| `docs/参考/Claude Code Skills 完全指南.md` | All 58+ Skills categorized by relevance |
| `docs/参考/GitHub量化交易开源项目速查.md` | Open-source quant projects on GitHub |
| `docs/数据/数据审查报告_2026-06-09.md` | Data quality risks and 5-layer defense system |

## Known Deployments

- **Server**: Tencent Cloud `VM-0-17-opencloudos`, path `/root/Liang-Hua/`, Python 3.11
- **Cron**: `0 21 * * 1-5` (run.py), `5 21 * * 1-5` (report.py), `10 21 * * 1-5` (health check), `15 21 * * 1-5` (DB backup)
- **Mac local**: crontab MUST be empty (`crontab -r` already done)
- **DingTalk webhook**: configured in `config/settings_local.py` (gitignored, keyword filter: "量化")
- **DB backups**: `data/backups/stocks_YYYYMMDD.db`, auto-retained 7 days
- **Logs**: `logs/run.log` (structured, rotated 10MB×7), `logs/cron.log`, `logs/report.log`, `logs/health.log`
