"""Faster strategy candidates (research: backtest first; paper/live only for one that passes). Rules fixed on
18 Oct 2026 BEFORE any result was seen. Same data for every coin: 1-hour candles (UTC), SOL, BTC and ETH.
Common to both:
  regime ON = the last COMPLETED UTC day closed above its 50-day simple average (built from the same 1h data, so
              nothing from the current day is used); no new entries while the regime is off
  entries fill at the next 1h open; the stop is watched all the time (a gap below it fills at the open)
  size: lose at most 0.5% of the book at the stop, counting both costs and a 0.5% gap buffer; max 50% of the book
  one position at a time
A  "Dip Buyer"  (aims at many trades)
  entry : the 1h close drops hard: RSI(2) (Wilder) below 10, regime ON
  stop  : signal close - 3 x ATR14 (1h, Wilder)
  exits : a 1h close above the 5-hour simple average (the bounce) -> sell at the next open;
          or 48 hours held; or the regime turns off
B  "Fast Trend" (v2's idea on 4-hour bars; 4h bars start 00/04/08/12/16/20 UTC, built from the 1h candles)
  entry : a 4h close above the highest 4h close of the previous 20 bars, regime ON
  stop  : signal close - 3 x ATR14 (4h); trailing at each 4h close: highest 4h close since entry - 3 x ATR14 (4h),
          never moves down
  exits : the stop, or the regime turns off"""
H=3600;DAY=86400;H4=4*H
P=dict(risk=0.005,max_pos=0.5,gap_buffer=0.005,regime_sma=50,atr_n=14,atr_mult=3.0,
       rsi_n=2,rsi_entry=10.0,exit_sma=5,max_hold_h=48,breakout=20)
def params(over=None):return dict(P,**(over or {}))
def sma(xs,n):
    out=[None]*len(xs);run=0.0
    for i,x in enumerate(xs):
        run+=x
        if i>=n:run-=xs[i-n]
        if i>=n-1:out[i]=run/n
    return out
def atr_wilder(h,l,c,n):
    out=[None]*len(c);trs=[]
    for i in range(1,len(c)):
        tr=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]));trs.append(tr)
        if len(trs)==n:out[i]=sum(trs)/n
        elif len(trs)>n:out[i]=(out[i-1]*(n-1)+tr)/n
    return out
def rsi_wilder(c,n):
    """RSI with Wilder smoothing; the first average is the mean of the first n changes."""
    out=[None]*len(c);g=l=None;gs=[];ls=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1];up=max(d,0.0);dn=max(-d,0.0)
        if g is None:
            gs.append(up);ls.append(dn)
            if len(gs)==n:g=sum(gs)/n;l=sum(ls)/n
            else:continue
        else:g=(g*(n-1)+up)/n;l=(l*(n-1)+dn)/n
        out[i]=100.0 if l==0 else 100-100/(1+g/l)
    return out
class Hourly:
    """Indicators over closed 1h candles [(ts,o,h,l,c,v)] sorted by ts."""
    def __init__(s,rows,p=None):
        p=s.p=params(p);s.rows=rows
        s.ts=[r[0] for r in rows];s.o=[r[1] for r in rows];s.h=[r[2] for r in rows];s.l=[r[3] for r in rows];s.c=[r[4] for r in rows]
        s.atr=atr_wilder(s.h,s.l,s.c,p["atr_n"]);s.rsi=rsi_wilder(s.c,p["rsi_n"]);s.sma5=sma(s.c,p["exit_sma"])
        # daily closes of COMPLETED days: a day is complete once a later day has started
        days=[];closes=[]
        for i,t in enumerate(s.ts):
            d=t//DAY
            if days and days[-1]==d:closes[-1]=s.c[i]
            else:days.append(d);closes.append(s.c[i])
        dsma=sma(closes,p["regime_sma"]);s._reg={}
        for k in range(1,len(days)):                       # regime for day days[k] uses day days[k-1]
            prev=k-1;s._reg[days[k]]=dsma[prev] is not None and closes[prev]>dsma[prev]
        # 4h bars: index of the 1h bar that closes each 4h bar, and 4h OHLC
        s.c4i=[];s.c4=[];h4=[];l4=[];cur=None
        for i,t in enumerate(s.ts):
            b=t//H4
            if b!=cur:cur=b;h4.append(s.h[i]);l4.append(s.l[i]);s.c4.append(s.c[i]);s.c4i.append(i)
            else:h4[-1]=max(h4[-1],s.h[i]);l4[-1]=min(l4[-1],s.l[i]);s.c4[-1]=s.c[i];s.c4i[-1]=i
        s.c4ts=[s.ts[i] for i in s.c4i]
        s.atr4=atr_wilder(h4,l4,s.c4,p["atr_n"])
        s.bar4={i:k for k,i in enumerate(s.c4i) if (s.ts[i]+H)%H4==0}  # 1h index -> 4h bar index, only for COMPLETE 4h bars
    def regime(s,i):return s._reg.get(s.ts[i]//DAY,False)
class DipBuyer:
    NAME="A dip-buyer";VERSION="dip-buyer v1"
    def __init__(s,ind):s.x=ind;s.p=ind.p
    def signal(s,i):
        x=s.x
        if x.rsi[i] is None or x.atr[i] is None or not x.regime(i) or x.rsi[i]>=s.p["rsi_entry"]:return None
        return dict(ref=x.c[i],stop=x.c[i]-s.p["atr_mult"]*x.atr[i],bar=i)
    def on_close(s,i,pos):
        x=s.x
        if not x.regime(i):return "regime"
        if x.sma5[i] is not None and x.c[i]>x.sma5[i]:return "bounce"
        if (x.ts[i]+H-pos["t"])>=s.p["max_hold_h"]*H:return "time"
        return None
class FastTrend:
    NAME="B fast-trend";VERSION="fast-trend v1"
    def __init__(s,ind):s.x=ind;s.p=ind.p
    def signal(s,i):
        x=s.x;k=x.bar4.get(i)
        if k is None or k<s.p["breakout"] or x.atr4[k] is None or not x.regime(i):return None
        if x.c4[k]<=max(x.c4[k-s.p["breakout"]:k]):return None
        return dict(ref=x.c4[k],stop=x.c4[k]-s.p["atr_mult"]*x.atr4[k],bar=i)
    def on_close(s,i,pos):
        x=s.x
        if not x.regime(i):return "regime"
        k=x.bar4.get(i)
        if k is not None and x.atr4[k] is not None:
            pos["hi"]=max(pos["hi"],x.c4[k]);pos["stop"]=max(pos["stop"],pos["hi"]-s.p["atr_mult"]*x.atr4[k])
        return None
CANDIDATES=(DipBuyer,FastTrend)
def size_usd(equity,stop_pct,cost,p=None):
    p=params(p);eff=stop_pct+2*cost+p["gap_buffer"]
    return max(0.0,min(equity*p["risk"]/eff if eff>0 else 0,equity*p["max_pos"]))
