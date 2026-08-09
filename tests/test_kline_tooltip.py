"""K线 tooltip 测试"""
import pandas as pd
from web.generate import _render_svg_kline, KLINE_TOOLTIP_JS


def _sample_df():
    return pd.DataFrame({
        'date': [f'2026-08-0{i}' for i in range(1, 7)],
        'open': [1.0, 2.0, 3.0, 2.0, 3.0, 4.0],
        'close': [2.0, 3.0, 2.0, 3.0, 4.0, 3.0],
        'high': [3.0, 4.0, 4.0, 4.0, 5.0, 5.0],
        'low': [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
        'volume': [100, 200, 300, 400, 500, 600],
    })


def test_svg_has_data_attrs():
    svg = _render_svg_kline(_sample_df())
    assert 'data-date=' in svg
    assert 'data-o=' in svg and 'data-c=' in svg
    assert 'data-pct=' in svg and 'data-vol=' in svg


def test_svg_has_hit_area():
    svg = _render_svg_kline(_sample_df())
    assert 'data-kline' in svg  # svg 根标记


def test_tooltip_js_has_core_logic():
    assert 'kline-tip' in KLINE_TOOLTIP_JS
    assert 'touchstart' in KLINE_TOOLTIP_JS  # 手机点击
    assert 'data-date' in KLINE_TOOLTIP_JS
