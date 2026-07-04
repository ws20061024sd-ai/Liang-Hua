# Claude Code Skills 完全指南

> 通俗理解：Skills 是给 Claude 安装的"专业能力包"。遇到特定任务时自动按最佳实践执行，不需要你一步步指挥。
>
> 本文涵盖你环境中**全部 50+ 个 Skill**——逐一定义、用法、量化交易相关度评分。

---

## 目录

1. [怎么使用 Skill](#1-怎么使用-skill)
2. [你的 Skills 全景图](#2-你的-skills-全景图)
3. [系统内置命令（10个）](#3-系统内置命令)
4. [你导入的 agent-skills（24个）](#4-agent-skills24个)
5. [/last30days —— 搜社交媒体](#5-last30days--搜社交媒体)
6. [pm-skills（9个产品经理 Skill）](#6-pm-skills9个)
7. [前端设计 Skill（14个）](#7-前端设计-skill14个)
8. [output-skill —— 防截断](#8-output-skill--防截断)
9. [相关性速查表](#9-相关性速查表)

---

## 1. 怎么使用 Skill

### 方式一：斜杠命令

```
/code-review         /deep-research        /run
/verify              /security-review      /simplify
/loop                /last30days
```

### 方式二：自然语言（Claude 自动识别）

```
"帮我审查一下代码"         → 自动用 code-review
"帮我调研A股动量因子"      → 自动用 deep-research  
"帮我排查这个报错"         → 自动用 debugging-and-error-recovery
"最近散户在讨论什么"       → 自动用 last30days
```

### 评分说明

| 标记 | 含义 |
|:--:|------|
| 🌟🌟🌟 | 量化交易核心——几乎每天用 |
| 🌟🌟 | 经常用到——每周几次 |
| 🌟 | 偶尔用到——阶段性使用 |
| ⬜ | 和量化无关——知道就行 |

---

## 2. 你的 Skills 全景图

```
你的 Claude Code 环境：50+ Skills

/Users/xoln/.claude/skills/
├── 🔧 系统内置命令（10个）    /code-review, /deep-research, /run 等
├── 📦 agent-skills/（24个）   代码审查、测试、调试、部署、安全等
├── 📰 last30days-skill/       搜 Reddit/X/YouTube 最近30天讨论
├── 📋 pm-skills/（9个）       产品经理工具（需求、策略、市场调研等）
├── 🎨 前端设计（14个）        taste-skill, imagegen 等 UI/设计 Skill
└── 📄 output-skill            防止 AI 输出被截断
```

---

## 3. 系统内置命令

### /code-review 🌟🌟🌟

**做什么**：检查代码找 Bug、冗余、性能问题。支持自动修复。

```
用法：/code-review             审查当前改动
      /code-review --fix       审查 + 自动修复

量化场景：写完策略、改完下载逻辑、改完风控规则——任何不确定写得对不对的时候
```

### /deep-research 🌟🌟🌟

**做什么**：多源搜索 → 交叉验证 → 输出有引用报告。搜网页、论文、研报。

```
用法：/deep-research A股动量因子2025年还有效吗？

量化场景：研究新策略因子、对比数据源方案、调研技术选型
```

### /run 🌟🌟🌟

**做什么**：自动运行项目，展示实际效果。

```
用法：/run              Claude 自动判断启动方式
      python run.py     等价于 /run

量化场景：每次改完代码看信号输出
```

### /verify 🌟🌟

**做什么**：改完 Bug 后实际运行，拿着预期行为去验证，告诉你是/否通过。

```
用法：/verify

和 /run 的区别：/run 只是跑起来，/verify 会对比预期结果
量化场景：修了下载Bug → 确认真的能下载到数据
         加了ST过滤 → 确认ST确实被排除了
```

### /security-review 🌟

**做什么**：检查安全漏洞——API Key 是否硬编码、网络请求是否安全、数据库注入风险。

```
用法：/security-review

量化场景：实盘上线前必做
```

### /simplify 🌟🌟

**做什么**：只做代码质量优化——删冗余、提复用、改结构。不找 Bug。

```
用法：/simplify

量化场景：代码写了一段时间越来越臃肿时
         发现多个策略有重复逻辑时
```

### /loop 🌟

**做什么**：定时循环执行命令。

```
用法：/loop 5m /run    每5分钟跑一次

量化场景：调试阶段定时检查数据下载进度
         正式部署建议用服务器 crontab 替代
```

### /init ⬜

**做什么**：分析项目结构，生成 CLAUDE.md 文件，让 Claude 更了解你的项目。

```
用法：/init

量化场景：已做过，不需要再执行
```

### /review ⬜

**做什么**：审查 GitHub Pull Request。

```
量化场景：团队协作时才用，个人项目不需要
```

### /claude-api ⬜

**做什么**：构建使用 Claude API 的应用。

```
量化场景：将来用 AI 做智能选股/新闻情绪分析时可能用到
```

---

## 4. agent-skills（24个）

> 安装位置：`~/.claude/skills/agent-skills/skills/`
> 这是最实用的一组——覆盖软件开发的完整生命周期。

### 代码质量（5个）

#### code-review-and-quality 🌟🌟🌟

**做什么**：从正确性、可读性、架构、安全、性能五个维度审查代码。

```
触发方式："帮我做一次多维度代码审查"
量化场景：策略上线前的最终检查
```

#### code-simplification 🌟🌟

**做什么**：只做优化——删冗余、提复用、改结构。不找 Bug。

```
触发方式："帮我把这段代码简化一下"
量化场景：代码越来越臃肿时
```

#### test-driven-development 🌟

**做什么**：先写测试→写实现→验证。TDD 流程。

```
触发方式："用 TDD 方式实现这个函数"
量化场景：写核心逻辑（因子计算、风控规则）
```

#### debugging-and-error-recovery 🌟🌟🌟

**做什么**：系统排查 Bug 根因，不是瞎猜。一步步缩小范围直到定位。

```
触发方式："帮我排查这个报错" / "为什么数据取不到"
量化场景：遇到任何不懂的报错时——这是最常用的之一
```

#### performance-optimization 🌟

**做什么**：分析性能瓶颈，提出优化方案。

```
触发方式："帮我优化这段回测代码的速度"
量化场景：回测太慢、数据下载太慢
```

---

### 开发流程（5个）

#### spec-driven-development 🌟🌟

**做什么**：写代码前先写规范——输入/输出/边界条件/验收标准。

```
触发方式："帮我为新策略写一份规范"
量化场景：开发新策略时——先定义清楚再写代码
```

#### planning-and-task-breakdown 🌟🌟

**做什么**：把大任务拆成小步骤，标注依赖关系和验收标准。

```
触发方式："帮我规划多因子回测引擎的开发任务"
量化场景：开始一个新功能模块时
```

#### incremental-implementation 🌟🌟

**做什么**：分步实现，每次只改一小块、每次都验证通过。

```
触发方式："分步实现这个功能，每步验证一次"
量化场景：改动涉及多个文件时——避免一次改太多出问题找不到原因
```

#### doubt-driven-development 🌟

**做什么**：每个非平凡决策都要经过新鲜上下文的对抗性审查。

```
触发方式：写核心交易逻辑时自动触发
量化场景：涉及资金安全、风控规则的代码——最高标准审查
```

#### source-driven-development ⬜

**做什么**：基于官方文档实现，不凭记忆写代码。

```
触发方式：对接新 API 或新框架时使用
量化场景：对接新数据源 API 时
```

---

### 部署与运维（3个）

#### shipping-and-launch 🌟

**做什么**：上线前检查清单——监控、灰度、回滚方案。

```
触发方式："帮我做上线前的最终检查"
量化场景：部署到服务器前
```

#### ci-cd-and-automation ⬜

**做什么**：搭建 CI/CD 流水线。

```
量化场景：搭建自动化测试+部署时
```

#### observability-and-instrumentation 🌟

**做什么**：加日志、指标、追踪、告警。

```
触发方式："帮我在关键路径加监控日志"
量化场景：生产环境需要知道策略是否正常运行
```

---

### 项目管理（4个）

#### documentation-and-adrs 🌟🌟

**做什么**：记录架构决策和技术选择——你已有的 `项目复盘与经验整理.md` 就是这个思路。

```
触发方式："帮我记录刚才做的架构决策"
量化场景：做了一个重要技术选择后——防止以后忘了为什么
```

#### git-workflow-and-versioning 🌟

**做什么**：组织 commit 信息、分支策略、冲突解决。

```
触发方式："帮我整理一下这次改动，分几个 commit 提交"
量化场景：改了很多文件不知道怎么 commit 时
```

#### deprecation-and-migration 🌟

**做什么**：管理废弃旧代码、迁移到新方案。

```
触发方式："旧的数据下载方案怎么迁移到新方案"
量化场景：旧三策略→多因子引擎迁移时
```

#### api-and-interface-design ⬜

**做什么**：设计 API 和模块接口。

```
量化场景：设计数据接口、策略接口时
```

---

### 安全与配置（3个）

#### security-and-hardening 🌟

**做什么**：代码安全加固——比 /security-review 更深入。

```
量化场景：实盘前深度安全检查
```

#### context-engineering ⬜

**做什么**：优化 Claude 的上下文配置——调整 CLAUDE.md 等规则文件。

```
量化场景：优化 Claude 对你项目的理解时
```

#### using-agent-skills ⬜

**做什么**：元 Skill——发现和调用其他 Skill。不需要你主动使用，Claude 内部自动调用。

---

### 思考与决策（3个）

#### interview-me 🌟🌟

**做什么**：通过一次次提问，帮你澄清你真正想要什么（而不是你以为你想要什么）。

```
触发方式："interview me" / "帮我理清需求"
量化场景："帮我做一个选股系统"——这种模糊需求最适合先 interview
```

#### idea-refine 🌟

**做什么**：把模糊想法通过发散→收敛思维变成可执行方案。

```
触发方式："refine this idea" / "帮我把这个想法具体化"
量化场景："我想做一个行业轮动策略"——从想法到具体方案
```

#### doubt-driven-development 🌟

（同开发流程中所述——也属于思考和决策 Skill）

---

### 前端专用（2个，可跳过）

| Skill | 做什么 |
|-------|--------|
| frontend-ui-engineering | 构建 UI 组件、布局、状态管理 |
| browser-testing-with-devtools | 在 Chrome 中测试网页（需 Chrome DevTools MCP） |

---

## 5. /last30days —— 搜社交媒体

> 安装位置：`~/.claude/skills/last30days-skill/`
> GitHub Trending #1 项目

### 做什么 🌟🌟

**不是搜网页，是搜人说的话**。搜索 Reddit、X(Twitter)、YouTube、TikTok、Hacker News、Polymarket、GitHub 等平台上，最近 30 天内人们真实讨论什么。结果按点赞/转发/关注度排序。

```
/last30days   ←→   /deep-research
搜"普通人怎么说"     搜"官方/论文/研报怎么说"
```

### 怎么用

```
/last30days 中国散户最近关注哪些A股板块
/last30days 量化交易 选股策略 最新讨论
/last30days Python量化框架 对比
/last30days A股散户情绪 热门话题
```

### 平台支持

| 平台 | 状态 |
|------|:--:|
| Reddit | ✅ 开箱即用 |
| Hacker News | ✅ 开箱即用 |
| Polymarket | ✅ 开箱即用 |
| GitHub | ✅ 开箱即用 |
| X (Twitter) | ⚠️ 需配 API Key |
| YouTube | ⚠️ 需配 API Key |
| TikTok | ⚠️ 需配 API Key |
| Bluesky | ⚠️ 需配 API Key |

> 配置方式：在 `~/.claude/settings.json` 或环境变量中设置 `SCRAPECREATORS_API_KEY`

### 量化场景

| 你想知道 | 搜什么 |
|---------|--------|
| 散户情绪和热点板块 | `/last30days A股 散户 热门板块` |
| 新策略灵感 | `/last30days quantitative trading strategy 2026` |
| 技术栈趋势 | `/last30days Python backtesting framework 推荐` |
| 数据源评价 | `/last30days AKShare vs Tushare 哪个好用` |

---

## 6. pm-skills（9个）

> 安装位置：`~/.claude/skills/pm-skills/`
> 产品经理工具集。大部分和量化无关，少数可用于策略研究。

### pm-market-research 🌟

**做什么**：市场调研——用户画像、市场细分、情绪分析、竞品分析。

```
量化场景：研究不同投资者群体的行为特征
         "A股散户 vs 机构的行为差异"
```

### pm-data-analytics 🌟

**做什么**：数据分析——SQL 查询生成、用户行为分析、留存模式识别。

```
量化场景：分析信号命中率、持仓行为模式
```

### pm-product-discovery 🌟

**做什么**：产品探索——想法验证、假设检验、功能优先级。

```
量化场景：验证一个新策略因子是否值得投入开发
```

### pm-product-strategy ⬜

**做什么**：产品策略——愿景、SWOT、波特五力、定价。

```
量化场景：几乎用不到
```

### pm-execution ⬜

**做什么**：项目管理——PRD、OKR、路线图、Sprint。

```
量化场景：几乎用不到
```

### pm-go-to-market ⬜

**做什么**：产品上市策略。

```
量化场景：几乎用不到
```

### pm-marketing-growth ⬜

**做什么**：营销增长。

```
量化场景：几乎用不到
```

### pm-ai-shipping ⬜

**做什么**：AI 构建的应用发布前的审计和安全检查。

```
量化场景：几乎用不到
```

### pm-toolkit ⬜

**做什么**：PM 工具——简历审查、NDA 草拟、语法检查。

```
量化场景：几乎用不到
```

---

## 7. 前端设计 Skill（14个）

> 全部和量化交易无关。列出来供了解，可以跳过。

### 设计系统类

| Skill | 做什么 |
|-------|--------|
| **taste-skill** | 反"AI 模版味"的前端设计系统——Claude 默认启用 |
| **taste-skill-v1** | 上一代 taste-skill，保持向后兼容 |
| **soft-skill** | 定义高端 agency 风格的字体/间距/阴影/动效 |
| **minimalist-skill** | 极简风格——暖色单色调、排版对比、扁平网格 |
| **brutalist-skill** | 粗野主义——瑞士印刷排版 + 军事终端美学 |
| **gpt-tasteskill** | GSAP 高级动效 + 排版引擎 + 随机化布局 |
| **stitch-skill** | Google Stitch 设计系统——DESIGN.md 生成器 |
| **redesign-skill** | 升级现有网站/App 的设计质量 |
| **brandkit** | 品牌设计系统——Logo、配色、视觉体系 |

### 图片生成类

| Skill | 做什么 |
|-------|--------|
| **imagegen-frontend-web** | 生成网站设计参考图（每个 section 一张） |
| **imagegen-frontend-mobile** | 生成 App 设计参考图（iOS/Android） |
| **image-to-code-skill** | 从设计图生成代码 |

### 输出控制类

| Skill | 做什么 |
|-------|--------|
| **output-skill** | 强制完整输出，禁止省略/占位符/截断 |

---

## 8. output-skill —— 防截断

> 安装位置：`~/.claude/skills/output-skill/`

### 做什么 🌟

强制 Claude 输出完整代码，禁止用 `// ... 省略` 或 `# 同上` 等占位符。当需要生成完整文件时自动激活。

```
触发方式：通常自动激活——当你要求输出完整代码时
量化场景：让 Claude 输出完整的策略文件而非"省略无关代码"
```

---

## 9. 相关性速查表

### 🌟🌟🌟 量化交易核心（每天用）

| Skill | 用什么触发 |
|-------|-----------|
| /code-review | `/code-review` 或 "帮我审查代码" |
| /deep-research | `/deep-research 话题` 或 "帮我调研..." |
| /run | `/run` 或 "帮我跑一下" |
| code-review-and-quality | "帮我做多维度代码审查" |
| debugging-and-error-recovery | "帮我排查这个报错" |

### 🌟🌟 经常用到（每周几次）

| Skill | 用什么触发 |
|-------|-----------|
| /verify | `/verify` 或 "确认修好了没" |
| /simplify | `/simplify` 或 "代码简化一下" |
| /last30days | `/last30days 话题` |
| spec-driven-development | "帮我写规范" |
| planning-and-task-breakdown | "帮我拆任务" |
| incremental-implementation | "分步实现" |
| documentation-and-adrs | "记录这个决策" |
| interview-me | "interview me" / "理清需求" |
| code-simplification | "简化这段代码" |

### 🌟 偶尔用到

| Skill | 什么时候 |
|-------|---------|
| /security-review | 实盘上线前 |
| /loop | 调试时定时检查 |
| test-driven-development | 写核心逻辑 |
| performance-optimization | 回测/下载太慢 |
| shipping-and-launch | 部署到服务器前 |
| observability-and-instrumentation | 加生产监控 |
| deprecation-and-migration | 旧策略迁移 |
| security-and-hardening | 实盘前深度加固 |
| doubt-driven-development | 写核心交易逻辑 |
| idea-refine | 模糊想法具体化 |
| pm-market-research | 市场行为研究 |
| pm-data-analytics | 信号/持仓分析 |
| output-skill | 要完整输出时 |

### ⬜ 和量化无关

`/init`, `/review`, `/claude-api`, `source-driven-development`, `ci-cd-and-automation`, `context-engineering`, `using-agent-skills`, `api-and-interface-design`, `frontend-ui-engineering`, `browser-testing-with-devtools`, 全部 pm-skills（除 market-research 和 data-analytics 外）, 全部 14 个前端设计 Skill

---

> **原则：不需要背命令。** 直接用自然语言说话，Claude 会自动判断该用哪个 Skill。
>
> 记住最核心的 3 个：`/code-review`（审查代码）、`/run`（看效果）、`/deep-research`（调研）。其他说人话就行。

---

*更新：2026-06-18*
*你当前环境：系统内置 10 个 + agent-skills 24 个 + last30days + pm-skills 9 个 + 前端设计 14 个 = 58 个 Skill*
