"""Slow Perps: a slower long + short perps strategy. BACKTEST + VERDICT ONLY: no paper book, no real money.
The rules below were written down before any test and are never tuned on the results (pre-registered):
  candles  daily (Coinbase UTC days) and, judged on its own, 4-hour candles built from the stored 1-hour candles
  coins    BTC ETH SOL XRP DOGE LINK UNI ATOM (all are Hyperliquid perps)
  LONG     a candle closes above its 50-candle average, the 20-candle average is above the 50, and the close is above
           the highest close of the 20 candles before it
  SHORT    the mirror: below the 50 average, 20 below 50, close under the lowest close of the 20 before
  entry    the next candle's open (+ slippage); skipped when that open already ran more than 5% past the signal close,
           or is already beyond the stop
  stop     3 x ATR(14) from the signal close, then it trails: best close since entry - 3 x ATR (only ever tightens);
           checked against each candle's low/high, a gap through the stop fills at the open (pessimistic)
  exit     also when a candle closes back across the 50 average (filled at the next open)
  size     1 position per coin; risk 1% of that coin's book per trade (the live charter would be 0.25%, results
           scale), at most 3x leverage
  costs    Hyperliquid taker 0.045% + slippage 0.02% per side; funding 0.01% per 8 h charged to longs, shorts are
           never paid funding (conservative). Stress run: fees and slippage x3, longs pay 3x funding, shorts pay 1x.
The evidence bar (evidence.py, the same bar as every strategy): held-back Sharpe > 1.5 (last 25% of the time, an
equal-weight basket of the coin books), profit factor with fees x3 >= 1.3, Monte Carlo 95% drawdown <= 25%, >= 60% of
the 6 neighbour settings also pass, >= 60% of the 90-day walk-forward windows make money, >= 30 trades.
Each timeframe is judged on its own; one that passes still only earns a paper book, and only if the owner asks.
Honest limits (also in the report): Coinbase spot prices stand in for perps prices; funding is an average; no
narrative filter (none can be tested on the past)."""
import time,math,logging,threading,statistics as st
import evidence as EV,candles
from tgfmt import term,kv,SEP,wrap
log=logging.getLogger("bot");H=3600;DAY=86400;H4=4*H;KEY="backtest:slowperps"
COINS={"BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","XRP":"XRP-USD","DOGE":"DOGE-USD","LINK":"LINK-USD","UNI":"UNI-USD","ATOM":"ATOM-USD"}
DEF=dict(sma_fast=20,sma_slow=50,breakout=20,atr_n=14,atr_mult=3.0,max_chase=0.05,risk_pct=0.01,max_lev=3.0)
COST=dict(fee=0.00045,slip=0.0002,fund_h=0.0000125)
NEIGHBOURS=(("atr_mult",2.5),("atr_mult",3.5),("breakout",15),("breakout",30),("sma_slow",40),("sma_slow",60))
MIN_CANDLES={DAY:300,H4:1800}
TFNAME={DAY:"daily",H4:"4-hour"}
# ---- indicators (closed candles only) ---------------------------------------------------------------------------
def sma(xs,n):
    out=[None]*len(xs);s=0.0
    for i,x in enumerate(xs):
        s+=x
        if i>=n:s-=xs[i-n]
        if i>=n-1:out[i]=s/n
    return out
def atr(rows,n):
    """Wilder's ATR on (ts,o,h,l,c,v) rows."""
    out=[None]*len(rows);a=None;tr=[]
    for i in range(1,len(rows)):
        h,l,pc=rows[i][2],rows[i][3],rows[i-1][4];x=max(h-l,abs(h-pc),abs(l-pc))
        if a is None:
            tr.append(x)
            if len(tr)==n:a=sum(tr)/n
        else:a=(a*(n-1)+x)/n
        out[i]=a
    return out
def resample(rows,tf=H4,src=H):
    """1-hour rows -> tf rows aligned to UTC (00, 04, 08 ...). A bucket needs at least 3/4 of its hours."""
    B={}
    for r in rows:B.setdefault(r[0]//tf*tf,[]).append(r)
    out=[]
    for t in sorted(B):
        x=sorted(B[t])
        if len(x)<0.75*tf/src:continue
        out.append((t,x[0][1],max(r[2] for r in x),min(r[3] for r in x),x[-1][4],sum(r[5] for r in x)))
    return out
def prep(rows,p):
    c=[r[4] for r in rows];return dict(c=c,f=sma(c,p["sma_fast"]),s=sma(c,p["sma_slow"]),a=atr(rows,p["atr_n"]))
def signal(X,i,p):
    """-> +1 long, -1 short, 0 nothing, judged at the close of candle i."""
    c,f,s,a=X["c"],X["f"],X["s"],X["a"]
    if i<max(p["sma_slow"],p["breakout"]+1,p["atr_n"]+1) or None in(f[i],s[i],a[i]):return 0
    hi=max(c[i-p["breakout"]:i]);lo=min(c[i-p["breakout"]:i])
    if c[i]>s[i] and f[i]>s[i] and c[i]>hi:return 1
    if c[i]<s[i] and f[i]<s[i] and c[i]<lo:return -1
    return 0
# ---- one coin, one book -------------------------------------------------------------------------------------------
def run(rows,p,tf,fee=COST["fee"],slip=COST["slip"],fund_long=COST["fund_h"],fund_short=0.0,equity=1000.0,start_ts=None,end_ts=None,X=None):
    """Simulates one coin's book. fund_long/fund_short: share of the position value paid per hour (a negative value
    would be income; the defaults never assume income)."""
    X=X or prep(rows,p);eq=equity;pos=None;pend=None;out_next=False;T=[];curve=[];k=p["atr_mult"]
    def close(px,why,t):
        nonlocal eq,pos
        d=pos["d"];g=d*(px-pos["entry"])*pos["size"]-px*pos["size"]*fee;eq+=g;pnl=pos["realized"]+g
        T.append(dict(t_in=pos["t"],t_out=t,pnl=pnl,ret=pnl/pos["eq0"],why=why,d=d,lev=pos["lev"]));pos=None
    for i,(t,o,h,l,c,v) in enumerate(rows):
        if end_ts and t>=end_ts:break
        if start_ts and t<start_ts:continue
        if pos and out_next:close(o*(1-pos["d"]*slip),"back across the 50 average",t)
        out_next=False
        if pend and not pos:
            d=pend["d"];stop=pend["close"]-d*k*pend["atr"];px=o*(1+d*slip)
            if d*(o/pend["close"]-1)<=p["max_chase"] and d*(px-stop)>0:
                sd=abs(px-stop)/px;notional=min(eq*p["risk_pct"]/(sd+2*fee+2*slip),eq*p["max_lev"])
                eq-=notional*fee
                pos=dict(d=d,entry=px,size=notional/px,stop=stop,best=pend["close"],t=t,eq0=eq+notional*fee,realized=-notional*fee,lev=notional/max(eq,1e-9))
        pend=None
        if pos:
            d=pos["d"];fh=fund_long if d>0 else fund_short;f=pos["size"]*o*fh*tf/H;eq-=f;pos["realized"]-=f
            if d*(o-pos["stop"])<=0:close(o*(1-d*slip),"stop (gap)",t)
            elif d*((l if d>0 else h)-pos["stop"])<=0:close(pos["stop"]*(1-d*slip),"stop",t)
        if pos:
            d=pos["d"];pos["best"]=max(pos["best"],c) if d>0 else min(pos["best"],c)
            if X["a"][i]:
                tr=pos["best"]-d*k*X["a"][i]
                if d*(tr-pos["stop"])>0:pos["stop"]=tr
            if X["s"][i] is not None and d*(c-X["s"][i])<0:out_next=True
        if not pos:
            d=signal(X,i,p)
            if d:pend=dict(d=d,close=c,atr=X["a"][i])
        curve.append((t+tf,eq+(pos["d"]*(c-pos["entry"])*pos["size"] if pos else 0)))
        if eq<=equity*0.02:break                                       # ruined
    if pos:close(rows[min(i,len(rows)-1)][4],"end of data",rows[min(i,len(rows)-1)][0]+tf)
    return dict(trades=T,curve=curve,start=equity,end=eq)
def metrics(r):
    T=r["trades"];P=[x["pnl"] for x in T];pk=r["start"];dd=0.0
    for _,e in r["curve"]:pk=max(pk,e);dd=max(dd,(pk-e)/pk if pk>0 else 1.0)
    yrs=max(1/12,(r["curve"][-1][0]-r["curve"][0][0])/(365*DAY)) if r["curve"] else 1
    return dict(trades=len(T),per_year=len(T)/yrs,win_rate=sum(1 for x in P if x>0)/len(T) if T else None,pf=EV.pf(P),ret=r["end"]/r["start"]-1,max_dd=dd,
                longs=sum(1 for x in T if x["d"]>0),shorts=sum(1 for x in T if x["d"]<0))
def stress(p,tf,rows,X=None,**kw):
    return run(rows,p,tf,fee=COST["fee"]*3,slip=COST["slip"]*3,fund_long=COST["fund_h"]*3,fund_short=COST["fund_h"],X=X,**kw)
def full(data,tf=DAY,p=None,split=0.75):
    """data: {coin: rows of this timeframe}. -> the whole evidence-bar result for one timeframe."""
    p=dict(DEF,**(p or {}));coins=[k for k in data if len(data[k])>=MIN_CANDLES.get(tf,300)]
    if not coins:return dict(error=f"not enough {TFNAME.get(tf,'')} history",tf=tf)
    t0=min(data[k][0][0] for k in coins);t1=max(data[k][-1][0] for k in coins)+tf;cut=int(t0+(t1-t0)*split)//tf*tf
    C={};allT=[];oos=[];p3=[]
    for k in coins:
        X=prep(data[k],p);a=run(data[k],p,tf,X=X);ho=run(data[k],p,tf,start_ts=cut,X=X);x3=stress(p,tf,data[k],X=X)
        C[k]=dict(all=metrics(a),holdout=metrics(ho),x3=metrics(x3),first=data[k][0][0])
        allT+=[dict(x,coin=k) for x in a["trades"]];p3+=[x["pnl"] for x in x3["trades"]];oos.append(EV.daily_returns(ho["curve"]))
    n=min(len(x) for x in oos) if oos else 0;bk=[sum(x[-n+j] for x in oos)/len(oos) for j in range(n)] if n else []
    sh=EV.sharpe(bk);mc=EV.monte_carlo([x["ret"] for x in allT])
    nb={}
    for k_,v_ in NEIGHBOURS:
        pp=dict(p,**{k_:v_});pn=[x["pnl"] for k in coins for x in stress(pp,tf,data[k])["trades"]]
        nb[f"{k_}={v_:g}"]=dict(pf=EV.pf(pn),pnl=sum(pn),trades=len(pn))
    share,_=EV.plateau(nb,lambda m:(m["pf"] or 0)>=1.3 and m["pnl"]>0)
    W,wf=EV.walk_forward([dict(t_out=x["t_out"],ret=x["ret"]) for x in allT],t0,t1,90)
    checks,ok=EV.verdict(sh,EV.pf(p3),mc,len(allT),share,wf)
    L=[x["pnl"] for x in allT if x["d"]>0];S=[x["pnl"] for x in allT if x["d"]<0]
    return dict(tf=tf,ran=time.time(),from_ts=t0,to_ts=t1,cut_ts=cut,coins=C,trades=len(allT),pf=EV.pf([x["pnl"] for x in allT]),pf_x3=EV.pf(p3),
                pf_long=EV.pf(L),pf_short=EV.pf(S),longs=len(L),shorts=len(S),sharpe_oos=sh,mc=mc,plateau=nb,plateau_share=share,wf_share=wf,
                wf_windows=len([w for w in W if w["trades"]]),checks=checks,passed=ok,
                ret_basket=st.mean([C[k]["all"]["ret"] for k in coins]),
                notes=["Coinbase spot prices stand in for perps prices (the small perps-spot gap is ignored).",
                       "Funding is an average (0.01% per 8 h) charged to longs; shorts are never paid funding.",
                       "No narrative filter: there is no point-in-time CT/news history to test it on.",
                       "XRP on Coinbase starts July 2023 (delisted 2021-2023)."])
# ---- plain words ------------------------------------------------------------------------------------------------
PLAIN={"trades":"enough trades to judge","Sharpe, held-back":"good on the newest 25% it never saw",
       "PF, fees x3":"still profitable with triple costs","Monte Carlo 95% drawdown":"drawdown stays small when luck is reshuffled",
       "neighbour settings pass":"nearby settings work too (not a lucky pick)","walk-forward windows green":"most 3-month stretches make money"}
def plain(bt):
    """The verdict for a beginner: one point per line."""
    if bt.get("error"):return ["could not test: "+bt["error"]]
    nm=TFNAME.get(bt["tf"],"?");bad=[c for c in bt["checks"] if not c["ok"]]
    out=[f"{nm}: {'PASSED' if bt['passed'] else 'NOT passed'}"]
    if bt["passed"]:out+=["it cleared every check","next: a paper book, only","if you say yes. Real money","stays off."]
    else:
        out.append(f"failed {len(bad)} of {len(bt['checks'])} checks:")
        for c in bad:out+=wrap("- "+PLAIN.get(c["name"],c["name"]),"","  ")
        out+=["so: no book is started","nothing changes in the bot"]
    return out
def report(bt):
    if bt.get("error"):return term("backtest slowperps",wrap(bt["error"]),("ERR","slow perps: no data"))
    f2=lambda v:"–" if v is None else ("∞" if v>=99 else f"{v:.2f}");pc=lambda v:"–" if v is None else f"{v:+.0%}"
    nm=TFNAME.get(bt["tf"],"?")
    rows=[kv("candles",nm),kv("period",time.strftime("%b %Y",time.gmtime(bt["from_ts"]))+" to "+time.strftime("%b %Y",time.gmtime(bt["to_ts"]))),
          kv("costs","0.065%/side + funding"),SEP,"coin trd/yr   PF  return  maxDD"]
    for k,c in bt["coins"].items():a=c["all"];rows.append(f"{k:<5}{a['per_year']:>6.1f}{f2(a['pf']):>6}{pc(a['ret']):>8}{a['max_dd']:>7.0%}")
    rows+=[f"all: {bt['trades']} trades, PF {f2(bt['pf'])}",f"longs:  {bt['longs']}, PF {f2(bt['pf_long'])}",f"shorts: {bt['shorts']}, PF {f2(bt['pf_short'])}",
           f"fees x3: PF {f2(bt['pf_x3'])}",SEP,"evidence bar:"]
    for c in bt["checks"]:
        v=c["value"];vs="–" if v is None else (f"{v:.0%}" if any(w in c["name"] for w in("drawdown","pass","green")) else f"{v:.2f}" if isinstance(v,float) else str(v))
        rows+=wrap(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']} {c['need']}");rows.append(f"     got {vs}")
    rows+=[SEP,"in plain words:"]+plain(bt)+[SEP]+sum((wrap(n,"· ") for n in bt["notes"]),[])+wrap("· backtest only: no paper book, no real money.")
    return term("slowperps --"+("4h" if bt["tf"]==H4 else "daily"),rows,("OK","✅ "+nm+": passed") if bt["passed"] else ("WARN","🐢 "+nm+": NOT passed"))
def summary(out):
    rows=[]
    for tf in(DAY,H4):
        bt=out.get(TFNAME[tf])
        if bt:rows+=plain(bt)+[SEP]
    rows+=["rules were fixed before the","test and never tuned.","everything stays paper.","/backtest slowperps last"]
    ok=any(b.get("passed") for b in out.values() if isinstance(b,dict))
    return term("slowperps --verdict",rows,("OK","✅ slow perps: one passed") if ok else ("WARN","🐢 slow perps: verdict"))
# ---- data + background run ----------------------------------------------------------------------------------
def load_data(d):
    """-> {daily: {coin: rows}, 4-hour: {coin: rows}} from Coinbase (stored in the candles table)."""
    D={};F={}
    for k,sym in COINS.items():
        try:candles.backfill(d,sym,DAY,start=candles.HISTORY_FROM);D[k]=candles.load(d,sym,DAY,since=candles.HISTORY_FROM)
        except Exception as e:log.warning("slowperps daily %s: %s",k,e)
        try:candles.backfill(d,sym,H,start=candles.HISTORY_FROM);F[k]=resample(candles.load(d,sym,H,since=candles.HISTORY_FROM),H4)
        except Exception as e:log.warning("slowperps 4h %s: %s",k,e)
    return {DAY:D,H4:F}
_lock=threading.Lock()
def run_async(db_path,notify,cfg=None):
    if not _lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);n=Notifier(d,bool(getattr(notify,"token","")));data=load_data(d);out={}
            for tf in(DAY,H4):
                bt=full(data[tf],tf);out[TFNAME[tf]]=bt;n.send(report(bt))
            d.kv_set(KEY,out);n.send(summary(out))
        except Exception as e:
            log.exception("slow perps backtest failed");notify.send(term("backtest slowperps",wrap(f"{type(e).__name__}: {str(e)[:160]}"),("ERR","slow perps backtest failed")))
        finally:_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
def auto_once(db,db_path,notify,version):
    """Runs the backtest once in the background after the update that added it (the verdict arrives in Telegram)."""
    if db.kv_get(KEY) or db.kv_get("slowperps:auto")==version:return False
    db.kv_set("slowperps:auto",version);return run_async(db_path,notify)
