# findings — 代码审查发现

> 2026-07-28 | 五轴代码审查

---

## 发现 1: `run.py:202` — settings 未导入 (Critical)

**类型**: Correctness / NameError
**影响**: ROE 覆盖率检查每次都静默失败，NameError 被 try/except 吞掉

**根因**: `settings.DB_PATH` 在第202行使用，但 `from config import settings` 在第293行才导入。中间没有任何导入 settings 的语句。

**修复方案**: 在 `main()` 函数开头（约第183行）加入 `from config import settings`

**受影响的代码**:
```python
# run.py:201-208
try:
    import sqlite3 as _sq
    _c = _sq.connect(settings.DB_PATH)  # ❌ NameError
```

---

## 发现 2: `scripts/data_check.py:216-219` — minr/maxr 未定义 (Critical)

**类型**: Correctness / NameError
**影响**: `check_financial_roe()` 运行时必然崩溃

**根因**: 最近重构将 `SELECT MIN(roe), MAX(roe)` 查询替换为达标季度查找 (`HAVING COUNT >= 255`)，但删除了 minr/maxr 赋值，保留了引用。

**修复方案**: 在达标季度查询后重新执行 ROE 范围查询

**受影响的代码**:
```python
# data_check.py:216-219
if minr is not None and minr < t['roe_min_val']:  # ❌ NameError
    warn(...)
```

---

## 发现 3: ROE 阈值 255 两处硬编码 (Important)

**类型**: Architecture / 重复定义
**影响**: 修改一处忘另一处 → 行为不一致

**出现位置**:
- `run.py:206,208`: `HAVING COUNT(DISTINCT code)>=255`, `if _rc < 255`
- `data_check.py:42`: `'roe_min_stocks': 255`

**修复方案**: `settings.py` 加 `ROE_MIN_STOCKS = 255`，两处引用

---

## 发现 4: 策略异常静默吞掉 (Important)

**类型**: Correctness / 可观测性
**影响**: 非 verbose 模式下策略异常完全不可见

**位置**: `engine/runner.py:84-86`

**修复方案**: `except` 块无条件 `log.warning()`，verbose 模式额外 print
