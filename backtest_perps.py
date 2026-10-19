"""/backtest perps: the perps rules (perps.py) on years of 1-hour candles of XRP, DOGE, LINK, UNI, ATOM and SOL
(Coinbase spot USD prices as the long history; Hyperliquid keeps only ~200 days of hourly candles). Long AND short.
Honest limits, also printed in the report:
  - the narrative filter (rule zero) cannot be tested on the past: no point-in-time CT/news history exists, so the
    backtest judges the technical rules ALONE. Paper trading then judges them with the narrative filter.
  - funding is not in the spot history: an average funding cost is charged to longs (and paid to shorts), tripled
    in the stress run together with the fees.
Fills: entry at the NEXT candle's open after the signal candle closes; the stop and targets are checked against each
candle's high/low, the stop first when both are touched in the same hour (pessimistic); a gap beyond the stop fills at
the open. Two sizing modes: 'charter' (risk 1% of the book per trade, for readable numbers; the live charter is 0.25%,
results scale) and 'grok' (8x-19x, up to half the book as margin: liquidations simulated)."""
import time,math,logging,threading,statistics as st
import perps as PZ,evidence as EV,candles
from tgfmt import term,kv,SEP,wrap
log=logging.getLogger("bot");H=3600;KEY="backtest:perps"
COINS={"XRP":"XRP-USD","DOGE":"DOGE-USD","LINK":"LINK-USD","UNI":"UNI-USD","ATOM":"ATOM-USD","SOL":"SOL-USD"}
MAXLEV={"XRP":20,"DOGE":10,"LINK":10,"UNI":10,"ATOM":5,"SOL":20}   # assumption for the past; live uses Hyperliquid's meta
def prep(rows,p):
    c=[r[4] for r in rows];return dict(c=c,ef=PZ.ema(c,p["ema_fast"]),es=PZ.ema(c,p["ema_slow"]),a=PZ.atr(rows,p["atr_n"]))
def sig_at(rows,X,i,p,amean):
    c,ef,es,a=X["c"],X["ef"],X["es"],X["a"]
    if i<max(p["ema_slow"],p["breakout"]+1,p["atr_n"]+1) or a[i] is None or es[i] is None:return None
    cl=c[i];o=rows[i][1];gap=abs(ef[i]-es[i])/cl
    if gap<p["regime_min_gap"] or (amean and a[i]>p["vol_spike"]*amean):return None
    hi=max(c[i-p["breakout"]:i]);lo=min(c[i-p["breakout"]:i])
    d=1 if cl>es[i] and ef[i]>es[i] and cl>o and cl>hi else -1 if cl<es[i] and ef[i]<es[i] and cl<o and cl<lo else 0
    if not d:return None
    R=p["atr_mult"]*a[i];vr=rows[i][5]/max(st.mean([r[5] for r in rows[i-20:i]]),1e-12)
    sc=25*min(1,gap/0.02)+20*min(1,abs(cl-(hi if d==1 else lo))/a[i])+15*min(1,max(0,vr-1)/2)+10
    return dict(d=d,stop=cl-d*R,tp1=cl+d*R*p["tp1_r"],tp2=cl+d*R*p["tp2_r"],R=R,score=sc)
def run(rows,p,mode="charter",fee=0.00045,fund_h=0.0000125,slip=0.0002,equity=1000.0,risk_pct=0.01,start_ts=None,end_ts=None,coin="SOL",X=None,b=None):
    X=X or prep(rows,p);b=b or PZ.BOOK_DEF["perps-grok"];eq=equity;pos=None;pend=None;T=[];curve=[];asum=0.0;aq=[];liqs=0
    for i in range(len(rows)):
        t,o,h,l,c,v=rows[i]
        if X["a"][i] is not None:
            aq.append(X["a"][i]);asum+=X["a"][i]
            if len(aq)>720:asum-=aq.pop(0)
        if start_ts and t<start_ts:continue
        if end_ts and t>=end_ts:break
        if pend and not pos:                                      # entry at this candle's open
            sg=pend;d=sg["d"];px=o*(1+d*slip)
            if d*(px-sg["stop"])>0 and d*(sg["tp1"]-px)>0:
                sd=abs(px-sg["stop"])/px;td=abs(sg["tp1"]-px)/px
                if mode=="grok":
                    lev,_=PZ.choose_leverage(sd,td,MAXLEV.get(coin,10),b,p)
                    if lev:margin=eq*(b["margin_a_plus"] if sg["score"]>=p["a_plus"] else b["margin_a"]);notional=margin*lev
                    else:notional=0
                else:
                    notional=eq*risk_pct/(sd+2*fee+slip);lev=max(1,math.ceil(notional/(eq*0.25)));margin=notional/lev
                if notional>0:
                    ld=PZ.liq_dist(lev,MAXLEV.get(coin,10));eq-=notional*fee
                    pos=dict(d=d,entry=px,size=notional/px,notional=notional,margin=margin,lev=lev,stop=sg["stop"],tp1=sg["tp1"],tp2=sg["tp2"],
                             liq=px*(1-d*ld),t=t,tp1_done=False,risk=notional*(sd+2*fee),realized=-notional*fee)
        pend=None
        if pos:
            d=pos["d"];pos["realized"]-=d*pos["notional"]*fund_h;eq-=d*pos["notional"]*fund_h
            def close(px_,frac,why):
                nonlocal eq,pos
                g=d*(px_-pos["entry"])*pos["size"]*frac-px_*pos["size"]*frac*fee;eq+=g;pos["realized"]+=g
                if frac>=0.999:
                    T.append(dict(t_in=pos["t"],t_out=t,pnl=pos["realized"],ret=pos["realized"]/max(1e-9,eq-pos["realized"]),why=why,lev=pos["lev"],d=d));pos=None
                else:pos["size"]*=1-frac;pos["notional"]*=1-frac
            worst=l if d>0 else h;best=h if d>0 else l
            if d*(o-pos["liq"])<=0 or (d*(pos["liq"]-pos["stop"])>0 and d*(worst-pos["liq"])<=0):   # gap through the liquidation price (or it sits inside the stop)
                eq-=pos["margin"];liqs+=1;loss=-pos["margin"]
                T.append(dict(t_in=pos["t"],t_out=t,pnl=loss+pos["realized"],ret=(loss+pos["realized"])/max(1e-9,eq+pos["margin"]),why="liquidation",lev=pos["lev"],d=d));pos=None
            elif d*(o-pos["stop"])<=0:close(o,1.0,"stop")
            elif d*(worst-pos["stop"])<=0:close(pos["stop"]*(1-d*slip),1.0,"breakeven" if pos["tp1_done"] else "stop")
            else:
                if not pos["tp1_done"] and d*(best-pos["tp1"])>=0:
                    close(pos["tp1"],p["tp1_portion"],"tp1");pos["tp1_done"]=True;pos["stop"]=pos["entry"]*(1+d*2*fee)
                if pos and d*(best-pos["tp2"])>=0:close(pos["tp2"],1.0,"tp2")
                elif pos and t-pos["t"]>=p["max_hold_h"]*H:close(c,1.0,"time")
        if not pos and pend is None:
            sg=sig_at(rows,X,i,p,asum/len(aq) if aq else None)
            if sg:pend=sg
        curve.append((t+H,eq+((pos["d"]*(c-pos["entry"])*pos["size"]) if pos else 0)))
        if eq<=equity*0.02:break                                    # ruined
    if pos:
        g=pos["d"]*(rows[min(i,len(rows)-1)][4]-pos["entry"])*pos["size"]-pos["notional"]*fee;eq+=g
        T.append(dict(t_in=pos["t"],t_out=rows[min(i,len(rows)-1)][0],pnl=pos["realized"]+g,ret=(pos["realized"]+g)/max(1e-9,eq),why="end",lev=pos["lev"],d=pos["d"]))
    return dict(trades=T,curve=curve,start=equity,end=eq,liquidations=liqs)
def metrics(r):
    T=r["trades"];P=[x["pnl"] for x in T];w=[x for x in P if x>0];pk=r["start"];dd=0
    for _,e in r["curve"]:pk=max(pk,e);dd=max(dd,(pk-e)/pk if pk>0 else 1)
    return dict(trades=len(T),win_rate=len(w)/len(T) if T else None,pf=EV.pf(P),ret=r["end"]/r["start"]-1,max_dd=dd,liquidations=r["liquidations"],
        longs=sum(1 for x in T if x["d"]>0),shorts=sum(1 for x in T if x["d"]<0),per_month=len(T)/max(1,(r["curve"][-1][0]-r["curve"][0][0])/(30*86400)) if r["curve"] else 0)
def full(data,p=None,mode="charter",fee=0.00045,fund_h=0.0000125,split=0.75):
    p=dict(PZ.DEF,**(p or {}));coins=[k for k in data if len(data[k])>2000]
    if not coins:return dict(error="not enough hourly history",mode=mode)
    t0=min(data[k][0][0] for k in coins);t1=max(data[k][-1][0] for k in coins)+H;cut=int(t0+(t1-t0)*split)//H*H
    C={};allT=[];oos=[];p3=[]
    for k in coins:
        X=prep(data[k],p)
        a=run(data[k],p,mode,fee,fund_h,coin=k,X=X);ho=run(data[k],p,mode,fee,fund_h,start_ts=cut,coin=k,X=X);x3=run(data[k],p,mode,fee*3,fund_h*3,coin=k,X=X)
        C[k]=dict(all=metrics(a),holdout=metrics(ho),x3=metrics(x3),first=data[k][0][0]);allT+=[dict(x,coin=k) for x in a["trades"]];p3+=[x["pnl"] for x in x3["trades"]]
        oos.append(EV.daily_returns(ho["curve"]))
    # held-back Sharpe of an equal-weight basket of the coin books
    n=min(len(x) for x in oos) if oos else 0;bk=[sum(x[-n+j] for x in oos)/len(oos) for j in range(n)] if n else []
    sh=EV.sharpe(bk);mc=EV.monte_carlo([x["ret"] for x in allT])
    nb={}
    for k_,v_ in(("atr_mult",1.0),("atr_mult",2.0),("breakout",10),("breakout",30),("tp2_r",1.5),("tp2_r",3.0)):
        pp=dict(p,**{k_:v_});pn=[x["pnl"] for k in coins for x in run(data[k],pp,mode,fee*3,fund_h*3,coin=k)["trades"]];nb[f"{k_}={v_}"]=dict(pf=EV.pf(pn),pnl=sum(pn),trades=len(pn))
    share,_=EV.plateau(nb,lambda m:(m["pf"] or 0)>=1.3 and m["pnl"]>0)
    W,wf=EV.walk_forward([dict(t_out=x["t_out"],ret=x["ret"]) for x in allT],t0,t1,90)
    checks,ok=EV.verdict(sh,EV.pf(p3),mc,len(allT),share,wf,bar=dict(mc_dd95=0.25 if mode=="charter" else 0.6))
    return dict(mode=mode,ran=time.time(),from_ts=t0,to_ts=t1,cut_ts=cut,fee=fee,fund_h=fund_h,coins=C,trades=len(allT),pf=EV.pf([x["pnl"] for x in allT]),pf_x3=EV.pf(p3),
        sharpe_oos=sh,mc=mc,plateau=nb,plateau_share=share,wf_share=wf,checks=checks,passed=ok,liquidations=sum(C[k]["all"]["liquidations"] for k in coins),
        notes=["The narrative filter cannot be tested on the past (no point-in-time CT/news history): technical rules only.",
               "Funding is an average assumption (0.01%/8h against longs), tripled with the fees in the stress run.",
               "XRP history on Coinbase starts in July 2023 (it was delisted 2021-2023)."+("" if mode=="charter" else " Liquidations are simulated at the coin's assumed max leverage.")])
def report(bt):
    if bt.get("error"):return term("backtest perps",wrap(bt["error"]),("ERR","perps backtest failed"))
    f2=lambda v:"–" if v is None else ("∞" if v>=99 else f"{v:.2f}");pc=lambda v:"–" if v is None else f"{v:+.1%}"
    rows=[kv("mode",bt["mode"]+(" (0.25-1% risk)" if bt["mode"]=="charter" else " (8-19x, ≤50% margin)")),
          kv("period",time.strftime("%b %Y",time.gmtime(bt["from_ts"]))+" to "+time.strftime("%b %Y",time.gmtime(bt["to_ts"]))),
          kv("fees",f"{bt['fee']:.3%}/side"),SEP,"coin trd/mo   PF  return  maxDD"]
    for k,c in bt["coins"].items():a=c["all"];rows.append(f"{k:<5}{a['per_month']:>6.1f}{f2(a['pf']):>6}{pc(a['ret']):>8}{a['max_dd']:>7.0%}")
    rows+=[f"all: {bt['trades']} trades, PF {f2(bt['pf'])}",f"fees x3: PF {f2(bt['pf_x3'])}"]
    if bt["mode"]=="grok":rows.append(f"liquidations: {bt['liquidations']}")
    rows+=[SEP,"evidence bar:"]
    for c in bt["checks"]:
        v=c["value"];vs="–" if v is None else (f"{v:.0%}" if any(w in c["name"] for w in("drawdown","pass","green")) else f"{v:.2f}" if isinstance(v,float) else str(v))
        rows+=wrap(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']} {c['need']}");rows.append(f"     got {vs}")
    rows+=[SEP]+sum((wrap(n,"· ") for n in bt["notes"]),[])
    return term("backtest --perps "+bt["mode"],rows,("OK","✅ perps "+bt["mode"]+" passed the bar") if bt["passed"] else ("WARN","perps "+bt["mode"]+": NOT passed"))
_lock=threading.Lock()
def run_async(db_path,notify,cfg):
    if not _lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);n=Notifier(d,bool(getattr(notify,"token","")));data={}
            for k,sym in COINS.items():
                try:candles.backfill(d,sym,H,start=candles.HISTORY_FROM);data[k]=candles.load(d,sym,H,since=candles.HISTORY_FROM)
                except Exception as e:log.warning("perps history %s: %s",k,e)
            out={}
            for mode in("charter","grok"):
                bt=full(data,(cfg.get("perps") or {}),mode);out[mode]=bt;n.send(report(bt))
            d.kv_set(KEY,out)
        except Exception as e:
            log.exception("perps backtest failed");notify.send(term("backtest perps",wrap(f"{type(e).__name__}: {str(e)[:160]}"),("ERR","perps backtest failed")))
        finally:_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
