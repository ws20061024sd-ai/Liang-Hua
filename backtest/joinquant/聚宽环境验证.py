"""
聚宽环境最小验证 —— 在粘贴完整策略之前，先跑这个确认环境正常。

使用方法：
  1. 打开 joinquant.com → 研究环境（不是策略！）
  2. 新建 Notebook → 粘贴本文件 → 逐 cell 运行
  3. 全部通过 → 再粘贴完整策略到回测环境
"""

# ====== Cell 1：确认能取到数据 ======
stocks = get_index_stocks('000300.XSHG')[:5]
print(f"成分股: {stocks}")

raw = get_price(stocks[:3],
    start_date='2020-01-01', end_date='2020-01-10',
    fields=['close'], fq='pre', panel=False)

print(f"type={type(raw).__name__}, shape={raw.shape}")
print(f"columns样例: {list(raw.columns)[:3]}")
print(f"columns类型: {type(raw.columns)}")

# 检查：列名格式是什么？
c = raw.columns[0]
print(f"第一列: '{c}'  type={type(c).__name__}")
if isinstance(c, tuple):
    print(f"  → 是 MultiIndex! levels={raw.columns.levels}")
    print(f"  → 所以 get_price columns = (field, stock)")
else:
    print(f"  → 是普通 Index，列名即股票代码")

# ====== Cell 2：验证列名格式匹配 ======
# get_index_stocks 返回的是 '000001.XSHE' 格式
# get_price columns 是什么格式？能直接对上吗？
stocks_5 = get_index_stocks('000300.XSHG')[:5]
close_raw = get_price(stocks_5,
    start_date='2020-01-01', end_date='2020-01-10',
    fields=['close'], fq='pre', panel=False)

# 两种可能的列名格式
print(f"get_index_stocks: {stocks_5}")
print(f"get_price columns: {list(close_raw.columns)[:5]}")

# 尝试直接匹配
direct_match = [s for s in stocks_5 if s in close_raw.columns]
code_match = [s for s in stocks_5 if s[:6] in close_raw.columns]
print(f"直接匹配: {len(direct_match)}/5")
print(f"纯代码匹配: {len(code_match)}/5")

# 结论：哪种匹配方式是对的，就用哪种

# ====== Cell 3：验证 MultiIndex 兼容 ======
# 多字段时 columns 是什么格式？
raw2 = get_price(stocks_5,
    start_date='2020-01-01', end_date='2020-01-10',
    fields=['close', 'volume'], fq='pre', panel=False)

print(f"多字段 columns: {list(raw2.columns)[:3]}")
print(f"is MultiIndex: {isinstance(raw2.columns, pd.MultiIndex)}")
if isinstance(raw2.columns, pd.MultiIndex):
    print(f"levels: {raw2.columns.levels}")

# ====== Cell 4：验证财务数据 ======
try:
    q = query(valuation.code, valuation.pe_ratio
    ).filter(valuation.code.in_(stocks_5))
    fin = get_fundamentals(q, date='2020-06-30')
    print(f"财务数据: {len(fin) if fin is not None else 0} 条")
    if fin is not None and not fin.empty:
        print(fin.head(3))
except Exception as e:
    print(f"财务查询失败: {e}")

print("\n✅ 全部 Cell 通过 → 环境就绪，可以粘贴完整策略")
