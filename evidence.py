"""The evidence bar from the Master Build Prompt, as small pure functions shared by every backtest:
  - Sharpe ratio on the held-back (out-of-sample) period, from daily equity, annualized with sqrt(365) (crypto trades daily)
  - profit factor with costs tripled
  - Monte Carlo: the trade results reshuffled 2,000 times -> how bad the drawdown gets in the worst 5% of orders
  - plateau test: neighbouring settings must also work (a real edge is a plateau, not a spike)
  - walk-forward: the rules are judged window by window on time they never saw (rules are fixed, nothing is re-fitted)
  - at least 30 trades before any of it means anything
BAR = Sharpe > 1.5 out-of-sample AND PF >= 1.3 with fees x3 AND Monte Carlo 95% drawdown survivable AND 30+ trades."""
import math,random,statistics as st
BAR=dict(sharpe_oos=1.5,pf_fees_x3=1.3,min_trades=30,mc_dd95=0.25,plateau_share=0.6,wf_share=0.6)
def daily_returns(curve):
    """curve: [(ts, equity)] in time order (any spacing) -> list of daily returns (last value of each UTC day)."""
    last={}
    for t,e in curve:last[int(t//86400)]=e
    days=sorted(last);out=[]
    for a,b in zip(days,days[1:]):
        ea=last[a];eb=last[b]
        if ea>0:out+= [0.0]*(b-a-1)+[eb/ea-1]
    return out
def sharpe(rets,per_year=365):
    if len(rets)<20:return None
    sd=st.pstdev(rets)
    if sd==0:return None
    return st.mean(rets)/sd*math.sqrt(per_year)
def pf(pnls):
    gw=sum(x for x in pnls if x>0);gl=-sum(x for x in pnls if x<=0)
    return gw/gl if gl else (99.0 if gw else None)
def max_dd_from_returns(rs,start=1.0):
    e=pk=start;dd=0.0
    for r in rs:
        e*=1+r;pk=max(pk,e);dd=max(dd,(pk-e)/pk if pk>0 else 1.0)
        if e<=0:return 1.0
    return dd
def monte_carlo(trade_returns,n=2000,seed=7):
    """trade_returns: each trade's result as a share of the book (e.g. -0.01). -> dd50, dd95, worst, ret5 (5th pct)."""
    if len(trade_returns)<5:return None
    rnd=random.Random(seed);dds=[];fin=[]
    for _ in range(n):
        x=trade_returns[:];rnd.shuffle(x);dds.append(max_dd_from_returns(x));e=1.0
        for r in x:e*=1+r
        fin.append(e-1)
    dds.sort();fin.sort()
    return dict(dd50=dds[n//2],dd95=dds[int(n*0.95)],worst=dds[-1],ret5=fin[int(n*0.05)],ret50=fin[n//2],runs=n)
def plateau(results,ok):
    """results: {settings_label: metrics}; ok(metrics)->bool. -> (share that passes, list of (label, passed))."""
    L=[(k,bool(ok(v))) for k,v in results.items()]
    return (sum(1 for _,p in L if p)/len(L) if L else 0.0),L
def walk_forward(trades,t0,t1,window_days=90):
    """trades: dicts with t_out and ret (share of book). Splits [t0,t1) into consecutive windows. -> list of
    dict(start, trades, ret, pf) and the share of windows (with trades) that made money."""
    W=[];t=t0
    while t<t1:
        e=min(t1,t+window_days*86400);T=[x for x in trades if t<=x["t_out"]<e];r=1.0
        for x in T:r*=1+x["ret"]
        W.append(dict(start=t,trades=len(T),ret=r-1,pf=pf([x["ret"] for x in T])));t=e
    act=[w for w in W if w["trades"]]
    return W,(sum(1 for w in act if w["ret"]>0)/len(act) if act else 0.0)
def verdict(sharpe_oos,pf3,mc,trades,plateau_share=None,wf_share=None,bar=None):
    b=dict(BAR,**(bar or {}))
    C=[("trades",f">= {b['min_trades']}",trades,trades>=b["min_trades"]),
       ("Sharpe, held-back",f"> {b['sharpe_oos']}",sharpe_oos,sharpe_oos is not None and sharpe_oos>b["sharpe_oos"]),
       ("PF, fees x3",f">= {b['pf_fees_x3']}",pf3,pf3 is not None and pf3>=b["pf_fees_x3"]),
       ("Monte Carlo 95% drawdown",f"<= {b['mc_dd95']:.0%}",mc["dd95"] if mc else None,bool(mc) and mc["dd95"]<=b["mc_dd95"])]
    if plateau_share is not None:C.append(("neighbour settings pass",f">= {b['plateau_share']:.0%}",plateau_share,plateau_share>=b["plateau_share"]))
    if wf_share is not None:C.append(("walk-forward windows green",f">= {b['wf_share']:.0%}",wf_share,wf_share>=b["wf_share"]))
    return [dict(name=n,need=nd,value=v,ok=bool(o)) for n,nd,v,o in C],all(o for *_,o in C)
