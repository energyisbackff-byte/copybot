"""Backtest for Trend Rider on stored 15m candles, with the same rules as live (strategy_trend).
Pessimistic fills: entry at the next candle's open; if a candle touches both stop and target, the stop wins; a gap
through a level fills at the open; break-even only moves after the candle that reached +1R; costs on both sides.
History is split by time: the first 75% is the training period, the last 25% is held back and judged once."""
import time,json,statistics as st
import strategy_trend as S
GATE=dict(min_trades=100,min_pf_double_cost=1.3,max_dd=0.10,max_top3=0.40)
def run(c15,p=None,cost=0.0015,equity=30.0,start_ts=None,end_ts=None,ind=None):
    ind=ind or S.Ind(c15,p);p=ind.p;cash=equity;pos=None;pend=None;trades=[];curve=[]
    cool=0;pause=0;streak=0;last=None;day=week=None;d0=w0=equity;peak=equity;tripped=None;blocked=0
    for i in range(1,len(ind.ts)):
        t=ind.ts[i]
        if start_ts and t<start_ts:continue
        if end_ts and t>=end_ts:break
        last=i
        o,h,l,c=ind.o[i],ind.h[i],ind.l[i],ind.c[i]
        eqnow=cash+(pos["units"]*o if pos else 0);dk=t//86400;wk=(t//86400+3)//7     # UTC day / Monday-based week
        if dk!=day:day,d0=dk,eqnow
        if wk!=week:week,w0=wk,eqnow
        peak=max(peak,eqnow)
        if tripped is None and eqnow<peak*(1-p["dd_switch"]):tripped=t
        limit_hit=eqnow<d0*(1-p["daily_loss"]) or eqnow<w0*(1-p["weekly_loss"])
        if pend and limit_hit:pend=None;blocked+=1
        if pend and not pos:                                   # enter at this candle's open
            sp=(o-pend["stop"])/o
            if p["min_stop"]<=sp<=p["max_stop"]:
                size=S.size_usd(cash,sp,p);units=size/o;cash-=size*(1+cost)
                pos=dict(S.plan(o,pend["stop"],p),units=units,size=size,t=t,risk=size*sp)
            pend=None
        if pos:
            px=why=None
            if o<=pos["stop"]:px,why=o,"stop"
            elif l<=pos["stop"]:px,why=pos["stop"],"stop"
            elif o>=pos["target"]:px,why=o,"target"
            elif h>=pos["target"]:px,why=pos["target"],"target"
            else:
                if not pos["be"] and h>=pos["be_at"]:pos["stop"]=max(pos["stop"],pos["entry"]*(1+p["be_buffer"]));pos["be"]=True
                if t%3600==2700 and ind.trend_broken_at_hour(ind.hidx[i]):px,why=c,"trend_break"
                elif t+900-pos["t"]>=p["max_hold_h"]*3600:px,why=c,"max_hold"
            if px:
                back=pos["units"]*px*(1-cost);pnl=back-pos["size"]*(1+cost);cash+=back
                trades.append(dict(t_in=pos["t"],t_out=t+900,entry=pos["entry"],exit=px,pnl=pnl,r=pnl/pos["risk"] if pos["risk"] else 0,why=why,be=pos["be"]))
                pos=None;cool=t+900+p["cooldown_h"]*3600;streak=streak+1 if pnl<=0 else 0
                if streak>=p["loss_streak"]:pause=t+900+p["streak_pause_h"]*3600;streak=0
        if not pos and not pend and t+900>=max(cool,pause):
            sg=ind.signal(i)
            if sg and (cash<d0*(1-p["daily_loss"]) or cash<w0*(1-p["weekly_loss"])):blocked+=1;sg=None
            if sg:pend=sg
        if i%4==0:curve.append((t+900,cash+(pos["units"]*c if pos else 0)))
    if pos and last is not None:   # close anything still open at the last close, so the result is honest
        c=ind.c[last];back=pos["units"]*c*(1-cost);pnl=back-pos["size"]*(1+cost);cash+=back
        trades.append(dict(t_in=pos["t"],t_out=ind.ts[last]+900,entry=pos["entry"],exit=c,pnl=pnl,r=pnl/pos["risk"] if pos["risk"] else 0,why="end",be=pos["be"]))
    return dict(trades=trades,curve=curve,start=equity,end=cash,dd_switch_at=tripped,limit_blocked=blocked)
def metrics(res,days):
    T=res["trades"];w=[x["pnl"] for x in T if x["pnl"]>0];lo=[x["pnl"] for x in T if x["pnl"]<=0]
    gw=sum(w);gl=-sum(lo);pk=res["start"];dd=0
    for _,e in res["curve"]:pk=max(pk,e);dd=max(dd,(pk-e)/pk)
    why={}
    for x in T:why[x["why"]]=why.get(x["why"],0)+1
    return dict(trades=len(T),win_rate=len(w)/len(T) if T else None,pf=(gw/gl if gl else (99.0 if gw else None)),
        ret=res["end"]/res["start"]-1,max_dd=dd,avg_r=st.mean(x["r"] for x in T) if T else None,
        top3=sum(sorted(w,reverse=True)[:3])/gw if gw else None,per_week=len(T)/(days/7) if days else None,exits=why,
        start=res["start"],end=res["end"],dd_switch_at=res.get("dd_switch_at"),limit_blocked=res.get("limit_blocked",0))
def full(c15,p=None,cost=0.0015,equity=30.0,split=0.75):
    """Train / held-back / whole-period results, a doubled-cost stress run and the gate A verdict."""
    ind=S.Ind(c15,p);first=next((i for i in range(len(ind.ts)) if ind.ready(i)),None)
    if first is None:return dict(error="not enough history: need about 12 days of candles at least")
    t0=ind.ts[first];t1=ind.ts[-1]+900;cut=int(t0+(t1-t0)*split)//900*900;D=lambda a,b:(b-a)/86400
    allr=run(c15,cost=cost,equity=equity,ind=ind);tr=run(c15,cost=cost,equity=equity,end_ts=cut,ind=ind)
    ho=run(c15,cost=cost,equity=equity,start_ts=cut,ind=ind);dbl=run(c15,cost=cost*2,equity=equity,ind=ind)
    M=dict(all=metrics(allr,D(t0,t1)),train=metrics(tr,D(t0,cut)),holdout=metrics(ho,D(cut,t1)),double_cost=metrics(dbl,D(t0,t1)))
    a=M["all"];checks=[
        ("trades",f">= {GATE['min_trades']}",a["trades"],a["trades"]>=GATE["min_trades"]),
        ("PF, costs x2",f">= {GATE['min_pf_double_cost']}",M["double_cost"]["pf"],(M["double_cost"]["pf"] or 0)>=GATE["min_pf_double_cost"]),
        ("max drawdown",f"<= {GATE['max_dd']:.0%}",a["max_dd"],a["max_dd"]<=GATE["max_dd"]),
        ("held-back profit","> 0",M["holdout"]["ret"],M["holdout"]["ret"]>0),
        ("best 3 trades",f"<= {GATE['max_top3']:.0%}",a["top3"],a["top3"] is not None and a["top3"]<=GATE["max_top3"])]
    step=max(1,len(allr["curve"])//300)
    return dict(version=S.VERSION,ran=time.time(),cost=cost,equity=equity,from_ts=t0,to_ts=t1,cut_ts=cut,candles=len(c15),
        metrics=M,checks=[dict(name=n,need=nd,value=v,ok=bool(ok)) for n,nd,v,ok in checks],passed=all(ok for *_,ok in checks),
        curve=allr["curve"][::step],last_trades=allr["trades"][-20:])
