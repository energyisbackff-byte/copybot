import time,logging,datetime as dt
from chain import jup_price,jup_quote,decimals,USDC
log=logging.getLogger("bot")
class Paper:
    def __init__(s,db,hel,cfg,notify):
        s.db,s.hel,s.cfg,s.n=db,hel,cfg,notify;s.p=cfg["paper"];s.g=cfg["safeguards"]
        if db.kv_get("cash") is None:
            db.kv_set("cash",s.p["start_equity_usd"]);db.kv_set("peak",s.p["start_equity_usd"])
    cash=property(lambda s:s.db.kv_get("cash"))
    def open_positions(s): return s.db.q("select id,trader,mint,tokens,cost,opened from positions where status='open'")
    def equity(s,mark=True):
        v=0
        for pid,tr,m,tok,cost,_ in s.open_positions():
            px=jup_price(m)[0] if mark else None
            v+=tok*px if px else cost
        return s.cash+v
    def blocked(s):
        if s.db.kv_get("shutdown"):return "drawdown auto-shutdown active"
        day=dt.datetime.utcnow().date().isoformat();ds=s.db.kv_get("day_start") or {}
        if ds.get("day")==day and s.db.kv_get("last_equity",ds["eq"])<ds["eq"]*(1-s.g["daily_loss_limit_pct"]):return "daily loss limit hit"
        return None
    def on_buy(s,trader,mint,usd,sig):
        why=s.blocked()
        if why:return s.skip(trader,mint,sig,why)
        if s.db.q("select 1 from positions where status='open' and trader=? and mint=?",(trader,mint)):return s.skip(trader,mint,sig,"already holding (trader add-on ignored)",quiet=True)
        if len(s.open_positions())>=s.p["max_open_positions"]:return s.skip(trader,mint,sig,"max open positions")
        eq=s.equity(mark=False);size=min(s.p["size_multiplier"]*usd,s.p["max_position_pct"]*eq,s.cash/(1+s.p["fee_pct"])-s.p["gas_usd"])
        if size<s.p["min_order_usd"]:return s.skip(trader,mint,sig,f"size ${size:.2f} below minimum / no cash")
        px,liq=jup_price(mint)
        if liq<s.g["min_liquidity_usd"]:return s.skip(trader,mint,sig,f"liquidity ${liq:,.0f} < ${s.g['min_liquidity_usd']:,}")
        out,imp=jup_quote(USDC,mint,size*1e6)
        if out is None:return s.skip(trader,mint,sig,"no Jupiter route")
        if imp>s.g["max_price_impact_pct"]:return s.skip(trader,mint,sig,f"price impact {imp:.1%} too high")
        tokens=out/10**decimals(s.hel,mint)*(1-s.p["slippage_pct"])/(1+s.p["delay_penalty_pct"])
        cost=size*(1+s.p["fee_pct"])+s.p["gas_usd"];s.db.kv_set("cash",s.cash-cost)
        s.db.x("insert into positions(trader,mint,tokens,cost,opened,status,open_sig) values(?,?,?,?,?,?,?)",(trader,mint,tokens,cost,time.time(),"open",sig))
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(time.time(),trader,mint,"buy",cost,tokens,size/tokens,0,sig,f"impact {imp:.2%}"))
        s.n.send(f"📈 PAPER BUY copying {trader}\ntoken {mint}\nsize ${size:.2f} (+fees ${cost-size:.2f})\nentry ${size/tokens:.10g}/token, liq ${liq:,.0f}\ncash ${s.cash:.2f}")
    def on_sell(s,trader,mint,frac,sig):
        r=s.db.q("select id,tokens,cost from positions where status='open' and trader=? and mint=?",(trader,mint))
        if not r:return
        pid,tok,cost=r[0];frac=1.0 if frac>0.97 else max(0,min(1,frac));sell=tok*frac
        out,imp=jup_quote(mint,USDC,sell*10**decimals(s.hel,mint))
        if out is None:
            px=jup_price(mint)[0] or 0;gross=sell*px;note="no route, marked at price"
        else: gross=out/1e6;note=f"impact {imp:.2%}"
        proceeds=gross*(1-s.p["slippage_pct"])*(1-s.p["fee_pct"])-s.p["gas_usd"];basis=cost*frac;pnl=proceeds-basis
        s.db.kv_set("cash",s.cash+proceeds)
        if frac>=1:s.db.x("update positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(time.time(),pnl,pid))
        else:s.db.x("update positions set tokens=?,cost=?,realized=realized+? where id=?",(tok-sell,cost-basis,pnl,pid))
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(time.time(),trader,mint,"sell",proceeds,sell,(gross/sell if sell else 0),pnl,sig,note))
        s.n.send(f"📉 PAPER SELL {frac:.0%} following {trader}\ntoken {mint}\nexit ${gross/sell if sell else 0:.10g}/token\nPnL after costs ${pnl:+.2f}\ncash ${s.cash:.2f}")
    def skip(s,trader,mint,sig,why,quiet=False):
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(time.time(),trader,mint,"skip",0,0,0,0,sig,why))
        (log.info if quiet else s.n.send)(f"⏭ skipped copy of {trader} buy {mint[:8]}…: {why}")
    def mark(s):
        eq=s.equity();s.db.kv_set("last_equity",eq);s.db.x("insert into equity values(?,?,?)",(time.time(),eq,s.cash))
        peak=max(s.db.kv_get("peak"),eq);s.db.kv_set("peak",peak)
        day=dt.datetime.utcnow().date().isoformat();ds=s.db.kv_get("day_start") or {}
        if ds.get("day")!=day:
            if ds:s.n.send(s.summary(f"🗓 Daily summary {ds['day']}"))
            s.db.kv_set("day_start",{"day":day,"eq":eq})
        if eq<peak*(1-s.g["max_drawdown_pct"]) and not s.db.kv_get("shutdown"):
            s.db.kv_set("shutdown",True);s.n.send(f"🛑 SAFEGUARD: drawdown {1-eq/peak:.0%} from peak ${peak:.2f}. New copies stopped.")
        return eq
    def summary(s,title="Status"):
        eq=s.db.kv_get("last_equity",s.cash);tr=s.db.q("select count(*),coalesce(sum(pnl),0) from trades where side='sell'")[0]
        return f"{title}\nequity ${eq:.2f} (start ${s.p['start_equity_usd']}) cash ${s.cash:.2f}\nopen positions {len(s.open_positions())}, sells {tr[0]}, realized ${tr[1]:+.2f}"
