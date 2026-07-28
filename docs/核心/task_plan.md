# task_plan — 代码审查修复

> 创建: 2026-07-28 | 来源: 五轴代码审查

---

## 阶段

### 阶段 1: Critical 修复（阻断级）

- [X] **修复1**: `run.py:202` — `settings` 未导入即使用 (NameError)
- [X] **修复2**: `scripts/data_check.py:216-219` — `minr`/`maxr` 未定义 (NameError)

### 阶段 2: Important 修复（重要）

- [X] **修复3**: ROE 覆盖率阈值 255 硬编码 → 统一到 `settings.py`
- [X] **修复4**: `engine/runner.py:84` — 策略异常仅 verbose 模式可见 → 至少 logging.warning

### 阶段 3: 文档更新

- [X] **文档**: 更新 `项目下一步计划.md`，记录本次审查修复

### 阶段 4: 验证

- [X] 运行 `python -m pytest tests/ -v`（26个测试）
- [X] 运行 `python scripts/data_check.py`（无 --block，仅输出）
- [X] 运行 `python run.py --no-update`（模拟信号生成）

---

## 决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | `settings` 在 `main()` 开头统一导入 | 多处需要 settings，不比在中间才导入 |
| D2 | `minr`/`maxr` 校验改为重新查询 ROE 范围 | 保留校验价值，而非删除 |
| D3 | ROE 阈值放入 `settings.py`，命名 `ROE_MIN_STOCKS` | 单一事实来源原则 |
