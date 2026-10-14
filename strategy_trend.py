"""Trend Rider v1: pure rules over closed SOL/USD 15m candles. Used unchanged by the backtester and the live paper book,
so what was tested is exactly what trades.
  1 trend: last closed 1h close > 200-hour SMA, and that SMA higher than 24 hours earlier
  2 pullback: within the last 4 candles a low touched the 20-EMA, and no close fell below the 50-EMA
  3 bounce: this candle closes above the previous candle's high
  4 stop: lowest low of the pullback minus 0.3 x ATR(14); skip if the stop is nearer than 0.6% or farther than 4%
  5 size: lose at most 0.5% of the book at the stop, position capped at 50% of the book
  6 exits: stop, 2R target, stop to break-even at +1R, hourly close below the 200-hour SMA, 48h max hold
  7 restraint: one position, 2h cooldown after an exit, 24h pause after 3 losses in a row"""
VERSION="trend-rider v1"
P=dict(ema_fast=20,ema_slow=50,atr_n=14,sma_h=200,slope_h=24,pullback_bars=4,atr_buffer=0.3,min_stop=0.006,max_stop=0.04,
       target_r=2.0,be_r=1.0,be_buffer=0.002,max_hold_h=48,cooldown_h=2,loss_streak=3,streak_pause_h=24,risk=0.005,max_pos=0.5,
       daily_loss=0.02,weekly_loss=0.04,dd_switch=0.06)   # last three: the book's limits, modelled in the backtest too
def params(over=None):return dict(P,**(over or {}))
def ema(xs,n):
    out=[];k=2/(n+1);e=None
    for x in xs:e=x if e is None else e+k*(x-e);out.append(e)
    return out
def atr(h,l,c,n):
    out=[];a=None
    for i in range(len(c)):
        tr=h[i]-l[i] if i==0 else max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        a=tr if a is None else (a*(n-1)+tr)/n;out.append(a)
    return out
class Ind:
    """Indicators for a candle list [(ts,o,h,l,c,v), ...] sorted by ts, all closed."""
    def __init__(s,c15,p=None):
        p=s.p=params(p);s.c15=c15
        s.ts=[r[0] for r in c15];s.o=[r[1] for r in c15];s.h=[r[2] for r in c15];s.l=[r[3] for r in c15];s.c=[r[4] for r in c15]
        s.ef=ema(s.c,p["ema_fast"]);s.es=ema(s.c,p["ema_slow"]);s.atr=atr(s.h,s.l,s.c,p["atr_n"])
        # hourly closes from complete hours (the candle starting at hour+45min closes the hour)
        s.hts=[];s.hc=[]
        for i,t in enumerate(s.ts):
            if t%3600==2700:s.hts.append(t+900);s.hc.append(s.c[i])   # hts = when that hour closed
        n=p["sma_h"];s.sma=[None]*len(s.hc);run=0.0
        for j,x in enumerate(s.hc):
            run+=x
            if j>=n:run-=s.hc[j-n]
            if j>=n-1:s.sma[j]=run/n
        # for each 15m candle i: index of the last hour closed at or before candle i's close
        s.hidx=[];j=-1
        for t in s.ts:
            while j+1<len(s.hts) and s.hts[j+1]<=t+900:j+=1
            s.hidx.append(j)
    def trend_ok(s,i):
        j=s.hidx[i];k=j-s.p["slope_h"]
        if j<0 or k<0 or s.sma[j] is None or s.sma[k] is None:return False
        return s.hc[j]>s.sma[j] and s.sma[j]>s.sma[k]
    def trend_broken_at_hour(s,j):
        return s.sma[j] is not None and s.hc[j]<s.sma[j]
    def ready(s,i):return i>=s.p["ema_slow"]*3 and s.hidx[i]>=s.p["sma_h"]+s.p["slope_h"]
    def signal(s,i):
        """Entry decision at the close of candle i -> None or dict(stop, stop_pct, ref, features)."""
        p=s.p
        if not s.ready(i) or not s.trend_ok(i):return None
        w=range(i-p["pullback_bars"]+1,i+1)
        if not any(s.l[k]<=s.ef[k] for k in w):return None
        if any(s.c[k]<s.es[k] for k in w):return None
        if not s.c[i]>s.h[i-1]:return None
        ref=s.c[i];stop=min(s.l[k] for k in w)-p["atr_buffer"]*s.atr[i];sp=(ref-stop)/ref
        if sp<p["min_stop"] or sp>p["max_stop"]:return None
        j=s.hidx[i]
        return dict(ref=ref,stop=stop,stop_pct=sp,features=dict(close=round(ref,4),ema20=round(s.ef[i],4),ema50=round(s.es[i],4),
            atr=round(s.atr[i],4),hour_close=round(s.hc[j],4),sma200h=round(s.sma[j],4),stop_pct=round(sp,4)))
def size_usd(equity,stop_pct,p=None):
    p=params(p);return max(0.0,min(equity*p["risk"]/stop_pct,equity*p["max_pos"]))
def plan(entry,stop,p=None):
    """Exit levels from the actual fill price; the risk distance is kept from the signal."""
    p=params(p);r=entry-stop
    return dict(entry=entry,stop=stop,r=r,target=entry+p["target_r"]*r,be_at=entry+p["be_r"]*r,be=False,version=VERSION)
