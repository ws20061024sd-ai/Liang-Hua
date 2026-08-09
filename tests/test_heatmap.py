"""板块热力图渲染测试"""
from web.generate import _render_heatmap


def test_heatmap_renders_blocks():
    data = [{'name': '券商', 'pct': 2.1, 'n': 30}, {'name': '银行', 'pct': -1.2, 'n': 300}]
    html = _render_heatmap(data)
    assert 'hm-wrap' in html
    assert '券商' in html and '银行' in html


def test_heatmap_up_red_down_green():
    data = [{'name': '券商', 'pct': 2.1, 'n': 30}, {'name': '银行', 'pct': -1.2, 'n': 300}]
    html = _render_heatmap(data)
    assert 'var(--up)' in html and 'var(--down)' in html


def test_heatmap_size_by_count():
    data = [{'name': '小板块', 'pct': 0.5, 'n': 10}, {'name': '大板块', 'pct': 0.5, 'n': 300}]
    html = _render_heatmap(data)
    import re
    sizes = re.findall(r'data-sz="(\d+)"', html)
    assert len(sizes) == 2
    assert int(sizes[1]) > int(sizes[0]), "成分股多的板块色块应更大"


def test_heatmap_empty_returns_empty():
    assert _render_heatmap([]) == ''
