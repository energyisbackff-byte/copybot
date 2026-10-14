"""Measured trading costs for SOL<->USDC at the live trade size, instead of an assumed number.
One sample = a real Jupiter quote buying SOL with $20 of USDC, then a quote selling that SOL straight back. The round-trip
loss already contains pool fees, spread and price impact (so nothing is counted twice); half of it is the cost per side.
The network fee (base fee + the priority-fee cap the bot allows) is added per side as a share of the trade."""
import time,statistics as st,logging
log=logging.getLogger("bot")
def sample(usd=20.0,priority_lamports=100000,slippage_bps=50):
    from live import jup_quote_full
    from chain import USDC,WSOL
    q1=jup_quote_full(USDC,WSOL,int(usd*1e6),slippage_bps);sol=int(q1["outAmount"])
    q2=jup_quote_full(WSOL,USDC,sol,slippage_bps);back=int(q2["outAmount"])/1e6
    px=usd/(sol/1e9);rt=1-back/usd;fee_usd=(5000+priority_lamports)/1e9*px
    return dict(ts=time.time(),usd=usd,rt=rt,fee_usd=fee_usd,side=rt/2+fee_usd/usd,px=px)
def measure(db,n=3,usd=20.0,priority_lamports=100000):
    """Take n samples (a few seconds apart), keep the last 200 in kv 'costs:samples'. Returns the summary or None."""
    got=[]
    for i in range(n):
        try:got.append(sample(usd,priority_lamports))
        except Exception as e:log.warning("cost sample failed: %s",e)
        if i<n-1:time.sleep(2.5)
    if got:db.kv_set("costs:samples",((db.kv_get("costs:samples") or [])+got)[-200:])
    return summary(db)
def summary(db):
    S=db.kv_get("costs:samples") or []
    if not S:return None
    side=sorted(x["side"] for x in S);p90=side[min(len(side)-1,int(len(side)*0.9))]
    return dict(n=len(S),median=st.median(side),p90=p90,rt_median=st.median(x["rt"] for x in S),fee_usd=st.median(x["fee_usd"] for x in S),last=S[-1]["ts"])
def backtest_cost(db,assumed=0.0015):
    """Cost per side for the backtest: the measured 90th percentile, but never below the assumed safety number."""
    s=summary(db)
    if not s:return assumed,"assumed (no measurements yet)"
    if s["p90"]>assumed:return s["p90"],f"measured p90 of {s['n']} quotes"
    return assumed,f"assumed {assumed:.2%} (measured p90 {s['p90']:.3%} is lower)"
