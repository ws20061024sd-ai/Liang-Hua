#!/usr/bin/env python3
"""
仪表盘生成器 v5 —— 博客设计系统 + SVG K线 + 信号首页
用法: PYTHONPATH=. python scripts/generate_dashboard_data.py
"""
import sqlite3, os, sys
from datetime import datetime, timedelta
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = 'data/stocks.db'

# ═══════════════ CSS（博客设计系统） ═══════════════

CSS = '''<style>
:root {
  --bg: #fafaf8; --bg-card: #ffffff; --bg-hover: #f5f5f2;
  --text: #1a1a1a; --text-muted: #6b6b6b; --text-soft: #94948c;
  --border: #e8e8e4; --border-light: #f0f0ec;
  --accent: #2563eb; --accent-soft: #eff6ff;
  --accent-2: #7c3aed; --accent-3: #059669;
  --up: #059669; --down: #dc2626; --warn: #d97706;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
  --shadow: 0 1px 3px rgba(0,0,0,0.06),0 1px 2px rgba(0,0,0,0.04);
}
[data-theme="dark"] {
  --bg: #111110; --bg-card: #1a1a19; --bg-hover: #22221e;
  --text: #e4e4e0; --text-muted: #8b8b85; --text-soft: #6b6b65;
  --border: #2a2a25; --border-light: #22221e;
  --accent: #60a5fa; --accent-soft: #1e2a3a;
  --accent-2: #a78bfa; --accent-3: #34d399;
  --up: #34d399; --down: #f87171; --warn: #fbbf24;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
  --shadow: 0 1px 3px rgba(0,0,0,0.3),0 1px 2px rgba(0,0,0,0.2);
}
*,::before,::after{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
nav{background:var(--bg);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:50;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
nav .inner{max-width:768px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:10px 16px}
nav .brand{font-weight:600;font-size:15px;letter-spacing:-.3px;color:var(--text);text-decoration:none}
nav .links{display:flex;align-items:center;gap:2px}
nav .links a{color:var(--text-muted);text-decoration:none;padding:6px 10px;font-size:13px;border-radius:6px;transition:all .15s}
nav .links a:hover{color:var(--text);background:var(--bg-hover)}
nav .links a.active{color:var(--accent);background:var(--accent-soft);font-weight:500}
main{max-width:768px;margin:0 auto;padding:24px 16px 48px}
.panel{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-sm);margin-bottom:12px;overflow:hidden}
.panel-hd{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-bottom:1px solid var(--border-light);font-size:11px;color:var(--text-soft);text-transform:uppercase;letter-spacing:.4px;font-weight:500}
.panel-bd{padding:12px 16px}
.up{color:var(--up)}.dn{color:var(--down)}.ac{color:var(--accent)}.dim{color:var(--text-soft)}.muted{color:var(--text-muted)}.fw{font-weight:600}
.code{font-family:'SF Mono','Cascadia Code','JetBrains Mono',monospace;font-weight:600;font-size:13px}
.ta-r{text-align:right}.ta-c{text-align:center}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--text-soft);font-weight:500;padding:5px 8px;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:.3px}
td{padding:5px 8px;border-bottom:1px solid var(--border-light)}
tr:hover td{background:var(--bg-hover)}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600}
.t-buy{background:#05966915;color:var(--up)}.t-sell{background:#dc262615;color:var(--down)}
.t-pass{background:#2563eb12;color:var(--accent)}.t-block{background:#6b6b6b12;color:var(--text-muted)}
.t-trend{background:#2563eb10;color:var(--accent)}.t-rev{background:#7c3aed10;color:var(--accent-2)}
.t-warn{background:#d9770615;color:var(--warn)}
.banner{padding:9px 14px;border-radius:8px;margin-bottom:12px;font-size:12px}
.banner-ok{background:#05966908;border:1px solid #05966918;color:var(--accent-3)}
.banner-warn{background:#d9770608;border:1px solid #d9770618;color:var(--warn)}
.grid{display:grid;gap:12px}
.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr 1fr 1fr}
.hero{padding:16px 0 12px}
.hero h2{font-size:16px;font-weight:600;margin-bottom:2px;letter-spacing:-.3px}
.hero p{color:var(--text-muted);font-size:13px}
.sig-row{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border-light);font-size:13px}
.sig-row:hover{background:var(--bg-hover)}
.sig-rank{font-size:15px;font-weight:700;color:var(--text-soft);min-width:24px}
.sig-meta{flex:1;min-width:0}
.sig-meta .reason{font-size:11px;color:var(--text-muted);margin-top:1px}
.empty{color:var(--text-soft);text-align:center;padding:32px 16px;font-size:13px}
.kv{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border-light);font-size:13px}
.kv:last-child{border-bottom:none}
.stock-header{display:flex;gap:20px;align-items:flex-start;margin-bottom:12px}
.stock-header .price{font-size:24px;font-weight:700}
.stock-header .chg{font-size:13px;margin-left:4px}
.stock-header .info td{padding:2px 8px 2px 0;border:none;font-size:12px}
.chart-box{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:4px;margin-bottom:12px;overflow-x:auto}
.card-hover{transition:all .15s}
.card-hover:hover{border-color:var(--accent);box-shadow:var(--shadow)}
@media(max-width:640px){.g2,.g3{grid-template-columns:1fr}nav .links{flex-wrap:wrap}}
</style>'''

# ═══════════════ SHARED ═══════════════

def _nav(active=''):
    links = [('index.html','信号'),('strategy.html','策略'),
             ('market.html','市场'),('factors.html','因子'),('signals.html','日志')]
    items = ''.join(f'<a href="{h}"{" class=active" if h==active else ""}>{n}</a>' for h,n in links)
    return f'<nav><div class="inner"><a href="index.html" class="brand">量化交易</a><div class="links">{items}</div></div></nav>'

def _page(title,active,body):
    return f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>{title} · 量化交易</title>\n{CSS}\n</head>\n<body>\n{_nav(active)}\n<main>\n{body}\n</main>\n</body>\n</html>'

def _tag(c,t): return f'<span class="tag {c}">{t}</span>'
def _ud(v): return f'<span class="{"up" if v>=0 else "dn"}">{v:+.1f}</span>' if v!=0 else '<span class="dim">0.0</span>'

# ═══════════════ DATA ═══════════════

def _q(c,s,p=None): return pd.read_sql_query(s,c,params=p) if p else pd.read_sql_query(s,c)

def _market(conn):
    ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
    sc=int(_q(conn,"SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?",(ld,)).iloc[0,0])
    idx=_q(conn,"SELECT date,AVG(close) as c FROM daily_kline WHERE date>=date('now','-180 days') GROUP BY date ORDER BY date")
    last=float(idx['c'].iloc[-1]) if len(idx) else 0
    r5=round((idx['c'].iloc[-1]/idx['c'].iloc[-5]-1)*100,2) if len(idx)>=5 else 0
    r20=round((idx['c'].iloc[-1]/idx['c'].iloc[-20]-1)*100,2) if len(idx)>=20 else 0
    if len(idx)>=60:
        m20=idx['c'].rolling(20).mean();m60=idx['c'].rolling(60).mean()
        reg='strong' if m20.iloc[-1]>m60.iloc[-1] else 'weak'
    else: reg='unknown'
    chg=idx['c'].pct_change().tail(20)
    return {'date':str(ld),'stocks':sc,'regime':reg,'close':last,'r5':r5,'r20':r20,
        'up':int((chg>0).sum()),'down':int((chg<0).sum())}

def _health(conn):
    ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
    dc=int(_q(conn,"SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?",(ld,)).iloc[0,0])
    nl=int(_q(conn,"SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL",(ld,)).iloc[0,0])
    tr=int(_q(conn,"SELECT COUNT(*) FROM daily_kline").iloc[0,0])
    f=_q(conn,"""SELECT COUNT(*) as t,SUM(CASE WHEN pe IS NOT NULL THEN 1 ELSE 0 END)as pe,
        SUM(CASE WHEN pb IS NOT NULL THEN 1 ELSE 0 END)as pb FROM financial_data
        WHERE date=(SELECT MAX(date) FROM financial_data WHERE date<=?)""",(ld,))
    pep=round(int(f['pe'].iloc[0])/int(f['t'].iloc[0])*100)if int(f['t'].iloc[0])else 0
    pbp=round(int(f['pb'].iloc[0])/int(f['t'].iloc[0])*100)if int(f['t'].iloc[0])else 0
    lr=_q(conn,"SELECT MAX(date) FROM financial_roe").iloc[0,0]
    rc=int(_q(conn,"SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=?",(lr,)).iloc[0,0])if lr else 0
    try:sz=round(os.path.getsize(DB)/1024/1024,1)
    except:sz=0
    return {'daily_date':str(ld),'daily_stocks':dc,'daily_nulls':nl,'daily_total':tr,'daily_ok':dc>=280 and nl==0,
        'pe_pct':pep,'pe_ok':pep>=80,'pb_pct':pbp,'pb_ok':pbp>=90,'roe_date':str(lr or''),'roe_stocks':rc,'roe_ok':rc>=255,'db':sz}

def _sectors(conn):
    """板块历史分析——5日累计涨幅排名"""
    df=_q(conn,"SELECT date,name,pct_change FROM sector_history ORDER BY date DESC LIMIT 900")
    if df.empty or df['date'].nunique()<2:
        return None,0
    days=df['date'].nunique()
    # 取最近5天
    recent_dates=sorted(df['date'].unique(),reverse=True)[:5]
    if len(recent_dates)<5:
        return None,days
    # 计算每个板块在5天内的累计涨幅
    latest=df[df['date']==recent_dates[0]]
    if latest.empty:return None,days
    sectors={}
    for _,r in latest.iterrows():
        name=r['name']
        sector_df=df[(df['name']==name)&(df['date'].isin(recent_dates))]
        if len(sector_df)>=3:
            cum=round(float(sector_df['pct_change'].sum()),1)
            # 连续走强天数
            sdf=sector_df.sort_values('date',ascending=False)
            streak=0
            for _,sr in sdf.iterrows():
                if float(sr['pct_change'])>0:streak+=1
                else:break
            sectors[name]={'cum':cum,'streak':streak}
    if not sectors:return None,days
    ranked=sorted(sectors.items(),key=lambda x:-x[1]['cum'])
    top=ranked[:5];bottom=ranked[-5:][::-1]
    return {'date':recent_dates[0],'days':min(5,days),'top':[{'n':n,'cum':d['cum'],'streak':d['streak']}for n,d in top],
        'bottom':[{'n':n,'cum':d['cum'],'streak':d['streak']}for n,d in bottom]},days

def _signals_all(conn):
    df=_q(conn,"SELECT date,code,name,strategy,action,price,strength,status,reason FROM signal_history ORDER BY date DESC,id DESC LIMIT 200")
    return[{'d':str(r['date']),'c':r['code'],'n':r['name'],'s':r['strategy'],'a':r['action'],
        'p':round(float(r['price']),2)if r['price']else 0,'st':r['status'],
        'strength':round(float(r['strength']),3)if r['strength']else 0,'reason':r['reason']or''}for _,r in df.iterrows()]

def _factors_all(conn):
    try:
        from engine.factor_engine import compute_factor_scores
        df=compute_factor_scores()
        if df.empty:return[]
        return[{'r':i+1,'code':r['code'],'name':r['name'],'score':round(float(r['score']),2),
            'price':round(float(r.get('close',0)),2),'mom':round(float(r.get('momentum',0)or 0),1),
            'vol':round(float(r.get('volatility',0)or 0),2)}for i,(_,r)in enumerate(df.head(15).iterrows())]
    except:return[]

def _positions(conn):
    try:
        from engine.position_tracker import get_positions
        ps=get_positions()
    except:ps=[]
    if not ps:return[]
    codes=[p['code']for p in ps];ph=','.join('?'*len(codes))
    ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
    pr=_q(conn,f"SELECT code,close FROM daily_kline WHERE date=? AND code IN ({ph})",[ld]+codes)
    pm=dict(zip(pr['code'],pr['close']))
    out=[]
    for p in ps:
        cur=pm.get(p['code'])
        if not cur:continue
        pk=float(_q(conn,"SELECT MAX(close) FROM daily_kline WHERE code=? AND date>=?",(p['code'],p['buy_date'])).iloc[0,0])
        pnl=round((cur-p['buy_price'])/p['buy_price']*100,1)
        out.append({'code':p['code'],'name':p['name'],'buy_date':p['buy_date'],'buy':round(p['buy_price'],2),'now':round(float(cur),2),'pnl':pnl,'peak':round(pk,2),'stop':round(pk*0.95,2)})
    return out

def _stock_detail(conn, code):
    df=_q(conn,"SELECT date,open,close,high,low,volume FROM daily_kline WHERE code=? AND date>=date('now','-200 days') ORDER BY date",(code,))
    if df.empty:return None
    name=_q(conn,"SELECT name FROM stock_info WHERE code=?",(code,))
    stock_name=name.iloc[0,0] if not name.empty else code
    closes=df['close'].values;last=float(closes[-1])
    prev=float(closes[-2]) if len(closes)>=2 else last
    chg=(last-prev)/prev*100 if prev else 0
    ma20=float(pd.Series(closes).rolling(20).mean().iloc[-1])if len(closes)>=20 else last
    ma60=float(pd.Series(closes).rolling(60).mean().iloc[-1])if len(closes)>=60 else last
    std20=float(pd.Series(closes).rolling(20).std().iloc[-1])if len(closes)>=20 else 0
    bb_upper=ma20+2*std20;bb_lower=ma20-2*std20
    fin=_q(conn,"SELECT pe,pb FROM financial_data WHERE code=? AND date=(SELECT MAX(date) FROM financial_data WHERE code=? AND date<=?)",(code,code,str(df['date'].iloc[-1])[:10]))
    pe=round(float(fin['pe'].iloc[0]),1)if not fin.empty and fin['pe'].notna().iloc[0] else None
    pb=round(float(fin['pb'].iloc[0]),2)if not fin.empty and fin['pb'].notna().iloc[0] else None
    high20=round(float(df['high'].tail(20).max()),2);low20=round(float(df['low'].tail(20).min()),2)
    avg_vol=float(df['volume'].tail(20).mean());latest_vol=float(df['volume'].iloc[-1])
    sigs=_q(conn,"SELECT date,strategy,action,price,status FROM signal_history WHERE code=? ORDER BY date DESC LIMIT 10",(code,))
    sig_rows=''
    if not sigs.empty:
        sig_rows='<table><thead><tr><th>日期</th><th>策略</th><th>操作</th><th class="ta-r">价格</th><th>状态</th></tr></thead><tbody>'+''.join(
            f'<tr><td>{r["date"]}</td><td class="dim">{r["strategy"]}</td><td>{_tag("t-buy"if r["action"]=="BUY"else"t-sell",r["action"])}</td><td class="ta-r code">¥{float(r["price"]):.2f}</td><td>{_tag("t-pass"if r["status"]=="passed"else"t-block",r["status"])}</td></tr>'
            for _,r in sigs.iterrows())+'</tbody></table>'
    return {'code':code,'name':stock_name,'last':last,'chg':chg,'ma20':ma20,'ma60':ma60,
        'bb_upper':bb_upper,'bb_lower':bb_lower,'pe':pe,'pb':pb,'sig_rows':sig_rows,
        'high20':high20,'low20':low20,'avg_vol':avg_vol,'latest_vol':latest_vol}

def _render_svg_kline(ohlc_df, ma20_series, ma60_series):
    """生成120天SVG K线图"""
    w,h=800,260;ml,mr,mt,mb=50,20,20,30
    pw,ph=w-ml-mr,h-mt-mb
    data=ohlc_df.tail(120)
    if len(data)<5:return'<div class="empty">K线数据不足</div>'
    all_p=[float(x)for x in list(data['high'])+list(data['low'])]
    pmin,pmax=min(all_p),max(all_p);pr=pmax-pmin or 1
    bar_w=max(1,pw/len(data)-1)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" style="width:100%;max-width:800px">']
    # 网格
    for i in range(5):
        y=mt+ph*i/4;price=pmax-pr*i/4
        parts.append(f'<line x1="{ml}" y1="{y:.0f}" x2="{w-mr}" y2="{y:.0f}" stroke="var(--border)" stroke-width="0.5"/>')
        parts.append(f'<text x="{ml-5}" y="{y+4:.0f}" text-anchor="end" fill="var(--text-soft)" font-size="9">{price:.1f}</text>')
    # K线
    for i,(_,d)in enumerate(data.iterrows()):
        x=ml+i*pw/len(data);o,c,h_v,l_v=float(d['open']),float(d['close']),float(d['high']),float(d['low'])
        up=c>=o;color='var(--up)'if up else'var(--down)'
        hy=mt+(pmax-h_v)/pr*ph;ly=mt+(pmax-l_v)/pr*ph
        parts.append(f'<line x1="{x+bar_w/2:.1f}" y1="{hy:.1f}" x2="{x+bar_w/2:.1f}" y2="{ly:.1f}" stroke="{color}" stroke-width="1"/>')
        bt=mt+(pmax-max(o,c))/pr*ph;bh=max(1,abs(c-o)/pr*ph)
        parts.append(f'<rect x="{x:.1f}" y="{bt:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}"/>')
    # MA20
    if ma20_series is not None and len(ma20_series)>=len(data):
        pts=[]
        for i,v in enumerate(ma20_series[-len(data):]):
            if pd.isna(v):continue
            x=ml+i*pw/len(data);y=mt+(pmax-v)/pr*ph;pts.append(f'{x:.1f},{y:.1f}')
        if pts:parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--accent)" stroke-width="1.5"/>')
    # MA60
    if ma60_series is not None and len(ma60_series)>=len(data):
        pts=[]
        for i,v in enumerate(ma60_series[-len(data):]):
            if pd.isna(v):continue
            x=ml+i*pw/len(data);y=mt+(pmax-v)/pr*ph;pts.append(f'{x:.1f},{y:.1f}')
        if pts:parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--accent-2)" stroke-width="1.5"/>')
    # 图例
    parts.append(f'<line x1="{w-120}" y1="14" x2="{w-100}" y2="14" stroke="var(--accent)" stroke-width="1.5"/>')
    parts.append(f'<text x="{w-96}" y="18" fill="var(--text-soft)" font-size="9">MA20</text>')
    parts.append(f'<line x1="{w-60}" y1="14" x2="{w-40}" y2="14" stroke="var(--accent-2)" stroke-width="1.5"/>')
    parts.append(f'<text x="{w-36}" y="18" fill="var(--text-soft)" font-size="9">MA60</text>')
    parts.append('</svg>')
    return'\n'.join(parts)

# ═══════════════ STRATEGY DATA ═══════════════

STRATS=[
    {'key':'ma','n':'双均线趋势跟踪','p':'MA(20,60)','ret':14.5,'sharpe':0.57,'dd':31.6,'style':'trend','months':61,'win':52,
     'desc':'快线上穿慢线买入，下穿卖出。慢均线减少假信号。','principle':'MA20(短期趋势)上穿MA60(中期趋势)=金叉买入，下穿=死叉卖出。其余时间不操作。',
     'good':'强势趋势市场','bad':'震荡市（反复穿越均线，假信号多）',
     'why':'MA(10,30)→MA(20,60)：假信号减少40%，回撤从-62%降到-32%，代价是入场更晚。'},
    {'key':'mb','n':'动量突破','p':'DK(10,2%,10)','ret':10.7,'sharpe':0.39,'dd':60.9,'style':'trend','months':77,'win':51,
     'desc':'突破10日最高×(1+2%)买入，跌破10日最低卖出。','principle':'股价突破近期高点=买方力量确立。2%缓冲过滤假突破。',
     'good':'强势单边行情','bad':'假突破频繁的震荡市(2023-24验证集年化-18%)',
     'why':'回看周期20→10天：更及时，年化从2.5%→10.7%。但回撤仍大(-61%)，必须配合止损。'},
    {'key':'mr','n':'均值回归','p':'BB(10,2.0)','ret':18.9,'sharpe':0.65,'dd':39.9,'style':'reversion','months':56,'win':54,
     'desc':'布林带下轨超卖+MA60向上时买入，上轨超买卖出。','principle':'价格跌破布林带下轨(超卖)→大概率回归中轨。MA60向上过滤下跌趋势。首次下穿触发，避免重复发信号。',
     'good':'震荡市(2023-24验证集年化+40%)','bad':'强趋势市（超卖后还有更超卖）',
     'why':'BB(20,2.0)→BB(10,2.0)：更短周期捕捉短期超卖，反应更快。标准差保持2.0。'},
]

# ═══════════════ PAGES ═══════════════

def page_index(conn, m, h, sigs, fs, ps):
    """信号首页"""
    today=datetime.now()
    try:daily_age=(today-datetime.strptime(m['date'],'%Y-%m-%d')).days
    except:daily_age=99
    try:sig_date=sigs[0]['d'] if sigs else None;sig_age=(today-datetime.strptime(sig_date,'%Y-%m-%d')).days if sig_date else 99
    except:sig_age=99
    fresh_ok=daily_age<=2 and sig_age<=3

    # 数据状态条
    fresh_html=f'<div class="banner {"banner-ok" if fresh_ok else "banner-warn"}">'
    if fresh_ok: fresh_html+=f'✅ 数据: {m["date"]} · 信号: {sig_date or "无"} · 信号可信'
    else: fresh_html+=f'⚠️ 数据: {m["date"]}({daily_age}天前) · 信号: {sig_date or "无"}({sig_age}天前) · 信号不可信'
    fresh_html+='</div>'

    # 市场条
    reg_label='强势 ↑'if m['regime']=='strong'else'弱势 ↓'
    reg_cls='up'if m['regime']=='strong'else'dn'
    mkt_html=f'<div class="panel"><div class="panel-bd" style="display:flex;align-items:center;gap:20px;padding:10px 16px"><span style="font-size:20px;font-weight:700">{m["close"]:.0f}</span><span style="font-size:12px;color:var(--text-muted)">CSI300</span><span class="{reg_cls}" style="font-weight:600">{reg_label}</span><span class="dim" style="font-size:11px">5日{_ud(m["r5"])}% · 20日{_ud(m["r20"])}% · 涨{m["up"]}/跌{m["down"]} {"偏多"if m["up"]>m["down"] else"偏空"}</span><span class="dim" style="font-size:10px;margin-left:auto">数据: {m["date"]}</span></div></div>'

    # 持仓
    pos_html=''
    if ps:
        stops=[p for p in ps if p['now']<=p['stop']]
        if stops:
            pos_html='<div class="banner banner-warn">'+''.join(f'⚠️ {p["code"]} {p["name"]} 触发止损(现¥{p["now"]}≤止损¥{p["stop"]}) 建议卖出  ·  'for p in stops)+'</div>'
        else:
            pos_html=f'<div class="panel"><div class="panel-bd" style="padding:8px 16px;font-size:12px"><span class="dim">💼 持仓 {len(ps)}只 · 无止损触发</span></div></div>'

    # 信号排名
    sig_html=''
    if not sigs: sig_html='<div class="empty">📭 暂无信号——策略尚未在服务器持续运行</div>'
    else:
        latest_date=sigs[0]['d']
        buys=[s for s in sigs if s['a']=='BUY'and s['st']=='passed'and s['d']==latest_date]
        blocked=[s for s in sigs if s['st']=='blocked'and s['d']==latest_date]
        sells=[s for s in sigs if s['a']=='SELL'and s['st']=='passed'and s['d']==latest_date]

        if buys:
            # 排名
            ranked=[]
            for s in buys:
                score=s['strength']*10
                if m['regime']=='strong'and('均线'in s['s']or'突破'in s['s']):score+=0.3
                if m['regime']!='strong'and'回归'in s['s']:score+=0.3
                ranked.append({**s,'score':score})
            # 合并同股票
            merged={}
            for r in ranked:
                c=r['c']
                if c not in merged:merged[c]={**r,'strategies':[r['s']],'max_score':r['score']}
                else:
                    merged[c]['strategies'].append(r['s'])
                    merged[c]['max_score']=max(merged[c]['max_score'],r['score'])
            for c,mr in merged.items():
                if len(mr['strategies'])>=2:mr['max_score']+=0.5;mr['cross']=True
                else:mr['cross']=False
            ranked_list=sorted(merged.values(),key=lambda x:-x['max_score'])

            rows=''
            for i,r in enumerate(ranked_list[:20]):
                cross='⭐⭐ 'if r.get('cross')else''
                strats='/'.join(r['strategies'])
                rows+=f'<div class="sig-row"><span class="sig-rank">{i+1}</span><div class="sig-meta"><span class="code">{r["c"]}</span> <span>{r["n"]}</span> <span class="dim">{cross}{strats}</span><div class="reason">{r.get("reason","")[:80]}</div></div><span class="code">¥{r["p"]:.2f}</span><span class="dim" style="min-width:40px;text-align:right">强度 {r["strength"]:.2f}</span><a href="stock_{r["c"]}.html" style="color:var(--accent);font-size:11px;text-decoration:none;margin-left:8px">详情 →</a></div>'

            sig_html=f'<div class="panel"><div class="panel-hd">🎯 今日信号（¥50以内） <span class="dim" style="font-size:10px;text-transform:none;letter-spacing:0">{latest_date} · {len(ranked_list)}只</span></div><div class="panel-bd">{rows}</div></div>'
        else:
            sig_html=f'<div class="panel"><div class="panel-hd">🎯 今日信号（¥50以内） <span class="dim" style="font-size:10px;text-transform:none;letter-spacing:0">{latest_date}</span></div><div class="panel-bd"><div class="empty">📭 今日无符合条件的买入信号<br><small class="dim">（所有买入信号均被拦截：大盘择时/价格超限/涨停/停牌）</small></div></div></div>'

        # 拦截统计
        if blocked:
            bc={}
            for b in blocked:
                r=b.get('reason','其他')
                if'跌停'in r:k='跌停'
                elif'停牌'in r:k='停牌'
                elif'ST'in r:k='ST'
                elif'涨停'in r:k='涨停'
                elif'大盘'in r or'择时'in r:k='大盘择时'
                elif'股价'in r or'上限'in r:k='买不起(>¥50)'
                elif'流动'in r:k='流动性'
                else:k='其他'
                bc[k]=bc.get(k,0)+1
            block_items=' · '.join(f'{k}:{v}'for k,v in sorted(bc.items(),key=lambda x:-x[1]))
            sig_html+=f'<div class="panel"><div class="panel-bd" style="padding:8px 16px;font-size:11px;color:var(--text-muted)">📋 今日拦截 {len(blocked)} 条: {block_items}</div></div>'

    body=f'''
    {fresh_html}
    {mkt_html}
    {pos_html}
    {sig_html}
    <div style="display:flex;gap:12px;margin-top:12px;font-size:12px">
      <a href="strategy.html" style="color:var(--accent);text-decoration:none">📈 策略分析 →</a>
      <a href="factors.html" style="color:var(--accent);text-decoration:none">🔝 因子参考 →</a>
      <a href="market.html" style="color:var(--accent);text-decoration:none">📊 市场监控 →</a>
      <a href="signals.html" style="color:var(--accent);text-decoration:none">📡 信号日志 →</a>
    </div>'''
    return _page('信号首页','index.html',body)


def page_stock(conn, code):
    """股票详情页"""
    d=_stock_detail(conn,code)
    if not d:return _page(f'{code}','',f'<div class="empty">无数据: {code}</div>')

    df=_q(conn,"SELECT date,open,close,high,low,volume FROM daily_kline WHERE code=? AND date>=date('now','-200 days') ORDER BY date",(code,))
    closes=df['close'].values
    ma20_s=pd.Series(closes).rolling(20).mean()
    ma60_s=pd.Series(closes).rolling(60).mean()
    svg=_render_svg_kline(df,ma20_s,ma60_s)

    pe_str=f'PE: {d["pe"]}'if d['pe']else'PE: —'
    pb_str=f'PB: {d["pb"]}'if d['pb']else'PB: —'

    body=f'''
    <div style="margin-bottom:12px"><a href="index.html" style="color:var(--accent);text-decoration:none;font-size:12px">← 返回信号列表</a></div>

    <div class="panel"><div class="panel-bd">
      <div class="stock-header">
        <div><div class="code" style="font-size:14px">{d['code']}</div><div style="font-size:11px;color:var(--text-muted)">{d['name']}</div></div>
        <div><span class="price">{d['last']:.2f}</span><span class="chg {("up"if d["chg"]>=0 else"dn")}">{("+"if d["chg"]>=0 else"")}{d["chg"]:.1f}%</span></div>
        <div class="info"><table><tr><td class="dim">{pe_str}</td><td class="dim">{pb_str}</td></tr></table></div>
      </div>

      <div class="chart-box">{svg}</div>

      <div class="grid g2" style="margin-top:8px">
        <div class="panel"><div class="panel-hd">技术指标</div><div class="panel-bd" style="font-size:12px">
          <div class="kv"><span class="dim">MA20</span><span class="code">{d["ma20"]:.2f}</span></div>
          <div class="kv"><span class="dim">MA60</span><span class="code">{d["ma60"]:.2f}</span></div>
          <div class="kv"><span class="dim">BB上轨</span><span class="code">{d["bb_upper"]:.2f}</span></div>
          <div class="kv"><span class="dim">BB下轨</span><span class="code">{d["bb_lower"]:.2f}</span></div>
          <div class="kv"><span class="dim">20日最高</span><span class="code">{d["high20"]:.2f}</span></div>
          <div class="kv"><span class="dim">20日最低</span><span class="code">{d["low20"]:.2f}</span></div>
        </div></div>
        <div class="panel"><div class="panel-hd">历史信号</div><div class="panel-bd">{d["sig_rows"]if d["sig_rows"]else'<div class="empty">暂无历史信号</div>'}</div></div>
      </div>
    </div></div>'''
    return _page(f'{d["code"]} {d["name"]}','',body)


def page_strategy(sigs):
    """策略分析页"""
    colors=[('#4a9eff','#4a9eff14'),('#059669','#05966914'),('#7c3aed','#7c3aed14')]
    cards=''
    for i,s in enumerate(STRATS):
        # 最近信号
        sig_part='<div class="dim" style="font-size:11px;margin-top:6px">暂无最近信号</div>'
        strat_sigs=[x for x in sigs if s['key']=='ma'and'均线'in x['s']or s['key']=='mb'and'突破'in x['s']or s['key']=='mr'and'回归'in x['s']]
        if strat_sigs:
            sig_part='<div style="font-size:10px;margin-top:6px"><span class="dim">最近信号: </span>'+''.join(
                f'{_tag("t-buy"if x["a"]=="BUY"else"t-sell",x["a"])} {x["c"]} {x["n"]} ¥{x["p"]:.2f} <span class="dim">({x["d"]})</span> '
                for x in strat_sigs[:3])+'</div>'

        cards+=f'''<div class="panel" style="border-left:3px solid {colors[i][0]}"><div class="panel-bd">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px"><span style="font-size:15px;font-weight:700">{s["n"]}</span><span>{_tag("t-trend"if s["style"]=="trend"else"t-rev","趋势"if s["style"]=="trend"else"反转")} <span class="dim">{s["p"]}</span></span></div>
        <div style="font-size:12px;color:var(--text-muted);line-height:1.6"><strong>原理：</strong>{s["principle"]}</div>
        <div style="font-size:12px;margin-top:4px"><strong>适合：</strong><span class="up">{s["good"]}</span> · <strong>不适合：</strong><span class="dn">{s["bad"]}</span></div>
        <div style="font-size:12px;color:var(--text-muted);margin-top:4px"><strong>为什么选这个参数：</strong>{s["why"]}</div>
        <div style="display:flex;gap:24px;align-items:center;margin-top:12px"><span style="font-size:24px;font-weight:700;color:{colors[i][0]}">{s["ret"]}%</span><span class="dim" style="font-size:11px">年化 · 夏普{s["sharpe"]:.2f} · 回撤{s["dd"]:.0f}% · 胜率{s["win"]}% · {s["months"]}月</span></div>
        {sig_part}</div></div>'''

    disclaimer='''
    <div class="panel" style="margin-top:12px"><div class="panel-bd" style="background:#d9770608;font-size:11px;color:var(--warn)">
    <strong>⚠️ 回测收益 ≠ 实盘收益。</strong>本地数据源(AKShare)存在约+3~6pp系统性偏差。止损模拟假设精确执行(实盘有滑点)。未考虑最低佣金(5元/笔)对小资金的放大效应。<strong>保守估计: 实盘收益 ≈ 回测收益 × 0.6~0.8。三策略使用硬阈值信号（非排名），本地回测方向可信。</strong></div></div>'''

    body=f'<div class="hero"><h2>📋 策略分析</h2><p>三个策略的完整讲解——原理、参数选择、适用环境</p></div>{cards}{disclaimer}'
    return _page('策略分析','strategy.html',body)


def page_market(m,sec,sec_days):
    """市场监控"""
    reg_label='强势 ↑'if m['regime']=='strong'else'弱势 ↓'
    reg_cls='up'if m['regime']=='strong'else'dn'

    sec_html=''
    if sec:
        top_rows=''.join(f'<div class="kv"><span>{s["n"]}</span><span><span class="up">+{s["cum"]}%</span> <span class="dim" style="font-size:10px">{"▲"*min(s["streak"],3)}</span></span></div>'for s in sec['top'])
        bot_rows=''.join(f'<div class="kv"><span>{s["n"]}</span><span><span class="dn">{s["cum"]}%</span> <span class="dim" style="font-size:10px">{"▼"*min(3,0)}</span></span></div>'for s in sec['bottom'])
        sec_html=f'''
    <div class="grid g2" style="margin-bottom:12px">
      <div class="panel"><div class="panel-hd">▲ 强势板块（{sec["days"]}日累计）</div><div class="panel-bd">{top_rows}</div></div>
      <div class="panel"><div class="panel-hd">▼ 走弱板块（{sec["days"]}日累计）</div><div class="panel-bd">{bot_rows}</div></div>
    </div>'''
    else:
        bar_w=min(100,sec_days/5*100)
        sec_html=f'''
    <div class="panel" style="margin-bottom:12px"><div class="panel-bd" style="text-align:center;padding:24px">
      <div class="dim" style="font-size:13px;margin-bottom:8px">板块数据积累中（{sec_days}/5天）</div>
      <div style="height:4px;background:var(--border-light);border-radius:2px;overflow:hidden;max-width:200px;margin:0 auto">
        <div style="height:100%;width:{bar_w:.0f}%;background:var(--accent);border-radius:2px;transition:width 1s"></div></div>
      <div class="dim" style="font-size:11px;margin-top:6px">需要 report.py 每日运行积累 ≥5天数据</div></div></div>'''

    body=f'''
    <div class="hero"><h2>📊 市场监控</h2><p>数据: {m["date"]} · {m["stocks"]}只 · CSI300 {m["close"]:.0f} <span class="{reg_cls}" style="font-weight:500">{reg_label}</span> · 5日{_ud(m["r5"])}% · 20日{_ud(m["r20"])}% · 涨{m["up"]}/跌{m["down"]} {"偏多"if m["up"]>m["down"]else"偏空"}</p></div>
    {sec_html}'''
    return _page('市场监控','market.html',body)


def page_factors(fs, sigs):
    """因子参考——辅助"""
    if not fs: return _page('因子参考','factors.html','<div class="hero"><h2>🔝 因子参考</h2></div><div class="empty">暂无数据</div>')

    # 交叉标签
    latest_date=sigs[0]['d'] if sigs else''
    buy_codes=set(s['c']for s in sigs if s['a']=='BUY'and s['st']=='passed'and s['d']==latest_date)

    rows=''.join(f'<tr><td>{f["r"]}</td><td class="code">{f["code"]}</td><td>{f["name"]}</td><td class="ta-r code">¥{f["price"]:.2f}</td><td class="ta-r"><span class="ac fw">{f["score"]:.1f}</span></td><td class="ta-r {("up"if f["mom"]>=0 else"dn")}">{f["mom"]:+.1f}</td><td>{"🟢 策略信号"if f["code"] in buy_codes else""}</td></tr>'for f in fs)

    body=f'''<div class="hero"><h2>🔝 因子参考（辅助）</h2><p>≤¥50已过滤 · 7因子加权 · ROE 120天滞后防泄露 · 本地数据排名仅供参考</p></div>
    <div class="panel"><div class="panel-hd">排名 Top 15</div><div class="panel-bd"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th class="ta-r">现价</th><th class="ta-r">得分</th><th class="ta-r">动量</th><th>标签</th></tr></thead><tbody>{rows}</tbody></table></div></div>
    <div class="panel" style="margin-top:12px"><div class="panel-bd" style="background:#d9770608;font-size:11px;color:var(--warn)"><strong>⚠️ 因子排名基于本地数据(AKShare)，与聚宽排名存在差异。</strong>多因子仅为辅助参考，选股决策以三策略信号为主。</div></div>'''
    return _page('因子参考','factors.html',body)


def page_signals(sigs):
    """信号日志"""
    if not sigs: return _page('信号日志','signals.html','<div class="hero"><h2>📡 信号日志</h2></div><div class="empty">暂无信号</div>')

    gb={}
    for s in sigs:
        if s['d']not in gb:gb[s['d']]=[]
        gb[s['d']].append(s)
    rows=''
    for date,slist in sorted(gb.items(),reverse=True):
        rows+=f'<div style="font-size:12px;color:var(--text-muted);padding:6px 0;font-weight:600;border-bottom:1px solid var(--border);margin:8px 0 4px">{date} ({len(slist)}条)</div>'
        for s in slist[:20]:
            rows+=f'<div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:11px"><span class="code">{s["c"]}</span><span class="dim">{s["n"]}</span><span class="dim" style="flex:1;font-size:10px">{s["s"]}</span>{_tag("t-buy"if s["a"]=="BUY"else"t-sell",s["a"])}<span class="code">¥{s["p"]:.2f}</span>{_tag("t-pass"if s["st"]=="passed"else"t-block",s["st"])}<span class="dim" style="font-size:10px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{s.get("reason","")}</span></div>'

    buy=sum(1 for s in sigs if s['a']=='BUY');sell=sum(1 for s in sigs if s['a']=='SELL')
    passed=sum(1 for s in sigs if s['st']=='passed');blocked=sum(1 for s in sigs if s['st']=='blocked')

    body=f'''<div class="hero"><h2>📡 信号日志</h2><p>共{len(sigs)}条 · 买入{buy} · 卖出{sell} · 通过{passed} · 拦截{blocked}</p></div>
    <div class="panel"><div class="panel-bd" style="max-height:70vh;overflow-y:auto">{rows}</div></div>'''
    return _page('信号日志','signals.html',body)


def page_health(h):
    """运维页"""
    body=f'''<div class="hero"><h2>🩺 数据健康</h2><p>运维数据——平时不需要看</p></div>
    <div class="grid g2">
      <div class="panel"><div class="panel-hd">数据库概览</div><div class="panel-bd">
        <div class="kv"><span class="dim">daily_kline</span><span>{h["daily_total"]:,}行 · {h["daily_date"]} · {h["daily_stocks"]}只</span></div>
        <div class="kv"><span class="dim">PE覆盖率</span><span>{_tag("t-pass"if h["pe_ok"]else"t-warn",f'{h["pe_pct"]}%')}</span></div>
        <div class="kv"><span class="dim">PB覆盖率</span><span>{_tag("t-pass"if h["pb_ok"]else"t-warn",f'{h["pb_pct"]}%')}</span></div>
        <div class="kv"><span class="dim">ROE</span><span>{h["roe_stocks"]}只 · {h["roe_date"]}</span></div>
        <div class="kv"><span class="dim">DB大小</span><span>{h["db"]} MB</span></div>
      </div></div>
      <div class="panel"><div class="panel-hd">数据校验</div><div class="panel-bd">
        <div class="kv"><span class="dim">日线NULL</span><span>{h["daily_nulls"]}</span></div>
        <div class="kv"><span class="dim">日线股票数</span><span>{_tag("t-pass"if h["daily_ok"]else"t-warn","达标"if h["daily_ok"]else"不足")}</span></div>
      </div></div>
    </div>'''
    return _page('运维','',body)


# ═══════════════ MAIN ═══════════════

def build():
    conn=sqlite3.connect(DB)
    ts=datetime.now().strftime('%H:%M')

    m=_market(conn);h=_health(conn);sigs=_signals_all(conn)
    fs=_factors_all(conn);ps=_positions(conn)
    sec,sec_days=_sectors(conn)

    os.makedirs('dashboard',exist_ok=True)

    pages=[
        ('index.html',page_index(conn,m,h,sigs,fs,ps)),
        ('strategy.html',page_strategy(sigs)),
        ('market.html',page_market(m,sec,sec_days)),
        ('factors.html',page_factors(fs,sigs)),
        ('signals.html',page_signals(sigs)),
        ('health.html',page_health(h)),
    ]

    # 股票详情页：为所有出现在买入信号中的股票生成
    buy_codes=set()
    if sigs:
        latest=sigs[0]['d']
        buy_codes=set(s['c']for s in sigs if s['a']=='BUY'and s['st']=='passed'and s['d']==latest)
    for code in buy_codes:
        pages.append((f'stock_{code}.html',page_stock(conn,code)))

    for fn,html in pages:
        with open(f'dashboard/{fn}','w',encoding='utf-8')as f:f.write(html)
        print(f'✅ dashboard/{fn} ({len(html):,} bytes)')

    conn.close()
    print(f'   → 打开 dashboard/index.html · {ts}')

if __name__=='__main__':
    build()
