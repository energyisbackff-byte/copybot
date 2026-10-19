"""Backtest for Trend Rider v2 on stored daily candles of SOL, BTC and ETH, with the same rules as live (strategy_v2).
Pessimistic and realistic fills:
  - a signal at day i's close fills at day i+1's OPEN (the first price we can actually trade after seeing the close)
  - the stop is checked against every day's LOW; if the day OPENS below the stop, the fill is the open (gap)
  - a trend exit (close below SMA50) fills at the next open
  - costs on both sides; the gate also re-runs everything with costs doubled
Each coin trades its own $1,000 book (percent results are what matter). The common period is split by time: the first
75% is training, the last 25% is held back and judged once. Gate v2 was fixed on 14 Oct 2026 before any v2 result."""
import time,statistics as st
import strategy_v2 as S,evidence as EV
COINS=("SOL","BTC","ETH")
GATE=dict(min_trades=40,min_pf_double_cost=1.3,min_profitable_coins=2,max_dd=0.10)
def run(rows,p=None,cost=0.0015,equity=1000.0,start_ts=None,end_ts=None,ind=None):
    ind=ind or S.Daily(rows,p);p=ind.p;cash=equity;pos=None;pend=None;exit_next=False;trades=[];curve=[];last=None
    def close(px,t,why):
        nonlocal cash,pos
        back=pos["units"]*px*(1-cost);pnl=back-pos["size"]*(1+cost);cash+=back
        trades.append(dict(t_in=pos["t"],t_out=t,entry=pos["entry"],exit=px,pnl=pnl,r=pnl/pos["risk"] if pos["risk"] else 0,why=why,days=(t-pos["t"])/86400))
        pos=None
    for i in range(len(ind.ts)):
        t=ind.ts[i]
        if start_ts and t<start_ts:continue
        if end_ts and t>=end_ts:break
        last=i;o,h,l,c=ind.o[i],ind.h[i],ind.l[i],ind.c[i]
        if pos and exit_next:close(o,t,"trend_exit")             # 7: trend exit at the next open
        exit_next=False
        if pend and not pos:                                      # 4: entry at the next open
            if pend["stop"]<o<=pend["ref"]*(1+p["max_chase"]):
                sp=(o-pend["stop"])/o;size=S.size_usd(cash,sp,cost,p);units=size/o
                if size>0:
                    cash-=size*(1+cost);pos=dict(units=units,size=size,entry=o,stop=pend["stop"],hi=pend["ref"],t=t,
                        risk=size*(sp+2*cost))
        pend=None
        if pos:                                                   # 6: the stop is live all day
            if o<=pos["stop"]:close(o,t,"stop")
            elif l<=pos["stop"]:close(pos["stop"],t,"stop")
        if pos:                                                   # at the close: trend exit or trail the stop
            if ind.trend_exit(i):exit_next=True
            else:
                pos["hi"]=max(pos["hi"],c)
                if ind.atr[i] is not None:pos["stop"]=max(pos["stop"],ind.trail(i,pos["hi"]))
        elif not exit_next:
            sg=ind.signal(i)
            if sg:pend=sg
        curve.append((t+86400,cash+(pos["units"]*c if pos else 0)))
    if pos and last is not None:close(ind.c[last],ind.ts[last]+86400,"end")   # close what is still open, so results are honest
    return dict(trades=trades,curve=curve,start=equity,end=cash)
def metrics(res):
    T=res["trades"];w=[x["pnl"] for x in T if x["pnl"]>0];lo=[x["pnl"] for x in T if x["pnl"]<=0]
    gw=sum(w);gl=-sum(lo);pk=res["start"];dd=0
    for _,e in res["curve"]:pk=max(pk,e);dd=max(dd,(pk-e)/pk)
    why={}
    for x in T:why[x["why"]]=why.get(x["why"],0)+1
    return dict(trades=len(T),win_rate=len(w)/len(T) if T else None,pf=(gw/gl if gl else (99.0 if gw else None)),pnl=sum(x["pnl"] for x in T),
        ret=res["end"]/res["start"]-1,max_dd=dd,avg_r=st.mean(x["r"] for x in T) if T else None,best=max((x["pnl"] for x in T),default=0),
        avg_days=st.mean(x["days"] for x in T) if T else None,exits=why,gross_win=gw,gross_loss=gl)
def hold(rows,t0,t1):
    """Buy-and-hold over the same period (bought at the first open, valued at daily closes)."""
    r=[x for x in rows if t0<=x[0]<t1]
    if not r:return dict(ret=None,max_dd=None)
    o=r[0][1];pk=o;dd=0
    for x in r:pk=max(pk,x[4]);dd=max(dd,(pk-x[4])/pk)
    return dict(ret=r[-1][4]/o-1,max_dd=dd)
def pf_of(pnls):
    gw=sum(x for x in pnls if x>0);gl=-sum(x for x in pnls if x<=0);return gw/gl if gl else (99.0 if gw else None)
def full(data,p=None,cost=0.0015,equity=1000.0,split=0.75,seen_from=None):
    """data: {coin: daily rows}. -> report dict with per-coin results, pooled results, benchmarks and the gate verdict.
    seen_from: start of history an earlier strategy's test already looked at (disclosed if the held-back period overlaps)."""
    inds={k:S.Daily(v,p) for k,v in data.items() if v}
    if set(inds)!=set(COINS):return dict(error="missing coins: "+", ".join(sorted(set(COINS)-set(inds))),version=S.VERSION)
    firsts=[];lasts=[]
    for k,d in inds.items():
        f=next((i for i in range(len(d.ts)) if d.ready(i)),None)
        if f is None:return dict(error=f"not enough {k} history (need 60+ days)",version=S.VERSION)
        firsts.append(d.ts[f]);lasts.append(d.ts[-1])
    t0=max(firsts);t1=min(lasts)+86400;cut=int(t0+(t1-t0)*split)//86400*86400
    C={};pooled=dict(all=[],train=[],holdout=[],dbl=[])
    for k in COINS:
        d=inds[k];rows=data[k]
        R=dict(all=run(rows,cost=cost,equity=equity,start_ts=t0,end_ts=t1,ind=d),train=run(rows,cost=cost,equity=equity,start_ts=t0,end_ts=cut,ind=d),
               holdout=run(rows,cost=cost,equity=equity,start_ts=cut,end_ts=t1,ind=d),dbl=run(rows,cost=cost*2,equity=equity,start_ts=t0,end_ts=t1,ind=d))
        C[k]=dict({n:metrics(r) for n,r in R.items()},hold=hold(rows,t0,t1),hold_holdout=hold(rows,cut,t1),
                  trades=[dict(x,coin=k) for x in R["all"]["trades"]])
        for n in pooled:pooled[n]+=[x["pnl"] for x in R[n]["trades"]]
    A=C;pall=pooled["all"]
    without_best=sum(pall)-sum(max([x["pnl"] for x in A[k]["trades"]]+[0]) for k in COINS)
    P=dict(trades=len(pall),pnl=sum(pall),pf=pf_of(pall),pf_double=pf_of(pooled["dbl"]),holdout_pnl=sum(pooled["holdout"]),holdout_trades=len(pooled["holdout"]),
           without_best=without_best,profitable_coins=sum(1 for k in COINS if A[k]["all"]["pnl"]>0),win_rate=(sum(1 for x in pall if x>0)/len(pall)) if pall else None)
    checks=[("trades, 3 coins",f">= {GATE['min_trades']}",P["trades"],P["trades"]>=GATE["min_trades"]),
        ("PF, costs x2",f">= {GATE['min_pf_double_cost']}",P["pf_double"],(P["pf_double"] or 0)>=GATE["min_pf_double_cost"]),
        ("profitable coins",f">= {GATE['min_profitable_coins']} of 3",P["profitable_coins"],P["profitable_coins"]>=GATE["min_profitable_coins"]),
        ("SOL alone, costs x2","profit > 0",A["SOL"]["dbl"]["pnl"],A["SOL"]["dbl"]["pnl"]>0),
        ("worst coin drawdown",f"<= {GATE['max_dd']:.0%}",max(A[k]["all"]["max_dd"] for k in COINS),max(A[k]["all"]["max_dd"] for k in COINS)<=GATE["max_dd"]),
        ("held-back profit","> 0",P["holdout_pnl"],P["holdout_pnl"]>0),
        ("profit w/o best trades","> 0",without_best,without_best>0)]
    notes=["BTC, ETH and SOL move together, so trades are not independent evidence.",
           "Real money would trade SOL only; that is why SOL must pass on its own."]
    if seen_from and seen_from<t1:notes.append("The held-back period overlaps prices that the v1 test already looked at (from "+
        time.strftime("%d %b %Y",time.gmtime(max(seen_from,cut)))+"); it is not fully untouched.")
    curve=[(t,e) for t,e in run(data["SOL"],cost=cost,equity=equity,start_ts=t0,end_ts=t1,ind=inds["SOL"])["curve"]][::3]
    bar=evidence_bar(data,inds,p,cost,equity,t0,t1,cut,C)
    return dict(version=S.VERSION,ran=time.time(),cost=cost,equity=equity,from_ts=t0,to_ts=t1,cut_ts=cut,candles={k:len(v) for k,v in data.items()},
        coins={k:{n:v for n,v in A[k].items() if n!="trades"} for k in COINS},pooled=P,bar=bar,
        checks=[dict(name=n,need=nd,value=v,ok=bool(ok)) for n,nd,v,ok in checks],passed=all(ok for *_,ok in checks),notes=notes,
        last_trades=sorted(sum((A[k]["trades"] for k in COINS),[]),key=lambda x:x["t_in"])[-15:],curve_sol=curve)
def evidence_bar(data,inds,p,cost,equity,t0,t1,cut,C):
    """The Master Build Prompt's bar, reported next to gate v2 (gate v2 itself is unchanged): Sharpe of SOL on the
    held-back period, profit factor with costs x3, Monte Carlo drawdown, neighbouring settings, walk-forward windows."""
    sol=run(data["SOL"],cost=cost,equity=equity,start_ts=cut,end_ts=t1,ind=inds["SOL"])
    sh=EV.sharpe(EV.daily_returns(sol["curve"]))
    p3=[x["pnl"] for k in COINS for x in run(data[k],cost=cost*3,equity=equity,start_ts=t0,end_ts=t1,ind=inds[k])["trades"]]
    tr=[dict(t_out=x["t_out"],ret=x["pnl"]/equity) for k in COINS for x in C[k]["trades"]]
    mc=EV.monte_carlo([x["ret"] for x in tr])
    nb={}
    for k_,v_ in(("atr_mult",2.5),("atr_mult",3.5),("breakout",15),("breakout",25),("sma_slow",40),("sma_slow",60)):
        pp=S.params(dict(p or {},**{k_:v_}));pn=[x["pnl"] for k in COINS for x in run(data[k],pp,cost=cost*3,equity=equity,start_ts=t0,end_ts=t1)["trades"]]
        nb[f"{k_}={v_}"]=dict(pf=EV.pf(pn),pnl=sum(pn),trades=len(pn))
    share,_=EV.plateau(nb,lambda m:(m["pf"] or 0)>=1.3 and m["pnl"]>0)
    W,wf=EV.walk_forward(tr,t0,t1,90)
    checks,ok=EV.verdict(sh,EV.pf(p3),mc,len(tr),share,wf)
    return dict(sharpe_oos=sh,pf_x3=EV.pf(p3),mc=mc,plateau=nb,plateau_share=share,wf_share=wf,wf=[dict(w,start=w["start"]) for w in W],checks=checks,passed=ok)
