#!/usr/bin/env python3
"""
仪表盘生成器 v4 —— 多页面输出，Python 直渲染 HTML，零 JS 依赖
输出: dashboard/{index,market,strategy}.html
用法: PYTHONPATH=. python scripts/generate_dashboard_data.py
"""
import sqlite3, os, sys
from datetime import datetime, timedelta
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = 'data/stocks.db'

# ═══════════════ CSS ═══════════════

CSS = '''<style>
:root{--bg:#0b0e11;--s1:#1e2329;--s2:#252a30;--b:#2b3139;--t:#eaecef;--d:#848e9c;--dd:#5e6673;--up:#0ecb81;--dn:#f6465d;--bl:#4a9eff;--am:#f0b90b;--pu:#a85cef}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--t);font:13px/1.45 -apple-system,BlinkMacSystemFont,'SF Mono','Segoe UI',monospace;padding:0;-webkit-font-smoothing:antialiased}
nav{background:var(--s1);border-bottom:1px solid var(--b);padding:0 20px;display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:10}
nav a{color:var(--d);text-decoration:none;padding:12px 16px;font-size:12px;border-bottom:2px solid transparent;transition:all .15s}
nav a:hover{color:var(--t);background:var(--s2)}
nav a.active{color:var(--bl);border-bottom-color:var(--bl)}
nav .brand{font-weight:700;color:var(--t);margin-right:16px;font-size:13px;letter-spacing:-.2px}
main{padding:16px 20px;max-width:1280px;margin:0 auto}
.grid{display:grid;gap:10px}
.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr 1fr 1fr}.g4{grid-template-columns:1fr 1fr 1fr 1fr}
.g13{grid-template-columns:1fr 3fr}.g31{grid-template-columns:3fr 1fr}
.panel{background:var(--s1);border:1px solid var(--b);border-radius:6px;overflow:hidden;margin-bottom:10px}
.panel-hd{padding:8px 14px;border-bottom:1px solid var(--b);font-size:11px;color:var(--d);text-transform:uppercase;letter-spacing:.5px;font-weight:500;display:flex;justify-content:space-between;align-items:center}
.panel-bd{padding:12px 14px}
.up{color:var(--up)}.dn{color:var(--dn)}.bl{color:var(--bl)}.dim{color:var(--dd)}.fw{font-weight:600}
.code{font-weight:600;font-size:12px}.ta-r{text-align:right}.ta-c{text-align:center}
.metric{text-align:center;padding:6px 4px}
.metric .l{font-size:10px;color:var(--dd);text-transform:uppercase;letter-spacing:.3px;margin-bottom:2px}
.metric .v{font-size:18px;font-weight:600;line-height:1.1}
.metric .s{font-size:10px;margin-top:1px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;color:var(--dd);font-weight:500;padding:5px 8px;border-bottom:1px solid var(--b);font-size:10px;text-transform:uppercase;letter-spacing:.3px}
td{padding:4px 8px;border-bottom:1px solid rgba(43,49,57,0.5)}tr:hover td{background:rgba(255,255,255,0.02)}
.tag{display:inline-block;padding:1px 5px;border-radius:2px;font-size:10px;font-weight:600}
.t-up{background:#0ecb8118;color:var(--up)}.t-dn{background:#f6465d18;color:var(--dn)}
.t-bl{background:#4a9eff18;color:var(--bl)}.t-pass{background:#0ecb8114;color:var(--up)}.t-block{background:#848e9c14;color:var(--d)}
.t-trend{background:#4a9eff12;color:var(--bl)}.t-rev{background:#a85cef12;color:var(--pu)}
.t-buy{background:#0ecb8118;color:var(--up)}.t-sell{background:#f6465d18;color:var(--dn)}
.empty{color:var(--dd);text-align:center;padding:28px;font-size:12px}
.bar-row{display:flex;align-items:center;gap:6px;margin:2px 0}
.bar-row span:first-child{color:var(--d);width:28px;font-size:10px}
.bar-row span:last-child{width:32px;text-align:right;font-size:10px;color:var(--d)}
.bar{flex:1;height:3px;background:var(--s2);border-radius:2px;overflow:hidden}
.bar-f{height:100%;border-radius:2px}
.pos-item{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--b)}
.pos-item:last-child{border-bottom:none}
.pos-code{font-weight:600;font-size:13px;min-width:50px}
.pos-meta{flex:1;font-size:10px;color:var(--d);line-height:1.4}
.pos-pnl{font-size:16px;font-weight:600}
.strat-card{border:1px solid var(--b);border-radius:8px;padding:16px;margin-bottom:10px}
.hero{background:var(--s1);border:1px solid var(--b);border-radius:8px;padding:20px 24px;margin-bottom:14px}
.hero h2{font-size:16px;margin-bottom:6px}
.hero p{color:var(--d);font-size:12px;line-height:1.6;max-width:700px}
.explain{font-size:12px;color:var(--d);line-height:1.7}
.explain strong{color:var(--t)}
.row-item{display:flex;justify-content:space-between;padding:3px 0;font-size:11px}
@media(max-width:900px){.g2,.g3,.g4,.g13,.g31{grid-template-columns:1fr}nav{flex-wrap:wrap}}
</style>'''

# ═══════════════ SHARED NAV ═══════════════

def _nav(active=''):
    links = [('index.html','仪表盘'),('market.html','市场监控'),('strategy.html','策略分析'),
             ('signals.html','信号日志'),('factors.html','因子引擎')]
    items = ''.join(f'<a href="{h}"{" class=active" if h==active else ""}>{n}</a>' for h,n in links)
    return f'<nav><span class="brand">量化交易</span>{items}</nav>'

# ═══════════════ DATA HELPERS ═══════════════

def _q(conn,s,p=None):
    return pd.read_sql_query(s,conn,params=p) if p else pd.read_sql_query(s,conn)

def _tag(c,t): return f'<span class="tag {c}">{t}</span>'
def _ud(v,f='+.1f'): return f'<span class="{"up" if v>=0 else "dn"}">{v:{f}}</span>' if v<0 else f'<span class="up">+{v:{f}}</span>'

STRATS = [
    {'key':'ma','n':'双均线趋势跟踪','p':'MA(20,60)','ret':14.5,'sharpe':0.57,'dd':31.6,'style':'trend','months':61,'win':52,
     'desc':'快线(MA20)上穿慢线(MA60)买入，下穿卖出。慢均线减少震荡市假信号，捕获中期趋势。',
     'principle':'两条移动平均线——快线(MA20)代表短期趋势，慢线(MA60)代表中期趋势。当短期趋势向上穿越中期趋势时（金叉），说明上涨动能确立，买入。反过来（死叉）卖出。其余时间不操作。',
     'good_for':'强势趋势市场——有明显的方向性行情，均线呈多头排列。',
     'bad_for':'震荡市——价格反复穿越均线，产生大量假信号（反复买卖都被打脸）。',
     'why_params':'MA(10,30)→MA(20,60)。更慢的均线减少了40%的假信号，交易次数从295次降到179次，回撤从-62%降到-32%。代价是入场更晚——在快速趋势中可能错过前半段。',
     'signals':[],'wf':[('训练集 19-22','24.5%','0.87','-21.7%','79次'),('验证集 23-24','0.3%','0.09','-31.6%','42次'),('测试集 25-26','6.7%','0.33','-14.4%','48次')]},
    {'key':'mb','n':'动量突破','p':'DK(10,2%,10)','ret':10.7,'sharpe':0.39,'dd':60.9,'style':'trend','months':77,'win':51,
     'desc':'收盘价突破过去10日最高价×(1+2%缓冲)买入，跌破10日最低价卖出。快速识别突破行情。',
     'principle':'当股价突破近期高点时，说明买方力量压倒卖方，趋势可能启动。2%的缓冲区过滤掉假突破（小幅刺穿后回落）。卖出用10日最低价——跌破近期支撑说明趋势反转。',
     'good_for':'强势单边行情——突破后持续上涨，涨幅远超2%缓冲。',
     'bad_for':'假突破频繁的震荡市——突破后立刻回落，反复止损。2023-2024验证集年化-18%就是这个原因。',
     'why_params':'回看周期20→10天。10天突破比20天更及时，年化从2.5%提升到10.7%。但回撤仍然大(-61%)，必须配合止损使用。',
     'signals':[],'wf':[('训练集 19-22','31.6%','0.81','-34.8%','157次'),('验证集 23-24','-18.0%','-0.55','-53.6%','100次'),('测试集 25-26','40.6%','1.01','-14.4%','80次')]},
    {'key':'mr','n':'均值回归','p':'BB(10,2.0)','ret':18.9,'sharpe':0.65,'dd':39.9,'style':'reversion','months':56,'win':54,
     'desc':'布林带(10,2.0)下轨超卖+MA60向上时买入，上轨超买卖出。捕捉短期过度反应后的回归。',
     'principle':'布林带是MA20±2倍标准差构成的通道。统计学上，价格在通道内的概率约95%。当价格跌破下轨时（超卖），大概率会回归中轨。MA60向上的过滤条件确保不在下跌趋势中接飞刀。首次下穿触发避免连续多天重复发信号。',
     'good_for':'震荡市——价格有明确的上下边界，反复在通道内波动。2023-2024验证集年化+40%正得益于此。',
     'bad_for':'强趋势市——价格可以一直贴着上轨涨或下轨跌，布林带不断扩张，"超卖"之后还有"更超卖"。',
     'why_params':'BB(20,2.0)→BB(10,2.0)。更短的周期让布林带更敏感地捕捉到短期超卖——10日均线比20日均线反应更快。标准差保持2.0——太窄(1.5)假信号多，太宽(2.5)信号太少。',
     'signals':[],'wf':[('训练集 19-22','-4.2%','0.00','-39.9%','72次'),('验证集 23-24','40.7%','1.41','-6.9%','46次'),('测试集 25-26','52.3%','1.60','-14.7%','49次')]},
]

# ═══════════════ DATA QUERIES ═══════════════

def _market(conn):
    ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
    sc=int(_q(conn,"SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?",(ld,)).iloc[0,0])
    idx=_q(conn,"SELECT date, AVG(close) as c FROM daily_kline WHERE date>=date('now','-180 days') GROUP BY date ORDER BY date")
    last=float(idx['c'].iloc[-1])if len(idx)else 0
    r5=round((idx['c'].iloc[-1]/idx['c'].iloc[-5]-1)*100,2)if len(idx)>=5 else 0
    r20=round((idx['c'].iloc[-1]/idx['c'].iloc[-20]-1)*100,2)if len(idx)>=20 else 0
    r60=round((idx['c'].iloc[-1]/idx['c'].iloc[-60]-1)*100,2)if len(idx)>=60 else 0
    if len(idx)>=60:
        m20=idx['c'].rolling(20).mean();m60=idx['c'].rolling(60).mean()
        reg='strong'if m20.iloc[-1]>m60.iloc[-1]else'weak'
    else:reg='unknown'
    chg=idx['c'].pct_change().tail(20)
    # 近期趋势线数据
    trend_pts=idx.tail(90)[['date','c']].copy();trend_pts['date']=trend_pts['date'].apply(lambda x:str(x)[5:])
    return {'date':str(ld),'stocks':sc,'regime':reg,'close':last,'r5':r5,'r20':r20,'r60':r60,
        'up':int((chg>0).sum()),'down':int((chg<0).sum()),
        'trend':[{'d':str(d)[5:],'v':round(float(c),1)}for d,c in zip(trend_pts['date'].tail(60),trend_pts['c'].tail(60))]}

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

def _breadth(conn):
    """市场广度——计算多天历史数据"""
    ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
    # 取最近60天的数据
    all_dates=_q(conn,"SELECT DISTINCT date FROM daily_kline WHERE date>=date('now','-90 days') ORDER BY date")
    dates=all_dates['date'].tolist()
    breadth_daily=[]
    for i,d in enumerate(dates[-30:]):  # 最近30天
        df=_q(conn,"SELECT code,close FROM daily_kline WHERE date=?",(d,))
        prev=_q(conn,"SELECT code,close FROM daily_kline WHERE date=(SELECT MAX(date) FROM daily_kline WHERE date<?)",(d,))
        if df.empty or prev.empty:continue
        pm=dict(zip(prev['code'],prev['close']))
        adv=decl=0
        for _,r in df.iterrows():
            if r['code']in pm:
                if r['close']>pm[r['code']]:adv+=1
                elif r['close']<pm[r['code']]:decl+=1
        # 采样 MA20/MA60 以上占比
        sample=min(50,len(df))
        a20=a60=0
        for _,r in df.head(sample).iterrows():
            hist=_q(conn,"SELECT close FROM daily_kline WHERE code=? AND date<=? ORDER BY date LIMIT 80",(r['code'],d))
            if len(hist)>=60:
                if float(hist['close'].iloc[-1])>hist['close'].rolling(20).mean().iloc[-1]:a20+=1
                if float(hist['close'].iloc[-1])>hist['close'].rolling(60).mean().iloc[-1]:a60+=1
        breadth_daily.append({'d':str(d)[5:],'adv':adv,'decl':decl,'total':adv+decl,
            'a20':round(a20/sample*100)if sample else 0,'a60':round(a60/sample*100)if sample else 0})
    return breadth_daily

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

def _factors(conn):
    try:
        from engine.factor_engine import compute_factor_scores
        df=compute_factor_scores()
        if df.empty:return[]
        codes=[r['code']for _,r in df.head(20).iterrows()]
        ph=','.join('?'*len(codes))
        ld=_q(conn,"SELECT MAX(date) FROM daily_kline").iloc[0,0]
        prices=_q(conn,f"SELECT code,close FROM daily_kline WHERE date=? AND code IN ({ph})",[ld]+codes)
        pm=dict(zip(prices['code'],prices['close']))
        return[{'r':i+1,'code':r['code'],'name':r['name'],'score':round(float(r['score']),2),
            'mom':round(float(r.get('momentum',0)or 0),1),'vol':round(float(r.get('volatility',0)or 0),2),
            'price':round(float(pm.get(r['code'],0)),2)}for i,(_,r)in enumerate(df.head(20).iterrows())]
    except:return[]

def _signals(conn):
    df=_q(conn,"SELECT date,code,name,strategy,action,price,status,reason FROM signal_history ORDER BY date DESC,id DESC LIMIT 100")
    return[{'d':str(r['date']),'c':r['code'],'n':r['name'],'s':r['strategy'],'a':r['action'],'p':round(float(r['price']),2)if r['price']else 0,'st':r['status'],'reason':r['reason']or''}for _,r in df.iterrows()]

def _sectors(conn):
    df=_q(conn,"SELECT date,name,pct_change FROM sector_history ORDER BY date DESC LIMIT 180")
    if df.empty:return None
    ld=df['date'].iloc[0];latest=df[df['date']==ld]
    return{'date':str(ld),'top':[{'n':r['name'],'pct':round(float(r['pct_change']),1)}for _,r in latest.nlargest(5,'pct_change').iterrows()],
        'bottom':[{'n':r['name'],'pct':round(float(r['pct_change']),1)}for _,r in latest.nsmallest(5,'pct_change').iterrows()]}

def _signal_for_strategy(conn, strat_name):
    """取某策略的最近信号作为示例"""
    df=_q(conn,"SELECT date,code,name,action,price,reason FROM signal_history WHERE strategy LIKE ? AND status='passed' ORDER BY date DESC LIMIT 3",(f'%{strat_name}%',))
    return[{'d':str(r['date']),'c':r['code'],'n':r['name'],'a':r['action'],'p':round(float(r['price']),2)if r['price']else 0,'reason':r['reason']or''}for _,r in df.iterrows()]

# ═══════════════ PAGE RENDERERS ═══════════════

def _page(title,active,body):
    return f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>{title} · 量化交易</title>\n{CSS}\n</head>\n<body>\n{_nav(active)}\n<main>\n{body}\n</main>\n</body>\n</html>'

def page_index(m,h,bd,ps,fs,sigs,sec):
    """仪表盘首页"""
    reg_cls='up'if m['regime']=='strong'else'dn'
    reg_label='TRENDING ↑'if m['regime']=='strong'else'RANGE ↓'

    health=''.join(f'<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--b);font-size:11px"><span class="dim">{k}</span><span>{_tag("t-up"if ok else"t-dn",v)}{extra}</span></div>'
        for k,v,ok,extra in[('日线',f'{h["daily_stocks"]}只',h['daily_ok'],f'<span class="dim"> {h["daily_date"]} NULL:{h["daily_nulls"]} {h["daily_total"]:,}行</span>'),
        ('PE',f'{h["pe_pct"]}%',h['pe_ok'],''),('PB',f'{h["pb_pct"]}%',h['pb_ok'],''),
        ('ROE',f'{h["roe_stocks"]}只',h['roe_ok'],f'<span class="dim"> {h["roe_date"]}</span>'),('DB',f'{h["db"]} MB',True,'')])

    breadth_html='<div class="empty">计算中...</div>'
    if bd:
        latest=bd[-1]
        breadth_html=f'''<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;text-align:center">
        <div class="metric"><div class="l">上涨/下跌</div><div class="v"><span class="up">{latest['adv']}</span>/<span class="dn">{latest['decl']}</span></div></div>
        <div class="metric"><div class="l">ADR</div><div class="v {"up" if latest["adv"]>latest["decl"] else "dn"}">{f"{latest['adv']/latest['decl']:.2f}" if latest["decl"] else "∞"}</div></div>
        <div class="metric"><div class="l">MA20以上</div><div class="v">{latest["a20"]}%</div></div>
        <div class="metric"><div class="l">MA60以上</div><div class="v">{latest["a60"]}%</div></div></div>'''

    pos_html='<div class="empty">无持仓</div>'
    if ps:pos_html=''.join(f'<div class="pos-item"><span class="pos-code {"up" if p["pnl"]>=0 else "dn"}">{p["code"]}</span><span class="pos-meta">{p["name"]} · 买 ¥{p["buy"]}<br>现 ¥{p["now"]} · 止损 ¥{p["stop"]}</span><span class="pos-pnl {"up" if p["pnl"]>=0 else "dn"}">{"+" if p["pnl"]>=0 else ""}{p["pnl"]}%</span></div>'for p in ps)

    factor_html='<div class="empty">暂无数据</div>'
    if fs:factor_html='<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th class="ta-r">现价</th><th class="ta-r">得分</th><th class="ta-r">动量</th></tr></thead><tbody>'+''.join(f'<tr><td>{f["r"]}</td><td class="code">{f["code"]}</td><td>{f["name"]}</td><td class="ta-r">¥{f["price"]:.2f}</td><td class="ta-r"><span class="bl fw">{f["score"]:.1f}</span></td><td class="ta-r {"up" if f["mom"]>=0 else "dn"}">{"+" if f["mom"]>=0 else ""}{f["mom"]}</td></tr>'for f in fs[:15])+'</tbody></table>'

    sec_html='<div class="empty">板块数据收集中<br><small>需 report.py 积累 ≥2 天数据</small></div>'
    if sec:sec_html=f'<div class="dim" style="margin-bottom:8px">{sec["date"]}</div><div class="dim" style="margin-bottom:4px">▲ 最强</div>'+''.join(f'<div class="row-item"><span>{s["n"]}</span><span class="up">+{s["pct"]}%</span></div>'for s in sec['top'])+'<div class="dim" style="margin:10px 0 4px">▼ 最弱</div>'+''.join(f'<div class="row-item"><span>{s["n"]}</span><span class="dn">{s["pct"]}%</span></div>'for s in sec['bottom'])

    sig_html='<div class="empty">暂无信号</div>'
    if sigs:
        gb={}
        for s in sigs:
            if s['d']not in gb:gb[s['d']]=[]
            gb[s['d']].append(s)
        parts=[]
        for date,slist in list(gb.items())[:4]:
            rows=''.join(f'<div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:11px"><span class="code">{s["c"]}</span><span class="dim" style="font-size:10px;min-width:36px">{s["n"]}</span><span class="dim flex-1" style="font-size:10px">{s["s"]}</span>{_tag("t-buy" if s["a"]=="BUY" else "t-sell",s["a"])}<span class="ta-r" style="min-width:45px">¥{s["p"]}</span>{_tag("t-pass" if s["st"]=="passed" else "t-block",s["st"])}</div>'for s in slist[:12])
            parts.append(f'<div style="font-size:11px;color:var(--d);padding:4px 0;font-weight:600;border-bottom:1px solid var(--b);margin:6px 0 3px">{date} ({len(slist)}条)</div>{rows}')
        sig_html=''.join(parts)

    # 策略迷你卡片
    colors=[('#4a9eff','#4a9eff18'),('#0ecb81','#0ecb8118'),('#a85cef','#a85cef18')]
    mini_cards=[]
    for i,s in enumerate(STRATS):
        st=_tag("t-trend" if s["style"]=="trend" else "t-rev","趋势" if s["style"]=="trend" else"反转")
        c=colors[i]
        mini_cards.append(
            '<div class="strat-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px"><span class="fw">{s["n"]}</span><span class="dim">{s["p"]}</span></div>'
            f'<div style="display:flex;gap:16px;align-items:center"><div><span style="font-size:20px;font-weight:700;color:{c[0]}">{s["ret"]}%</span><span class="dim" style="font-size:10px"> 年化</span></div>'
            f'<div class="flex-1" style="font-size:10px">'
            f'<div class="bar-row"><span>夏普</span><div class="bar"><div class="bar-f" style="width:{min(s["sharpe"]*100,100):.0f}%;background:{c[0]}"></div></div><span>{s["sharpe"]:.2f}</span></div>'
            f'<div class="bar-row"><span>回撤</span><div class="bar"><div class="bar-f" style="width:{min(s["dd"],100):.0f}%;background:#f6465d"></div></div><span>{s["dd"]:.0f}%</span></div>'
            f'<div class="bar-row"><span>胜率</span><div class="bar"><div class="bar-f" style="width:{s["win"]:.0f}%;background:#f0b90b"></div></div><span>{s["win"]}%</span></div></div></div>'
            f'<div class="dim" style="font-size:10px;margin-top:4px">{st} {s["months"]}月交易 · {s["desc"]}</div></div>')
    strat_mini=''.join(mini_cards)

    body=f'''
    <div class="grid g4" style="margin-bottom:10px">
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">CSI300</div><div class="v">{m["close"]:.0f}</div><div class="s {reg_cls}">{reg_label}</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">5日</div><div class="v {"up" if m["r5"]>=0 else "dn"}">{"+" if m["r5"]>=0 else ""}{m["r5"]}%</div><div class="s dim">20日 {"+" if m["r20"]>=0 else ""}{m["r20"]}%</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">60日</div><div class="v {"up" if m["r60"]>=0 else "dn"}">{"+" if m["r60"]>=0 else ""}{m["r60"]}%</div><div class="s dim">{m["stocks"]}只 · {m["date"]}</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">涨跌比 20日</div><div class="v"><span class="up">{m["up"]}</span>/<span class="dn">{m["down"]}</span></div><div class="s dim">{"偏多" if m["up"]>m["down"] else "偏空"}</div></div></div></div>
    </div>
    <div class="grid g2">
      <div class="panel"><div class="panel-hd">策略回测对比 <a href="strategy.html" style="color:var(--bl);font-size:10px;text-decoration:none">详情 →</a></div><div class="panel-bd">{strat_mini}</div></div>
      <div class="panel"><div class="panel-hd">多因子排名 Top15</div><div class="panel-bd" style="max-height:340px;overflow-y:auto">{factor_html}</div></div>
    </div>
    <div class="grid g3" style="margin-top:10px">
      <div class="panel"><div class="panel-hd">数据健康</div><div class="panel-bd">{health}</div></div>
      <div class="panel"><div class="panel-hd">持仓</div><div class="panel-bd">{pos_html}</div></div>
      <div class="panel"><div class="panel-hd">市场广度 <a href="market.html" style="color:var(--bl);font-size:10px;text-decoration:none">详情 →</a></div><div class="panel-bd">{breadth_html}</div></div>
    </div>
    <div class="grid g2" style="margin-top:10px">
      <div class="panel"><div class="panel-hd">板块</div><div class="panel-bd">{sec_html}</div></div>
      <div class="panel"><div class="panel-hd">最近信号 <a href="signals.html" style="color:var(--bl);font-size:10px;text-decoration:none">全部 →</a></div><div class="panel-bd" style="max-height:300px;overflow-y:auto">{sig_html}</div></div>
    </div>'''
    return _page('仪表盘','index.html',body)

def page_market(m,bd,sec):
    """市场监控页"""
    reg_cls='up'if m['regime']=='strong'else'dn'
    reg_label='TRENDING ↑'if m['regime']=='strong'else'RANGE ↓'

    # 市场状态建议
    if m['regime']=='strong':
        advice='<strong>强势趋势</strong>：趋势策略（均线、突破）优先，止损可放宽至8%。均值回归降权——趋势中"超买"可以一直持续。'
    else:
        advice='<strong>弱势/震荡</strong>：均值回归策略优先，趋势策略降权。止损收紧至5%。减少新开仓，优先管理现有持仓。'

    # 广度趋势迷你柱状图（纯CSS）
    bars=''
    if bd:
        max_total=max((x['adv']+x['decl'])for x in bd[-20:])if bd else 1
        bars='<div style="display:flex;align-items:flex-end;gap:1px;height:80px;margin:8px 0">'+''.join(
            f'<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end" title="{x["d"]}: {x["adv"]}↑{x["decl"]}↓">'
            f'<div style="background:var(--up);height:{x["adv"]/max_total*80:.0f}px;min-height:1px;border-radius:1px 1px 0 0"></div>'
            f'<div style="background:var(--dn);height:{x["decl"]/max_total*80:.0f}px;min-height:1px"></div></div>'
            for x in bd[-60:])+'</div>'

    # 广度数据表
    bd_table='<table><thead><tr><th>日期</th><th class="ta-r">上涨</th><th class="ta-r">下跌</th><th class="ta-r">ADR</th><th class="ta-r">MA20↑%</th><th class="ta-r">MA60↑%</th></tr></thead><tbody>'+''.join(
        f'<tr><td>{x["d"]}</td><td class="ta-r up">{x["adv"]}</td><td class="ta-r dn">{x["decl"]}</td>'
        f'<td class="ta-r {"up" if x["adv"]>x["decl"] else "dn"}">{(f"{x["adv"]/x["decl"]:.2f}" if x["decl"] else "∞")}</td>'
        f'<td class="ta-r">{x["a20"]}%</td><td class="ta-r">{x["a60"]}%</td></tr>'
        for x in reversed(bd[-15:]))+'</tbody></table>'if bd else'<div class="empty">暂无数据</div>'

    # 板块
    sec_html='<div class="empty">板块数据收集中</div>'
    if sec:
        sec_html=f'<div class="dim" style="margin-bottom:8px">{sec["date"]}</div>'
        sec_html+='<div class="dim" style="margin-bottom:4px">▲ 最强5板块</div>'+''.join(f'<div class="row-item"><span>{s["n"]}</span><span class="up">+{s["pct"]}%</span></div>'for s in sec['top'])
        sec_html+='<div class="dim" style="margin:12px 0 4px">▼ 最弱5板块</div>'+''.join(f'<div class="row-item"><span>{s["n"]}</span><span class="dn">{s["pct"]}%</span></div>'for s in sec['bottom'])

    body=f'''
    <div class="hero"><h2>📈 市场监控</h2><p>数据日期: {m["date"]} · 覆盖 {m["stocks"]} 只股票 · CSI300 {m["close"]:.0f} <span class="{reg_cls}">{reg_label}</span></p></div>

    <div class="grid g4" style="margin-bottom:10px">
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">CSI300</div><div class="v">{m["close"]:.0f}</div><div class="s {reg_cls}">{reg_label}</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">5日</div><div class="v {"up" if m["r5"]>=0 else "dn"}">{"+" if m["r5"]>=0 else ""}{m["r5"]}%</div><div class="s dim">20日 {"+" if m["r20"]>=0 else ""}{m["r20"]}%</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">60日</div><div class="v {"up" if m["r60"]>=0 else "dn"}">{"+" if m["r60"]>=0 else ""}{m["r60"]}%</div><div class="s dim">60日涨跌</div></div></div></div>
      <div class="panel"><div class="panel-bd"><div class="metric"><div class="l">涨跌比 20日</div><div class="v"><span class="up">{m["up"]}</span>/<span class="dn">{m["down"]}</span></div><div class="s dim">{"偏多" if m["up"]>m["down"] else "偏空"}</div></div></div></div>
    </div>

    <div class="panel"><div class="panel-hd">市场状态建议</div><div class="panel-bd"><div class="explain">{advice}</div></div></div>

    <div class="grid g2">
      <div class="panel"><div class="panel-hd">涨跌家数趋势（近60交易日，绿涨红跌）</div><div class="panel-bd">{bars}</div></div>
      <div class="panel"><div class="panel-hd">板块热度</div><div class="panel-bd">{sec_html}</div></div>
    </div>

    <div class="panel"><div class="panel-hd">市场广度历史（近15个交易日）</div><div class="panel-bd">{bd_table}</div></div>
    '''
    return _page('市场监控','market.html',body)

def page_strategy(sigs):
    """策略分析页"""
    # 为每个策略填充最近信号示例
    for s in STRATS:
        name_map={'ma':'双均线','mb':'动量突破','mr':'均值回归'}
        s['signals']=_signal_for_strategy(conn,name_map[s['key']])

    cards=''
    colors=[('#4a9eff','#4a9eff14'),('#0ecb81','#0ecb8114'),('#a85cef','#a85cef14')]
    for i,s in enumerate(STRATS):
        # 最近信号
        sig_part='<div class="dim" style="font-size:10px">暂无最近信号</div>'
        if s['signals']:
            sig_part='<div style="font-size:10px;margin-top:4px"><span class="dim">最近信号: </span>'+''.join(
                f'{_tag("t-buy" if sig["a"]=="BUY" else "t-sell",sig["a"])} {sig["c"]} {sig["n"]} ¥{sig["p"]} <span class="dim">({sig["d"]})</span> '
                for sig in s['signals'][:3])+'</div>'

        # Walk-Forward 表
        wf_rows=''.join(f'<tr><td>{r[0]}</td><td class="ta-r">{r[1]}</td><td class="ta-r">{r[2]}</td><td class="ta-r">{r[3]}</td><td class="ta-r">{r[4]}</td></tr>'for r in s['wf'])

        cards+=f'''<div class="strat-card" style="border-left:3px solid {colors[i][0]}">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
      <span style="font-size:15px;font-weight:700">{s['n']}</span>
      <span>{_tag("t-trend" if s["style"]=="trend" else "t-rev","趋势" if s["style"]=="trend" else"反转")} <span class="dim">{s['p']}</span></span></div>

    <div class="explain"><strong>原理：</strong>{s['principle']}</div>
    <div class="explain" style="margin-top:6px"><strong>适合：</strong><span class="up">{s['good_for']}</span></div>
    <div class="explain"><strong>不适合：</strong><span class="dn">{s['bad_for']}</span></div>
    <div class="explain" style="margin-top:6px"><strong>为什么选这个参数：</strong>{s['why_params']}</div>

    <div style="display:flex;gap:20px;align-items:center;margin:12px 0;padding:10px;background:var(--s2);border-radius:6px">
      <div><span style="font-size:22px;font-weight:700;color:{colors[i][0]}">{s['ret']}%</span><span class="dim" style="font-size:10px;margin-left:3px">年化</span></div>
      <div class="flex-1" style="font-size:10px">
      <div class="bar-row"><span>夏普</span><div class="bar"><div class="bar-f" style="width:{min(s['sharpe']*100,100):.0f}%;background:{colors[i][0]}"></div></div><span>{s['sharpe']:.2f}</span></div>
      <div class="bar-row"><span>回撤</span><div class="bar"><div class="bar-f" style="width:{min(s['dd'],100):.0f}%;background:#f6465d"></div></div><span>{s['dd']:.0f}%</span></div>
      <div class="bar-row"><span>胜率</span><div class="bar"><div class="bar-f" style="width:{s['win']:.0f}%;background:#f0b90b"></div></div><span>{s['win']}%</span></div>
      <div class="bar-row"><span>交易</span><div class="bar"><div class="bar-f" style="width:{min(s['months'],100):.0f}%;background:var(--d)"></div></div><span>{s['months']}月</span></div>
      </div></div>

    <div style="margin-top:10px"><span class="dim" style="font-size:10px;text-transform:uppercase;letter-spacing:.3px">Walk-Forward 验证</span>
    <table style="margin-top:4px"><thead><tr><th>周期</th><th class="ta-r">年化</th><th class="ta-r">夏普</th><th class="ta-r">回撤</th><th class="ta-r">交易</th></tr></thead><tbody>{wf_rows}</tbody></table></div>
    {sig_part}
    </div>'''

    # 对比表
    compare='<table><thead><tr><th>维度</th><th>双均线</th><th>动量突破</th><th>均值回归</th></tr></thead><tbody>'+''.join(
        f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>'for r in[
        ('信号逻辑','均线交叉','价格突破','超卖反弹'),('信号频率','低（月1-2次）','中','中'),
        ('适合市场','<span class="up">趋势</span>','<span class="up">强势趋势</span>','<span class="up">震荡</span>'),
        ('回撤特征','中等(-32%)','<span class="dn">高(-61%)</span>','较低(-40%)'),
        ('最大弱点','滞后——入场晚','假突破频繁','趋势中逆势')]
    )+'</tbody></table>'

    body=f'''
    <div class="hero"><h2>📋 策略分析</h2><p>三个策略的完整讲解——每个策略做什么、为什么选这个参数、在不同市场环境下表现如何。</p></div>
    {cards}
    <div class="panel"><div class="panel-hd">策略对比总览</div><div class="panel-bd">{compare}</div></div>
    '''
    return _page('策略分析','strategy.html',body)

# ═══════════════ MAIN ═══════════════

def build():
    global conn
    conn=sqlite3.connect(DB)
    ts=datetime.now().strftime('%Y-%m-%d %H:%M')

    m=_market(conn);h=_health(conn);bd=_breadth(conn)
    ps=_positions(conn);fs=_factors(conn);sigs=_signals(conn);sec=_sectors(conn)

    os.makedirs('dashboard',exist_ok=True)

    pages=[
        ('index.html',page_index(m,h,bd,ps,fs,sigs,sec)),
        ('market.html',page_market(m,bd,sec)),
        ('strategy.html',page_strategy(sigs)),
    ]

    for fn,html in pages:
        path=f'dashboard/{fn}'
        with open(path,'w',encoding='utf-8')as f:f.write(html)
        print(f'✅ {path} ({len(html):,} bytes)')

    conn.close()
    print(f'   → 打开 dashboard/index.html · {ts}')

if __name__=='__main__':
    build()
