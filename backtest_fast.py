"""Backtest for the faster candidates (strategy_fast) on stored 1h candles of SOL, BTC and ETH.
Fills: a signal at a 1h close fills at the next 1h OPEN; the stop is checked against every hour's low (a gap below it
fills at the open); a rule exit at a close fills at the next open; costs on both sides; everything re-run with costs
doubled. Each coin trades its own $1,000 book. The common period is split by time: first 75% training, last 25% held
back. Gate "fast" fixed on 18 Oct 2026 before any result:
  trades across 3 coins          >= 300 (enough evidence for a fast strategy)
  profit factor, all coins        >= 1.3 at the base cost (0.15%/side = about 3x the cost measured on Jupiter)
  costs doubled (0.30%/side)      still profitable, all coins together AND SOL alone
  profitable coins                >= 2 of 3
  SOL alone                       profitable in the training years AND in the held-back period
  worst coin drawdown             <= 10%
  held-back period                profitable, all coins together
  without each coin's 10 best trades: still profitable
Why the cost rule differs from v2 (PF >= 1.3 with costs doubled): a fast strategy pays costs about 30x more often, and
0.15%/side is already about 3x the measured cost; it must still make money at 6x."""
import time,statistics as st
import strategy_fast as F
COINS=("SOL","BTC","ETH");H=3600
GATE=dict(min_trades=300,min_pf=1.3,max_dd=0.10,best_n=10)
def run(ind,strat,cost=0.0015,equity=1000.0,start_ts=None,end_ts=None):
    p=ind.p;cash=equity;pos=None;pend=None;exit_next=None;trades=[];curve=[];last=None
    def close(px,t,why):
        nonlocal cash,pos
        back=pos["units"]*px*(1-cost);pnl=back-pos["size"]*(1+cost);cash+=back
        trades.append(dict(t_in=pos["t"],t_out=t,entry=pos["entry"],exit=px,pnl=pnl,why=why,hours=(t-pos["t"])/H));pos=None
    for i in range(len(ind.ts)):
        t=ind.ts[i]
        if start_ts and t<start_ts:continue
        if end_ts and t>=end_ts:break
        last=i;o,l,c=ind.o[i],ind.l[i],ind.c[i]
        if pos and exit_next:close(o,t,exit_next)
        exit_next=None
        if pend and not pos and o>pend["stop"]:
            sp=(o-pend["stop"])/o;size=F.size_usd(cash,sp,cost,p)
            if size>0:cash-=size*(1+cost);pos=dict(units=size/o,size=size,entry=o,stop=pend["stop"],hi=pend["ref"],t=t)
        pend=None
        if pos:
            if o<=pos["stop"]:close(o,t,"stop")
            elif l<=pos["stop"]:close(pos["stop"],t,"stop")
        if pos:exit_next=strat.on_close(i,pos)
        elif not exit_next:pend=strat.signal(i)
        if (t+H)%86400==0:curve.append((t+H,cash+(pos["units"]*c if pos else 0)))     # one point per day
    if pos and last is not None:close(ind.c[last],ind.ts[last]+H,"end")
    curve.append((ind.ts[last]+H if last is not None else 0,cash))
    return dict(trades=trades,curve=curve,start=equity,end=cash)
def metrics(res,months):
    T=res["trades"];w=[x["pnl"] for x in T if x["pnl"]>0];lo=[x["pnl"] for x in T if x["pnl"]<=0]
    gw=sum(w);gl=-sum(lo);pk=res["start"];dd=0
    for _,e in res["curve"]:pk=max(pk,e);dd=max(dd,(pk-e)/pk)
    why={}
    for x in T:why[x["why"]]=why.get(x["why"],0)+1
    return dict(trades=len(T),per_month=len(T)/months if months else None,win_rate=len(w)/len(T) if T else None,
        pf=(gw/gl if gl else (99.0 if gw else None)),pnl=sum(x["pnl"] for x in T),ret=res["end"]/res["start"]-1,max_dd=dd,
        won=gw,lost=gl,avg_hours=st.mean(x["hours"] for x in T) if T else None,exits=why)
def pf_of(pnls):
    gw=sum(x for x in pnls if x>0);gl=-sum(x for x in pnls if x<=0);return gw/gl if gl else (99.0 if gw else None)
def full(data,cost=0.0015,equity=1000.0,split=0.75,p=None,seen_from=None):
    """data: {coin: 1h rows}. -> {candidate VERSION: report} plus shared period info."""
    if set(k for k,v in data.items() if v)!=set(COINS):return dict(error="missing coins: "+", ".join(sorted(set(COINS)-set(k for k,v in data.items() if v))))
    inds={k:F.Hourly(data[k],p) for k in COINS}
    firsts=[];lasts=[]
    for k,d in inds.items():
        f=next((i for i in range(len(d.ts)) if d.ts[i]>=d.ts[0]+60*86400),None)        # 60 days of warm-up for the indicators
        if f is None:return dict(error=f"not enough {k} history")
        firsts.append(d.ts[f]);lasts.append(d.ts[-1])
    t0=max(firsts);t1=min(lasts)+H;cut=int(t0+(t1-t0)*split)//86400*86400
    mo=lambda a,b:(b-a)/(30.44*86400)
    out=dict(ran=time.time(),cost=cost,from_ts=t0,to_ts=t1,cut_ts=cut,candles={k:len(v) for k,v in data.items()},candidates=[])
    for S in F.CANDIDATES:
        C={};pooled=dict(all=[],holdout=[],dbl=[]);best=0
        for k in COINS:
            d=inds[k];s=S(d)
            R=dict(all=run(d,s,cost,equity,t0,t1),train=run(d,s,cost,equity,t0,cut),holdout=run(d,s,cost,equity,cut,t1),dbl=run(d,s,cost*2,equity,t0,t1))
            C[k]=dict(all=metrics(R["all"],mo(t0,t1)),train=metrics(R["train"],mo(t0,cut)),holdout=metrics(R["holdout"],mo(cut,t1)),dbl=metrics(R["dbl"],mo(t0,t1)))
            for n in pooled:pooled[n]+=[x["pnl"] for x in R[n]["trades"]]
            best+=sum(sorted((x["pnl"] for x in R["all"]["trades"]),reverse=True)[:GATE["best_n"]])
        pa=pooled["all"];wo=sum(pa)-best
        P=dict(trades=len(pa),pnl=sum(pa),pf=pf_of(pa),pf_double=pf_of(pooled["dbl"]),pnl_double=sum(pooled["dbl"]),holdout_pnl=sum(pooled["holdout"]),
               without_best=wo,profitable_coins=sum(1 for k in COINS if C[k]["all"]["pnl"]>0))
        worst=max(C[k]["all"]["max_dd"] for k in COINS)
        checks=[("trades, 3 coins",f">= {GATE['min_trades']}",P["trades"],P["trades"]>=GATE["min_trades"]),
            ("PF, base cost",f">= {GATE['min_pf']}",P["pf"],(P["pf"] or 0)>=GATE["min_pf"]),
            ("costs x2, all coins","profit > 0",P["pnl_double"],P["pnl_double"]>0),
            ("costs x2, SOL alone","profit > 0",C["SOL"]["dbl"]["pnl"],C["SOL"]["dbl"]["pnl"]>0),
            ("profitable coins",">= 2 of 3",P["profitable_coins"],P["profitable_coins"]>=2),
            ("SOL training years","profit > 0",C["SOL"]["train"]["pnl"],C["SOL"]["train"]["pnl"]>0),
            ("SOL held-back","profit > 0",C["SOL"]["holdout"]["pnl"],C["SOL"]["holdout"]["pnl"]>0),
            ("worst coin drawdown",f"<= {GATE['max_dd']:.0%}",worst,worst<=GATE["max_dd"]),
            ("held-back, all coins","profit > 0",P["holdout_pnl"],P["holdout_pnl"]>0),
            (f"w/o {GATE['best_n']} best per coin","profit > 0",wo,wo>0)]
        out["candidates"].append(dict(name=S.NAME,version=S.VERSION,coins=C,pooled=P,checks=[dict(name=n,need=nd,value=v,ok=bool(ok)) for n,nd,v,ok in checks],
            passed=all(ok for *_,ok in checks)))
    notes=["Two strategies were tested at once, so one could pass by luck; a pass still has to prove itself on paper.",
           "BTC, ETH and SOL move together, so trades are not independent evidence.",
           "Real money would trade SOL only; that is why SOL must pass on its own."]
    if seen_from and seen_from<t1:notes.append("The held-back period overlaps prices earlier tests already looked at; it is not fully untouched.")
    out["notes"]=notes;return out
