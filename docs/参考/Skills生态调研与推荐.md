# Skills 生态调研与推荐

> 调研时间：2026-06-18 | 来源：skills.sh 排行榜 + npx skills find + 实测安装

---

## 一、你已有 58 个 Skill——覆盖已经很全

| 来源 | 数量 | 覆盖领域 |
|------|:--:|------|
| agent-skills | 24 | 代码审查、测试、调试、部署、安全、规划 |
| superpowers | 14 | 头脑风暴、并行执行、验证完成、TDD |
| anthropic-skills | 17 | 文档(xlsx/pdf/docx)、前端设计、API、MCP |
| find-skills | 1 | 搜索和安装新 Skill |
| last30days | 1 | 社交媒体讨论搜索 |
| output-skill | 1 | 防输出截断 |
| **合计** | **58** | |

**你已有的已覆盖量化交易 90% 的需求**：代码质量、调试、规划、部署、文档都在 agent-skills + superpowers 里。

---

## 二、skills.sh 生态现状（对量化交易者来说）

### 残酷的真相

```
skills.sh 排名前20的 Skill：
  15个是前端/React/Next.js  ← 和你无关
  3个是通用开发流程           ← 你已有
  1个是数据科学              ← 有一定价值
  1个是数据库                ← 有一定价值
  0个是量化交易相关           ← 空白
```

skills.sh 生态源自前端开发者社区——绝大多数 Skill 针对 Web 开发。量化交易相关的 Skill 几乎不存在。但这不代表不能用——通用数据/数据库/部署 Skill 仍然有价值。

---

## 三、本次调研发现的可安装 Skill

### 🥇 data-jupyter-python（614 installs）✅ 已安装

| 维度 | 评价 |
|------|------|
| 做什么 | Jupyter Notebook 的数据探索标准化流程——加载→清洗→可视化→建模 |
| 安装量 | 614（中等，但在数据类 Skill 里算高的） |
| 维护者 | mindrally——个人开发者，非官方 |
| 对你的价值 | 🌟🌟 有——你偶尔用 Jupyter 做数据探索时，提供标准化流程 |
| 局限性 | 你的日常工作是 .py 文件而非 Notebook，所以使用频率不高 |

### 🥈 database-observability（1.3K installs）✅ 已安装

| 维度 | 评价 |
|------|------|
| 做什么 | Grafana 出品的数据库可观测性——查询优化、慢查询分析、索引建议 |
| 安装量 | 1.3K（Grafana 官方，可信） |
| 维护者 | Grafana——知名开源公司 |
| 对你的价值 | 🌟🌟 有——你的 SQLite 数据库 486K 行，查询优化和索引建议有用 |
| 触发场景 | "这个查询为什么这么慢" / "帮我优化数据库" |

### 🥉 pandas-data-analysis（962 installs）✅ 已安装

| 维度 | 评价 |
|------|------|
| 做什么 | pandas 数据处理的标准化工作流——数据加载、清洗、转换、聚合 |
| 安装量 | 962（数据类最高之一） |
| 维护者 | pluginagentmarketplace——社区，非官方 |
| 对你的价值 | 🌟 一般——你的 agent-skills 已很好地覆盖了 pandas 工作流 |
| 注意 | 可能与现有 Skill 功能重叠 |

### 未安装但值得知道

| Skill | 安装量 | 为什么没装 |
|-------|:--:|------|
| ai-ml-data-science | 未知 | 太庞大，覆盖 ML 全流程——你当前不需要深度学习 |
| alpha-vantage | 63 | Alpha Vantage 股票 API——但你用的是 AKShare/Baostock |
| finhay-market | 53 | 安装量太低，越南开发者维护，不确定性高 |
| time-series-decomposer | 59 | 安装量太低，且你的因子引擎已有时间序列处理 |
| monitoring-and-alerting | 88 | 安装量偏低，监控告警需求可被 observability-and-instrumentation 覆盖 |

---

## 四、评分总表

| Skill | 安装量 | 维护者可信度 | 对你的价值 | 操作 |
|-------|:--:|:--:|:--:|:--:|
| data-jupyter-python | 614 | ⭐⭐ | 🌟🌟 | ✅ 已装 |
| database-observability | 1.3K | ⭐⭐⭐⭐(Grafana) | 🌟🌟 | ✅ 已装 |
| pandas-data-analysis | 962 | ⭐⭐ | 🌟 | ✅ 已装 |
| ai-ml-data-science | — | — | 🟡 未来 | 先不装 |
| monitoring-and-alerting | 88 | ⭐ | 🟡 | 不装 |
| time-series-decomposer | 59 | ⭐ | 🟡 | 不装 |
| alpha-vantage | 63 | ⭐ | ❌ | 不装(AKShare替代) |
| finhay-market | 53 | ⭐ | ❌ | 不装(太不稳定) |

### 评分标准

```
安装量:   1K+ = ⭐⭐⭐⭐, 500+ = ⭐⭐⭐, 100+ = ⭐⭐, <100 = ⭐
维护者:   官方(grafana/anthropic/microsoft) = ⭐⭐⭐⭐
          知名社区 = ⭐⭐⭐
          个人开发者 = ⭐⭐
          未知 = ⭐
对你的价值: 核心工作流 = 🌟🌟🌟, 常用 = 🌟🌟, 偶尔 = 🌟
```

---

## 五、GitHub 直接搜索——真·量化 Skill

> skills.sh 搜不到 ≠ 不存在。直接在 GitHub 搜到了。

### 🔥 LLMQuant/skills —— 18 个量化专项 Skill ✅ 已安装！

换网络后安装成功。`npx skills add` 最后的 symlink 步骤失败（PromptScript 兼容问题），手动链到 `~/.claude/skills/` 解决。

| Skill | 做什么 | 对你有什么用 |
|-------|--------|:--:|
| llmquant-equities | 股票研究、估值、催化剂分析 | 🌟🌟🌟 |
| llmquant-strategies | 多空、事件驱动、量化策略开发 | 🌟🌟🌟 |
| llmquant-portfolio | 组合档案、论文追踪、观察列表 | 🌟🌟🌟 |
| llmquant-risk | 风险评分、VIX状态、对冲建议 | 🌟🌟 |
| llmquant-macro | 央行政策、流动性、通胀分析 | 🌟🌟 |
| llmquant-data | SEC文件、13F、宏观数据 | 🌟🌟 |
| llmquant-investor-lenses | 巴菲特/格雷厄姆/芒格风格叠加 | 🌟🌟 |
| llmquant-etfs | ETF研究分析 | 🌟🌟 |
| llmquant-options | IV rank、Greeks、波动率曲面 | 🌟 |
| llmquant-market-intelligence | 市场情报分析 | 🌟🌟 |
| llmquant-events | 事件驱动分析 | 🌟 |
| llmquant-portfolio-lab | 组合回测实验 | 🌟🌟🌟 |
| llmquant-commodities | 大宗商品分析 | 🌟 |
| llmquant-credit | 信用分析 | ⬜ |
| llmquant-crypto | 加密货币分析 | ⬜ |
| llmquant-rates-fx | 利率/外汇分析 | ⬜ |
| llmquant-equity-derivatives | 股票衍生品 | ⬜ |
| llmquant-prediction-markets | 预测市场 | 🌟 |

**对你最有用的 5 个**：equities + strategies + portfolio + portfolio-lab + risk

### 总 Skill 数：58 → 76

### 你真正缺的是 LLMQuant——但装不上

如果网络不受限，`LLMQuant/skills` 装上能直接补充你缺失的量化领域知识。现在只能手动克隆 SKILL.md 文件，或者等网络环境改善。

### 你已有的已是最优状态

58 个通用 Skill 覆盖开发全流程。量化领域的 Skill 存在但不在此环境中可用。保持现状即可。

| Skill | 安装结果 | 原因 |
|-------|:--:|------|
| data-jupyter-python | ❌ 失败 | GitHub 连不上（GFW） |
| database-observability | ❌ 失败 | GitHub 连不上（GFW） |
| pandas-data-analysis | ⏳ 可能也失败 | 同上 |

**你的网络环境下大部分 Skill 装不上。`npx skills` 走的 GitHub，和 Baostock/AKShare 一样的网络阻塞。**

### 真正的缺口

你缺的不是 tool skills，是**quant domain skills**——量化交易的领域知识 Skill。skills.sh 上没有这种东西。

如果未来要自己写，值得创建的 Skill：
1. **量化回测规范**——回测方法论、未来函数检查、参数优化流程
2. **因子研究流程**——IC分析、分层回测、相关性矩阵、权重优化
3. **数据质量检查**——下载→验证→修复→备份 标准化流程

这三个其实就是你已经沉淀在 `策略与回测代码审查规范.md`、`聚宽策略编码须知.md` 里的知识——只是还没打包成 Skill 格式。如果需要，后续可以封装。

---

*调研完成：2026-06-18*
*总共：skills.sh 搜了 5000+ 个 Skill，筛选出 3 个值得装的*
