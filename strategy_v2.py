"""Trend Rider v2: a slow daily trend follower. Pure rules over CLOSED daily candles (UTC days), used unchanged by the
backtester and the live paper/real-money legs, so what was tested is exactly what trades. Rules fixed 14 Oct 2026,
before any v2 result was seen:
  1 evaluate once a day, after the daily candle closes (00:00 UTC)
  2 uptrend: close > SMA50 (simple 50-day average of closes) and SMA20 > SMA50
  3 entry signal: close > the highest close of the PREVIOUS 20 completed days (today excluded)
  4 entry fill: at the next day's open (live: market buy right after the close); skipped if that price is already
    more than 5% above the signal close (no chasing) or at/below the stop
  5 initial stop: signal close - 3 x ATR14 (Wilder's average true range, 14 days)
  6 trailing stop: after each later close, stop = max(stop, highest close since entry - 3 x ATR14); never moves down.
    The stop is watched all day (live: every check; backtest: the day's low, a gap below it fills at the open)
  7 trend exit: a daily close below SMA50 sells at the next open (live: right after that close)
  8 size: lose at most 1% of the book if the stop is hit, counting both trading costs and a 1% gap buffer;
    never more than 50% of the book in the coin, and never more than the live per-swap limit
  9 skip a trade whose expected costs would eat more than 25% of the 1% risk budget (too small to be worth it)"""
VERSION="trend-rider v2"
P=dict(sma_fast=20,sma_slow=50,breakout=20,atr_n=14,atr_mult=3.0,risk=0.01,max_pos=0.5,gap_buffer=0.01,max_cost_share=0.25,min_usd=1.0,
       max_chase=0.05)    # max_chase: a live entry is skipped if the price is already 5% above the signal close
def params(over=None):return dict(P,**(over or {}))
def sma(xs,n):
    out=[None]*len(xs);run=0.0
    for i,x in enumerate(xs):
        run+=x
        if i>=n:run-=xs[i-n]
        if i>=n-1:out[i]=run/n
    return out
def atr_wilder(h,l,c,n):
    """Wilder's ATR: TR_i = max(high-low, |high-prev close|, |low-prev close|); first value = mean of the first n TRs
    (from day 1, which has a previous close); then ATR = (ATR_prev*(n-1) + TR)/n."""
    out=[None]*len(c);trs=[]
    for i in range(1,len(c)):
        tr=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]));trs.append(tr)
        if len(trs)==n:out[i]=sum(trs)/n
        elif len(trs)>n:out[i]=(out[i-1]*(n-1)+tr)/n
    return out
class Daily:
    """Indicators for daily candles [(ts,o,h,l,c,v), ...] sorted by ts, all closed."""
    def __init__(s,rows,p=None):
        p=s.p=params(p);s.rows=rows
        s.ts=[r[0] for r in rows];s.o=[r[1] for r in rows];s.h=[r[2] for r in rows];s.l=[r[3] for r in rows];s.c=[r[4] for r in rows]
        s.sf=sma(s.c,p["sma_fast"]);s.ss=sma(s.c,p["sma_slow"]);s.atr=atr_wilder(s.h,s.l,s.c,p["atr_n"])
    def ready(s,i):return i>=max(s.p["sma_slow"]-1,s.p["breakout"],s.p["atr_n"]) and s.ss[i] is not None and s.atr[i] is not None
    def uptrend(s,i):return s.ready(i) and s.c[i]>s.ss[i] and s.sf[i]>s.ss[i]
    def prev_high(s,i):return max(s.c[i-s.p["breakout"]:i])
    def breakout(s,i):return s.ready(i) and s.c[i]>s.prev_high(i)
    def trend_exit(s,i):return s.ss[i] is not None and s.c[i]<s.ss[i]
    def trail(s,i,hi):return hi-s.p["atr_mult"]*s.atr[i]
    def signal(s,i):
        """Entry decision at the close of day i -> None or dict(ref, stop, stop_pct, features)."""
        if not(s.uptrend(i) and s.breakout(i)):return None
        ref=s.c[i];stop=ref-s.p["atr_mult"]*s.atr[i]
        if stop<=0:return None
        return dict(ref=ref,stop=stop,stop_pct=(ref-stop)/ref,ts=s.ts[i],features=dict(close=round(ref,4),sma20=round(s.sf[i],4),sma50=round(s.ss[i],4),
            high20=round(s.prev_high(i),4),atr14=round(s.atr[i],4),stop_pct=round((ref-stop)/ref,4)))
    def state(s,i):
        """Plain-words snapshot for /trend and the dashboard."""
        if not s.ready(i):return dict(ready=False)
        return dict(ready=True,close=s.c[i],sma20=s.sf[i],sma50=s.ss[i],high20=s.prev_high(i),atr=s.atr[i],uptrend=s.uptrend(i),breakout=s.breakout(i),
                    signal=bool(s.signal(i)),trend_exit=s.trend_exit(i))
def size_usd(equity,stop_pct,cost=0.0015,p=None,cap_usd=None):
    """Dollar size so that hitting the stop loses about `risk` of the book, INCLUDING both trading costs and a gap buffer."""
    p=params(p);eff=stop_pct+2*cost+p["gap_buffer"]
    size=equity*p["risk"]/eff if eff>0 else 0
    size=min(size,equity*p["max_pos"])
    if cap_usd is not None:size=min(size,cap_usd)
    return max(0.0,size)
def worth_it(size,equity,cost=0.0015,fee_usd=0.0,p=None):
    """-> (ok, reason). Expected round-trip costs must stay under max_cost_share of the risk budget."""
    p=params(p)
    if size<p["min_usd"]:return False,f"size ${size:.2f} below ${p['min_usd']:.0f} minimum"
    est=size*2*cost+2*fee_usd;budget=equity*p["risk"]*p["max_cost_share"]
    if est>budget:return False,f"costs ${est:.3f} > {p['max_cost_share']:.0%} of the ${equity*p['risk']:.2f} risk"
    return True,"ok"
