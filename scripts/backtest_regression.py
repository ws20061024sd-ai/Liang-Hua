#!/usr/bin/env python3
"""
回测回归测试 —— 保存上次结果，新运行自动对比，变化>2pp告警

用法:
  python scripts/backtest_regression.py save     # 保存当前结果作为基线
  python scripts/backtest_regression.py check    # 运行回测并对比基线

集成到 CI/commit hook:
  每次提交前跑一次 check，变化>2pp则提醒确认
"""
import sys
import os
import json
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
BASELINE_FILE = os.path.join(PROJECT_DIR, 'data', '.backtest_baseline.json')
BACKTEST_SCRIPT = os.path.join(PROJECT_DIR, 'backtest', 'local_factor_backtest.py')

THRESHOLD_PP = 2.0  # 超过此值告警


def run_backtest():
    """运行回测，解析输出"""
    env = os.environ.copy()
    env['PYTHONPATH'] = PROJECT_DIR

    result = subprocess.run(
        [sys.executable, BACKTEST_SCRIPT],
        capture_output=True, text=True, timeout=120,
        cwd=PROJECT_DIR, env=env
    )

    metrics = {}
    for line in result.stdout.split('\n'):
        if 'TOP_N=' in line and '年化=' in line:
            parts = line.split('|')
            top_n = int(parts[0].split('=')[1].strip())
            ann_ret = float(parts[1].split('=')[1].replace('%', '').strip())
            sharpe = float(parts[2].split('=')[1].strip())
            max_dd = float(parts[3].split('=')[1].replace('%', '').strip())
            metrics[f'TOP_N={top_n}'] = {
                'ann_ret': round(ann_ret, 2),
                'sharpe': round(sharpe, 3),
                'max_dd': round(max_dd, 1),
            }

    return metrics


def save_baseline():
    """保存当前回测结果作为基线"""
    print("🏃 运行回测...")
    metrics = run_backtest()

    if not metrics:
        print("❌ 无法解析回测输出")
        sys.exit(1)

    baseline = {
        'saved_at': datetime.now().isoformat(),
        'metrics': metrics,
    }

    with open(BASELINE_FILE, 'w') as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 基线已保存到 {BASELINE_FILE}")
    for top_n, m in metrics.items():
        print(f"  {top_n}: 年化={m['ann_ret']}% 夏普={m['sharpe']} 回撤={m['max_dd']}%")


def check_regression():
    """运行回测，对比基线"""
    if not os.path.exists(BASELINE_FILE):
        print("⚠️  无基线文件，先运行: python scripts/backtest_regression.py save")
        sys.exit(1)

    with open(BASELINE_FILE) as f:
        baseline = json.load(f)

    print(f"📋 基线: {baseline['saved_at']}")
    print("🏃 运行回测...")
    current = run_backtest()

    if not current:
        print("❌ 无法解析回测输出")
        sys.exit(1)

    print(f"\n{'='*65}")
    print(f"  {'TOP_N':<10} {'基线':>8} {'当前':>8} {'差异':>8} {'判定'}")
    print(f"  {'-'*45}")

    all_ok = True
    for top_n in sorted(current.keys()):
        base = baseline['metrics'].get(top_n, {})
        curr = current[top_n]

        diff = curr['ann_ret'] - base['ann_ret']
        status = '✅' if abs(diff) <= THRESHOLD_PP else ('⚠️' if abs(diff) <= 5 else '🔴')

        if abs(diff) > THRESHOLD_PP:
            all_ok = False

        print(f"  {top_n:<10} {base['ann_ret']:>7.1f}% {curr['ann_ret']:>7.1f}% {diff:>+7.1f}pp {status}")

    print(f"  {'='*45}")

    if all_ok:
        print(f"\n✅ 回归测试通过 — 所有TOP_N变化 ≤ {THRESHOLD_PP}pp")
        sys.exit(0)
    else:
        print(f"\n🔴 回归测试告警 — 部分TOP_N变化 > {THRESHOLD_PP}pp")
        print("  请确认: 1) 是否故意修改了策略? 2) 数据是否有变化?")
        print("  确认无误后重新保存基线: python scripts/backtest_regression.py save")
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('save', 'check'):
        print("用法: python scripts/backtest_regression.py save|check")
        print("  save  — 保存当前回测结果作为基线")
        print("  check — 运行回测并对比基线，变化>2pp告警")
        sys.exit(1)

    if sys.argv[1] == 'save':
        save_baseline()
    else:
        check_regression()
