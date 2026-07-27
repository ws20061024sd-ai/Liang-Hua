#!/usr/bin/env python3
"""
自动数据库迁移——每次启动时检查表结构，缺列自动补。
嵌入在 run.py 启动流程中，零手工干预。
"""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# 期望的表结构定义
EXPECTED_SCHEMA = {
    'index_daily': [
        ('date', 'TEXT PRIMARY KEY'),
        ('open', 'REAL'), ('close', 'REAL'), ('high', 'REAL'), ('low', 'REAL'),
        ('volume', 'REAL'), ('amount', 'REAL'),
    ],
    'financial_roe': [
        ('code', 'TEXT'), ('date', 'TEXT'), ('roe', 'REAL'),
        ('PRIMARY KEY', '(code, date)'),
    ],
    'financial_data': [
        ('code', 'TEXT'), ('date', 'TEXT'), ('close', 'REAL'),
        ('pe', 'REAL'), ('pb', 'REAL'), ('ps', 'REAL'), ('pcf', 'REAL'),
        ('roe', 'REAL'), ('roa', 'REAL'),
        ('gross_margin', 'REAL'), ('net_margin', 'REAL'),
        ('revenue_yoy', 'REAL'), ('profit_yoy', 'REAL'),
        ('market_cap', 'REAL'), ('circ_mv', 'REAL'), ('total_assets', 'REAL'),
        ('PRIMARY KEY', '(code, date)'),
    ],
}


def ensure_schema():
    """检查并自动补全表结构"""
    conn = sqlite3.connect(settings.DB_PATH)

    for table_name, expected_cols in EXPECTED_SCHEMA.items():
        # 获取实际列
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}

        for col_name, col_type in expected_cols:
            if col_name == 'PRIMARY KEY':
                continue  # SQLite 不支持 ALTER TABLE ADD PRIMARY KEY
            if col_name not in actual:
                try:
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                    print(f"  ✅ 迁移: {table_name} +{col_name} {col_type}")
                except Exception as e:
                    print(f"  ⚠️ 迁移跳过 {table_name}.{col_name}: {e}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    ensure_schema()
    print("✅ 数据库迁移完成")
