# 仪表盘增强实施计划：板块热力图 + K线悬停 tooltip

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给量化仪表盘添加板块热力图（CSS 色块，红涨绿跌，大小∝成分股数）和 K 线悬停/点击 tooltip（日期、开高低收、涨跌幅、成交量）。

**Architecture:** 纯静态增强——全部改动集中在 `web/generate.py`（HTML 生成时直接输出色块与 data 属性，注入 ~50 行内联 JS tooltip）。不引入任何外部依赖，保持零 JS 依赖架构。

**Tech Stack:** Python 3 + pandas + SQLite（现有）；HTML/CSS/vanilla JS（生成产物）

## Global Constraints

- 零外部 JS 依赖（不引入 ECharts 等）
- 暗色模式必须适配：颜色一律用 CSS 变量（`--up`/`--down`/`--bg-card-solid`/`--border`/`--text-soft`），禁止硬编码颜色
- A 股配色习惯：涨红跌绿（`--up` 红、`--down` 绿）
- 不修改数据下载器、策略、信号、风控代码——纯展示层
- 色块大小 = 成分股数量（`up_count + down_count`），不新增数据源
- 无 JS 环境下降级安全：SVG 结构不变，tooltip 仅不显示

---

### Task 1: 板块热力图

**Files:**
- Modify: `web/generate.py`（新增 `_sector_heatmap()`、`_render_heatmap()`、CSS；`page_market()` 集成）
- Test: `tests/test_heatmap.py`（新建）

**Interfaces:**
- Consumes: `_q(conn, sql, params)` 辅助函数（generate.py 已有，line 166）；`sector_history` 表（date, name, pct_change, up_count, down_count）
- Produces: `_sector_heatmap(conn) -> list[dict]`（[{name, pct, n}]）；`_render_heatmap(rows) -> str`（HTML 色块）；`page_market()` 输出含 `.hm-wrap` 容器

- [ ] **Step 1: 写失败测试**

创建 `tests/test_heatmap.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `source venv/bin/activate && python -m pytest tests/test_heatmap.py -v`
Expected: FAIL（ImportError: cannot import name '_render_heatmap'）

- [ ] **Step 3: 实现热力图函数与 CSS**

在 `web/generate.py` 的 `_sectors()`（line 348）之后新增：

```python
def _sector_heatmap(conn):
    """当日全板块数据（热力图用）——返回 [{name, pct, n}]"""
    ld = _q(conn, "SELECT MAX(date) FROM sector_history").iloc[0, 0]
    if not ld:
        return []
    df = _q(conn, "SELECT name, pct_change, up_count, down_count FROM sector_history WHERE date=?", (ld,))
    rows = []
    for _, r in df.iterrows():
        pct = float(r['pct_change']) if r['pct_change'] is not None else 0
        n = int((r['up_count'] or 0) + (r['down_count'] or 0))
        rows.append({'name': r['name'], 'pct': round(pct, 1), 'n': max(n, 1)})
    return rows


def _heat_color(pct):
    """涨跌幅 → (CSS颜色变量, 透明度)。A股红涨绿跌，3档色阶"""
    if pct > 0:
        a = 0.35 if pct < 0.5 else (0.6 if pct < 2 else 1.0)
        return 'var(--up)', a
    if pct < 0:
        a = 0.35 if pct > -0.5 else (0.6 if pct > -2 else 1.0)
        return 'var(--down)', a
    return 'var(--border)', 0.5


def _render_heatmap(rows):
    """板块热力图——矩形色块 flex 排列，红涨绿跌，大小∝成分股数"""
    if not rows:
        return ''
    blocks = []
    for r in rows:
        color, op = _heat_color(r['pct'])
        sz = min(max(36, int(28 + r['n'] * 0.3)), 240)  # 成分股数→尺寸，上下限保护
        blocks.append(
            f'<div class="hm-block" data-sz="{sz}" style="width:{sz}px;height:{sz}px;'
            f'background:{color};opacity:{op}" '
            f'title="{r["name"]} · {r["pct"]:+.1f}% · {r["n"]}只成分股">'
            f'<span class="hm-name">{r["name"]}</span>'
            f'<span class="hm-pct">{r["pct"]:+.1f}%</span></div>'
        )
    return f'<div class="hm-wrap">{"".join(blocks)}</div>'
```

在 CSS 字符串中（`_page` 使用的 `CSS` 常量，可在 `.theme-btn` 样式附近追加）新增：

```css
.hm-wrap{display:flex;flex-wrap:wrap;gap:6px}
.hm-block{border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:36px;cursor:default}
.hm-name{font-size:10px;font-weight:600;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.45);max-width:90%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hm-pct{font-size:12px;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.45)}
```

- [ ] **Step 4: 运行确认通过**

Run: `source venv/bin/activate && python -m pytest tests/test_heatmap.py -v`
Expected: 4 passed

- [ ] **Step 5: page_market 集成热力图**

在 `page_market()`（line 726）中，`width_html` 定义之后、`sec_html` 定义之前插入：

```python
    # ── 板块热力图（当日）──
    heat_html = ''
    if conn:
        hm_rows = _sector_heatmap(conn)
        if hm_rows:
            hm_date = _q(conn, "SELECT MAX(date) FROM sector_history").iloc[0, 0]
            heat_html = (f'<div class="panel" style="margin-bottom:12px">'
                         f'<div class="panel-hd"> 板块热力图（{hm_date}）</div>'
                         f'<div class="panel-bd">{_render_heatmap(hm_rows)}</div></div>')
```

并在 `body` 模板中 `{width_html}` 之后加 `{heat_html}`：

```python
    body=f'''
    <div class="hero"><h2> 市场监控</h2>...</div>
    {kline_html}
    {width_html}
    {heat_html}
    {sec_html}'''
```

- [ ] **Step 6: 本地生成验证**

Run: `source venv/bin/activate && PYTHONPATH=. python web/generate.py`
Expected: 生成成功；`grep -c "hm-wrap" web/output/market.html` ≥ 1

- [ ] **Step 7: 跑全量测试确认无回归**

Run: `source venv/bin/activate && python -m pytest tests/ -q`
Expected: 35 passed（31 + 4 新增）

- [ ] **Step 8: Commit**

```bash
git add web/generate.py tests/test_heatmap.py
git commit -m "feat: 仪表盘板块热力图——CSS色块红涨绿跌大小∝成分股数"
```

---

### Task 2: K线悬停/点击 tooltip

**Files:**
- Modify: `web/generate.py`（`_render_svg_kline()` 加 data 属性 + 透明命中区；`_page()` 注入 tooltip JS；CSS 追加 tooltip 样式）
- Test: `tests/test_kline_tooltip.py`（新建）

**Interfaces:**
- Consumes: `_render_svg_kline()` 内部 data 循环（line 443-456，每根 K 线的 `d`、`vols[i]`）
- Produces: SVG 每根 K 线带 `data-date/data-o/data-h/data-l/data-c/data-pct/data-vol` 的透明命中 rect；`KLINE_TOOLTIP_JS` 常量（注入 `_page()` 的 `</body>` 前）；`.kline-tip` CSS

- [ ] **Step 1: 写失败测试**

创建 `tests/test_kline_tooltip.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `source venv/bin/activate && python -m pytest tests/test_kline_tooltip.py -v`
Expected: FAIL（ImportError: cannot import name 'KLINE_TOOLTIP_JS'）

- [ ] **Step 3: K线 SVG 加 data 属性与命中区**

在 `_render_svg_kline()`（line 434）SVG 根元素加标记：

```python
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" style="width:100%;max-width:800px" data-kline="1">']
```

K 线循环（line 443-456）重构——每根 K 线后追加透明命中 rect（覆盖价格区+量区，便于悬停命中）：

```python
    prev_close = None
    for i,(_,d)in enumerate(data.iterrows()):
        x=ml+i*(w-ml-mr)/n
        o,c,h_v,l_v=float(d['open']),float(d['close']),float(d['high']),float(d['low'])
        up=c>=o;color='var(--up)'if up else'var(--down)'
        # 影线
        hy=mt+(pmax-h_v)/pr*kline_h;ly=mt+(pmax-l_v)/pr*kline_h
        parts.append(f'<line x1="{x+bar_w/2:.1f}" y1="{hy:.1f}" x2="{x+bar_w/2:.1f}" y2="{ly:.1f}" stroke="{color}" stroke-width="1"/>')
        # 实体
        bt=mt+(pmax-max(o,c))/pr*kline_h;bh=max(1,abs(c-o)/pr*kline_h)
        parts.append(f'<rect x="{x:.1f}" y="{bt:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}"/>')
        # 成交量柱(半透明)
        vh=(vols[i]/vmax)*vol_h*0.9 if vmax>0 else 0
        vh=max(1,vh)if vh>0 else 0
        parts.append(f'<rect x="{x:.1f}" y="{vol_y0+vol_h-vh:.1f}" width="{bar_w:.1f}" height="{vh:.1f}" fill="{color}" opacity="0.35"/>')
        # ── tooltip 数据 + 透明命中区 ──
        pct = ((c - prev_close) / prev_close * 100) if prev_close else 0.0
        prev_close = c
        d_str = str(d['date'])
        vol = vols[i]
        vol_str = f'{vol/1e8:.1f}亿' if vol >= 1e8 else (f'{vol/1e4:.0f}万' if vol >= 1e4 else f'{vol:.0f}')
        parts.append(
            f'<rect x="{x-1:.1f}" y="{mt:.1f}" width="{bar_w+2:.1f}" height="{kline_h+vol_h:.1f}" '
            f'fill="transparent" data-date="{d_str}" data-o="{o:.2f}" data-h="{h_v:.2f}" '
            f'data-l="{l_v:.2f}" data-c="{c:.2f}" data-pct="{pct:+.2f}" data-vol="{vol_str}"/>'
        )
```

- [ ] **Step 4: tooltip JS 常量 + 注入 _page + CSS**

在 `TOGGLE_JS` 常量（line 145）之后新增：

```python
KLINE_TOOLTIP_JS = '''<script>
(function(){
  var tip=null;
  function fmt(v){return v||'—'}
  function show(ev){
    var t=ev.target;
    var d=t.getAttribute('data-date');if(!d)return;
    var o=t.getAttribute('data-o'),h=t.getAttribute('data-h'),l=t.getAttribute('data-l'),
        c=t.getAttribute('data-c'),p=parseFloat(t.getAttribute('data-pct')),v=t.getAttribute('data-vol');
    var cls=(p>=0?'up':'dn');
    tip.innerHTML='<div class="kt-date">'+d+' <span class="'+cls+'">'+(p>=0?'+':'')+p.toFixed(2)+'%</span></div>'
      +'<div class="kt-row"><span>开</span><b>'+fmt(o)+'</b><span>高</span><b>'+fmt(h)+'</b></div>'
      +'<div class="kt-row"><span>低</span><b>'+fmt(l)+'</b><span>收</span><b>'+fmt(c)+'</b></div>'
      +'<div class="kt-row"><span>量</span><b>'+fmt(v)+'</b></div>';
    tip.style.display='block';
    tip.style.left=(ev.clientX+12)+'px';
    tip.style.top=(ev.clientY-10)+'px';
    if(tip.getBoundingClientRect().right>window.innerWidth){tip.style.left=(ev.clientX-160)+'px';}
  }
  function hide(){if(tip)tip.style.display='none';}
  document.addEventListener('DOMContentLoaded',function(){
    var svgs=document.querySelectorAll('svg[data-kline]');
    if(!svgs.length)return;
    tip=document.createElement('div');tip.className='kline-tip';document.body.appendChild(tip);
    svgs.forEach(function(svg){
      svg.querySelectorAll('rect[data-date]').forEach(function(r){
        r.addEventListener('mousemove',show);
        r.addEventListener('mouseleave',hide);
        r.addEventListener('click',show);
        r.addEventListener('touchstart',function(ev){show(ev);ev.preventDefault();});
      });
    });
  });
})();
</script>'''
```

`_page()`（line 158-159）的 `</body>` 前注入：

```python
def _page(title,active,body):
    return f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>{title} · 量化交易</title>\n{THEME_SCRIPT}\n{FONTS_LINK}\n{CSS}\n</head>\n<body>\n{_nav(active)}\n<main>\n{body}\n</main>\n{TOGGLE_JS}\n{KLINE_TOOLTIP_JS}\n</body>\n</html>'
```

CSS 追加（与热力图 CSS 同处）：

```css
.kline-tip{position:fixed;z-index:999;background:var(--bg-card-solid);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:11px;box-shadow:var(--shadow-md);display:none;pointer-events:none;max-width:190px}
.kt-date{font-weight:600;margin-bottom:4px;display:flex;gap:8px;align-items:center}
.kt-row{display:flex;gap:8px;margin-top:2px}
.kt-row span{color:var(--text-muted)}
.kt-row b{font-weight:600}
```

- [ ] **Step 5: 运行确认通过**

Run: `source venv/bin/activate && python -m pytest tests/test_kline_tooltip.py -v`
Expected: 3 passed

- [ ] **Step 6: 本地生成验证**

Run: `source venv/bin/activate && PYTHONPATH=. python web/generate.py`
Expected: 生成成功；`grep -c "kline-tip" web/output/stock_000001.html` ≥ 1；`grep -c "data-pct" web/output/stock_000001.html` ≥ 1

- [ ] **Step 7: 跑全量测试确认无回归**

Run: `source venv/bin/activate && python -m pytest tests/ -q`
Expected: 38 passed（35 + 3 新增）

- [ ] **Step 8: Commit**

```bash
git add web/generate.py tests/test_kline_tooltip.py
git commit -m "feat: K线悬停/点击tooltip——日期开高低收涨跌幅成交量，暗色适配，手机可点"
```

---

### Task 3: 部署验证

**Files:** 无代码改动

- [ ] **Step 1: git push**

```bash
git push origin main
```

- [ ] **Step 2: 服务器同步并重新生成上传**

在服务器执行：

```bash
cd /root/Liang-Hua && git pull && PYTHONPATH=. ./venv/bin/python web/generate.py && /usr/local/bin/coscmd upload -r web/output/ /works/quant/ --delete -H '{"Cache-Control":"max-age=300"}' && /usr/local/bin/coscmd upload -r web/output/ /quant/ --delete -H '{"Cache-Control":"max-age=300"}'
```

- [ ] **Step 3: 线上验证**

```bash
curl -s https://xolnxoln.cn/works/quant/market.html | grep -c "hm-wrap"   # ≥1
curl -s https://xolnxoln.cn/works/quant/stock_000001.html | grep -c "kline-tip"  # ≥1
```

Expected: 两者都 ≥1

- [ ] **Step 4: 浏览器人工验收**

- market.html：热力图色块颜色正确（红涨绿跌）、大小随成分股数、悬停显示板块名/涨跌幅/只数
- 股票详情页：鼠标悬停 K 线显示完整数据浮层；手机点击同样显示；暗色模式样式正常
- 无 JS 环境（禁用 JS 刷新）页面正常显示
