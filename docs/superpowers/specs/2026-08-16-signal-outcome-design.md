# 信号效果验证系统设计（L1 固定窗口 + L2 等权账本）

> 日期：2026-08-16 · 状态：已获用户认可 · 对应方向：信号先可验证 → 打磨策略 → 优化其他

## 背景与目标

系统已运行约 2 个月（钉钉推信号、用户手动下单），但**信号有效性从未被验证**：持仓是推断的、无真实成交记录、无准确率统计。回测报告（聚宽/本地）检验的是"策略理论收益"，不是用户钉钉里实际收到的信号。

**目标**：把"这套信号到底有没有用"变成每天自动回答的问题，为策略打磨提供数据依据。

**用户已确认的决策**：
- L1（固定窗口预测力检验）+ L2（等权动态账本）都做
- 样本以历史回放为主（复用生产代码，补齐 1 年），真实信号持续累积
- **持续跟踪系统**：回放补齐历史 + 每日 cron 自动结算新信号
- L2 用等权无视资金（检验信号纯度，不受仓位/资金分配干扰）
- 结果展示在仪表盘新增「信号效果」页

## 术语与两种口径

| 口径 | 回答的问题 | 周期 | 成本假设 |
|---|---|---|---|
| L1 固定窗口 | 信号有没有预测力 | 信号后 5/10/20 个交易日（固定） | 无手续费（纯预测力） |
| L2 动态账本 | 按规则完整买卖周期赚不赚钱 | BUY→平仓（SELL/止损/到期，自然周期） | 次日开盘价成交 |

关键区别：L1 的所有信号必须有相同观察窗才能汇总平均；L2 每笔交易持有期由规则决定。

## 架构

```
daily_kline / index_daily（只读行情）
        │
signal_history（只读：真实信号来源）
        │
   scripts/signal_outcome.py（新）
        │  ① 首次：--replay 回放 1 年历史补齐
        │  ② 每日：结算新信号（L1 窗口到期 / L2 平仓触发）
        ▼
   signal_outcome 表（新，结算记录，source='real'/'replay'）
        │
   web/generate.py 新增 outcome.html（「信号效果」页）
```

复用（不改）：strategies/*、engine/runner、engine/risk_filter、engine/market_timing、engine/signal_store。
改动：generate.py 加页面函数；cron 加一条 21:02 结算任务；settings 加参数。

## L1 固定窗口检验规则

1. **观察窗口**：5/10/20 个交易日（settings 参数化：`OUTCOME_WINDOWS = [5, 10, 20]`）
2. **收益**：信号日收盘价（signal_history.price）→ 第 N 个交易日收盘价，`收益 = 第N日收盘/信号日收盘 - 1`。数据来自 daily_kline，无手续费
3. **超额收益**：同期沪深300（index_daily）同窗口收益，`超额 = 个股收益 - 指数收益`
4. **判定方向**：
   - BUY：超额 > 0 = 命中（涨但没跑赢大盘 = 平庸不命中）
   - SELL：超额 < 0 = 命中（反向验证）
5. **汇总**：分策略 × 窗口：条数/胜率（命中比例）/平均超额
6. **边界**：信号后不足 N 交易日 → 已到期窗口结算，未到期留空待续；停牌/退市查不到 → 标记跳过（status='skipped'）

## L2 等权动态账本规则

1. **成交假设**：信号日收盘产生信号 → **次日开盘价成交**为成本（更接近现实可执行价）。一字板买不进等滑点不模拟（标注局限）
2. **持仓跟踪**：每个 BUY 等权 1 份进入模型持仓，记录成本/日期
3. **平仓条件**：
   - 卖出信号：出现 SELL（任意策略，含移动止损）→ 平仓（同股多笔 FIFO 配对）
   - 强制平仓：持有 ≥ `OUTCOME_MAX_HOLD_DAYS=60` 交易日 → 平仓
   - 没有持仓的 SELL **不执行**（现实中也无法卖）——SELL 独立检验由 L1 承担
4. **每笔记录**：买入日/成本/平仓日/平仓价/收益%/持仓天数/平仓原因/策略
5. **汇总**：分策略：笔数/胜率/平均收益/平均持仓天数/等权累计收益；止损统计（触发次数/平均止损幅度——验证止损规则真的在起作用）
6. **不模拟**：资金约束（等权 1 份）、手续费、滑点、停牌无法成交——标注局限

## 回放引擎（--replay）

- 范围：过去 `REPLAY_YEARS=1`（约 250 交易日）；qfq 除权在 1 年内失真小（已知局限）
- 股票：当前沪深300 成分股（历史成分股漂移是已知局限，记录）
- 铁律：**不偷看未来**——t 日信号只用 t 日及之前数据（cleaner 按日滚动天然保证）
- 流程：对每个历史交易日：跑生产 runner 策略（strategies/risk_filter/market_timing 原样 import）→ 虚拟信号进 L2 撮合 → 次日开盘成交 → 逐日推进
- 性能：复用批量加载，300 只 × 250 天预计几十秒~几分钟
- 产物：结算写 signal_outcome，source='replay'
- 真实信号（signal_history 已有记录）结算写 source='real'

## 每日增量结算

```
21:00  run.py（照旧，不动）
21:02  scripts/signal_outcome.py（cron 新增）
       ① 登记今日新真实信号（source='real'）
       ② L1：检查窗口到期信号 → 结算
       ③ L2：检查持仓 → SELL/止损/到期平仓 → 结算
21:20  generate.py（照旧，多一个 outcome.html）
```

**幂等**：结算记录 UNIQUE 键（signal 标识 + 窗口/事件），重复跑不重复写。
**顺延**：第 N 个交易日语义天然规避节假日/缺数日。

## 仪表盘「信号效果」页（outcome.html）

- 来源切换：真实信号 / 回放历史 / 合并
- L1 表：策略 × 窗口胜率 + 平均超额（✅/❓/⚠️ 标记）
- L2 表：策略笔数/胜率/平均收益/平均持仓/等权累计 + 止损统计
- 最近结算列表（最新 10 条）

## 表结构（signal_outcome）

```sql
CREATE TABLE signal_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,              -- 信号日期
    code TEXT, name TEXT,
    strategy TEXT, action TEXT,  -- BUY/SELL（L2 只从 BUY 起）
    source TEXT,            -- 'real' | 'replay'
    kind TEXT,              -- 'l1_5d'|'l1_10d'|'l1_20d'|'l2_close'（结算类型）
    ref_price REAL,         -- 信号日收盘（L1 基准）
    exec_price REAL,        -- L2 成交价（次日开盘）或 NULL
    exit_price REAL,        -- L2 平仓价或 NULL
    pnl REAL,               -- 收益%（L1 窗口收益 / L2 持仓收益）
    excess REAL,            -- 超额收益%（对沪深300）
    hold_days INTEGER,      -- L2 持仓天数或 NULL
    exit_reason TEXT,       -- L2 平仓原因：'sell'|'stop_loss'|'timeout' 或 NULL
    status TEXT DEFAULT 'done',  -- 'done'|'pending'|'skipped'
    -- source 进 UNIQUE：回放日期与真实信号重叠（如 6-09~7-10）时互不冲突
    UNIQUE(date, code, strategy, action, kind, source)
)
```

## 改动文件清单

| 文件 | 动作 |
|---|---|
| `scripts/signal_outcome.py` | 新建：结算引擎（L1/L2 + --replay） |
| `web/generate.py` | 修改：`page_outcome()` + 导航加链接 |
| `config/settings.py` | 修改：OUTCOME_WINDOWS/REPLAY_YEARS/OUTCOME_MAX_HOLD_DAYS 等 |
| `run.py` 或 cron | cron 加 21:02 条目（服务器） |
| `setup.sh` / `CLAUDE.md` | 文档同步（cron 说明） |
| `tests/` | 新增约 15 个测试 |

不改：strategies/*、runner、risk_filter、market_timing、signal_store、downloader、run.py 核心流程。

## 验证与测试要点

- L1：窗口收益计算（5/10/20 交易日 vs 自然日）、超额计算、SELL 反向判定、窗口不足留空、停牌跳过
- L2：次日开盘成交、FIFO 配对、无持仓 SELL 不执行、60 日到期平仓、止损触发
- 幂等：重复结算不重复写
- 回放：不偷看未来（滚动窗口）、样本量输出
- 仪表盘：outcome.html 渲染、空表不崩

## 风险与局限（如实记录）

- qfq 前复权在 1 年回放内失真小但存在
- 当前成分股回看历史存在幸存者偏差（回放局限，与现有回测同源）
- 无滑点/手续费/一字板买不进模拟——L2 收益偏乐观，作为策略对比的相对值使用
- 双均线信号低频，回放后若样本 <30 笔，效果页显示"样本不足"不硬凑结论
- ST 标记缺失不影响验证（结算只看价格）
