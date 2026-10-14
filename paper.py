from tgfmt import term,kv,short,usd as fusd,esc,SEP,PAPER
import time,logging,statistics as st,datetime as dt
from chain import jup_price,jup_quote,decimals,USDC
log=logging.getLogger("bot")
FILLS="""create table if not exists fills(ts real,sig text,trader text,mint text,side text,status text,reason text,
trader_block_ts real,detect_ts real,fill_ts real,detect_ms real,total_ms real,trader_px real,quote_px real,our_px real,
slip_bps real,impact real,size_usd real,fee_usd real)"""
class Paper:
    def __init__(s,db,hel,cfg,notify):
        s.db,s.hel,s.cfg,s.n=db,hel,cfg,notify;s.p=cfg["paper"];s.g=cfg["safeguards"];db.c.execute(FILLS);db.c.commit()
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
    # ---- execution simulation -------------------------------------------------
    def _sim(s,inm,outm,raw):
        """Quote now (decision), wait simulated sign+send+land latency, re-quote: the second quote is our fill."""
        bps=s.p.get("quote_slippage_bps",300);q1=jup_quote(inm,outm,raw,bps,True)
        time.sleep(max(0,s.p.get("exec_latency_s",1.5)))
        q2=jup_quote(inm,outm,raw,bps,True);return q1,q2,time.time()
    def _fill(s,**k):
        cols=["ts","sig","trader","mint","side","status","reason","trader_block_ts","detect_ts","fill_ts","detect_ms","total_ms","trader_px","quote_px","our_px","slip_bps","impact","size_usd","fee_usd"]
        k.setdefault("ts",time.time())
        bt,dtc,ft=k.get("trader_block_ts"),k.get("detect_ts"),k.get("fill_ts")
        if bt and dtc:k["detect_ms"]=(dtc-bt)*1000
        if bt and ft:k["total_ms"]=(ft-bt)*1000
        s.db.x(f"insert into fills({','.join(cols)}) values({','.join('?'*len(cols))})",[k.get(c) for c in cols])
        return k
    def _latrows(s,f):
        d=f.get("detect_ms");t=f.get("total_ms");sl=f.get("slip_bps")
        return [kv("detect",f"{d/1000:.1f}s") if d is not None else None,kv("total",f"{t/1000:.1f}s") if t is not None else None,
                kv("slip",f"{sl:+.0f} bps vs trader") if sl is not None else None]
    def _lat(s,f):
        d=f.get("detect_ms");t=f.get("total_ms");sl=f.get("slip_bps")
        return (f"\n⏱ detect {d/1000:.1f}s · total {t/1000:.1f}s" if d is not None and t is not None else "")+(f" · slip vs trader {sl:+.0f} bps" if sl is not None else "")
    def on_buy(s,trader,mint,usd,amount,sig,tm):
        why=s.blocked()
        if why:return s.skip(trader,mint,sig,why,tm=tm)
        if s.db.q("select 1 from positions where status='open' and trader=? and mint=?",(trader,mint)):return s.skip(trader,mint,sig,"already holding (trader add-on ignored)",quiet=True,tm=tm)
        if len(s.open_positions())>=s.p["max_open_positions"]:return s.skip(trader,mint,sig,"max open positions",tm=tm)
        eq=s.equity(mark=False);size=min(s.p["size_multiplier"]*usd,s.p["max_position_pct"]*eq,s.cash/(1+s.p["fee_pct"])-s.p["gas_usd"])
        if size<s.p["min_order_usd"]:return s.skip(trader,mint,sig,f"size ${size:.2f} below minimum / no cash",tm=tm)
        px,liq=jup_price(mint)
        if liq<s.g["min_liquidity_usd"]:return s.skip(trader,mint,sig,f"liquidity ${liq:,.0f} < ${s.g['min_liquidity_usd']:,}",tm=tm)
        dec=decimals(s.hel,mint);q1,q2,ft=s._sim(USDC,mint,size*1e6);trader_px=usd/amount if amount else None
        base=dict(sig=sig,trader=trader,mint=mint,side="buy",trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"),fill_ts=ft,trader_px=trader_px,size_usd=size)
        if q2[0] is None:
            f=s._fill(status="failed",reason=f"quote failed ({q2[2]})",**base);return s.skip(trader,mint,sig,f"FILL FAILED: no Jupiter route/quote ({q2[2]})",tm=tm,logged=True)
        tokens=q2[0]/10**dec*(1-s.p.get("slippage_pct",0));our_px=size/tokens;imp=q2[1]
        qpx=(size/(q1[0]/10**dec)) if q1[0] else None;slip=(our_px/trader_px-1)*1e4 if trader_px else None
        if imp>s.g["max_price_impact_pct"]:
            s._fill(status="failed",reason=f"impact {imp:.1%}",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,**base)
            return s.skip(trader,mint,sig,f"FILL FAILED: price impact {imp:.1%} > {s.g['max_price_impact_pct']:.0%}",tm=tm,logged=True)
        fee=size*s.p["fee_pct"]+s.p["gas_usd"];cost=size+fee;s.db.kv_set("cash",s.cash-cost)
        s.db.x("insert into positions(trader,mint,tokens,cost,opened,status,open_sig) values(?,?,?,?,?,?,?)",(trader,mint,tokens,cost,ft,"open",sig))
        f=s._fill(status="filled",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,fee_usd=fee,**base)
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(ft,trader,mint,"buy",cost,tokens,our_px,0,sig,f"impact {imp:.2%}"+(f" slip {slip:+.0f}bps" if slip is not None else "")+(f" lat {f['total_ms']/1000:.1f}s" if f.get('total_ms') else "")))
        s.n.send(term("exec buy --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("size",fusd(size)),kv("fees",fusd(fee)),
            kv("our px",f"${our_px:.6g}"),kv("trd px",f"${trader_px or 0:.6g}"),kv("impact",f"{imp:.2%}"),kv("liq",f"${liq:,.0f}")]+s._latrows(f)+[SEP,kv("cash",fusd(s.cash))],
            ("OK","🟢 PAPER BUY filled"),f"<code>{esc(mint)}</code>"))
    def on_sell(s,trader,mint,frac,usd,amount,sig,tm):
        r=s.db.q("select id,tokens,cost from positions where status='open' and trader=? and mint=?",(trader,mint))
        if not r:return
        pid,tok,cost=r[0];frac=1.0 if frac>0.97 else max(0,min(1,frac));sell=tok*frac;dec=decimals(s.hel,mint)
        q1,q2,ft=s._sim(mint,USDC,sell*10**dec);trader_px=usd/amount if amount and usd else None
        base=dict(sig=sig,trader=trader,mint=mint,side="sell",trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"),fill_ts=ft,trader_px=trader_px)
        if q2[0] is None:
            s._fill(status="failed",reason=f"quote failed ({q2[2]})",**base)
            s.n.send(term("exec sell --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("error",f"no Jupiter route ({q2[2]})"),SEP,"position kept; retry on next","trader sell or by hand"],("ERR","🔴 PAPER SELL failed"),f"<code>{esc(mint)}</code>"));return
        gross=q2[0]/1e6;imp=q2[1];our_px=gross/sell if sell else 0
        fee=gross*s.p["fee_pct"]+s.p["gas_usd"];proceeds=gross*(1-s.p.get("slippage_pct",0))-fee;basis=cost*frac;pnl=proceeds-basis
        slip=(1-our_px/trader_px)*1e4 if trader_px else None;qpx=(q1[0]/1e6/sell) if q1[0] and sell else None
        s.db.kv_set("cash",s.cash+proceeds)
        if frac>=1:s.db.x("update positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(ft,pnl,pid))
        else:s.db.x("update positions set tokens=?,cost=?,realized=realized+? where id=?",(tok-sell,cost-basis,pnl,pid))
        f=s._fill(status="filled",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,size_usd=gross,fee_usd=fee,**base)
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(ft,trader,mint,"sell",proceeds,sell,our_px,pnl,sig,f"impact {imp:.2%}"+(f" slip {slip:+.0f}bps" if slip is not None else "")+(f" lat {f['total_ms']/1000:.1f}s" if f.get('total_ms') else "")))
        s.n.send(term("exec sell --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("portion",f"{frac:.0%}"),
            kv("our px",f"${our_px:.6g}"),kv("trd px",f"${trader_px or 0:.6g}"),kv("pnl",fusd(pnl,True)+" net"),kv("fees",fusd(fee))]+s._latrows(f)+[SEP,kv("cash",fusd(s.cash))],
            ("OK",("🟢" if pnl>=0 else "🔴")+f" PAPER SELL {frac:.0%}"),f"<code>{esc(mint)}</code>"))
    def skip(s,trader,mint,sig,why,quiet=False,tm=None,logged=False):
        if not logged and tm is not None: s._fill(sig=sig,trader=trader,mint=mint,side="buy",status="skipped",reason=why,trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"))
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(time.time(),trader,mint,"skip",0,0,0,0,sig,why))
        if quiet: log.info("skipped copy of %s buy %s: %s",trader,mint[:8],why)
        else: s.n.send(term("exec buy --paper",[kv("trader",trader),kv("token",short(mint)),kv("reason",why)],("WARN","copy skipped"),f"<code>{esc(mint)}</code>"))
    def exec_stats(s): return exec_stats(s.db)
    def mark(s):
        eq=s.equity();s.db.kv_set("last_equity",eq);s.db.x("insert into equity values(?,?,?)",(time.time(),eq,s.cash))
        peak=max(s.db.kv_get("peak"),eq);s.db.kv_set("peak",peak)
        day=dt.datetime.utcnow().date().isoformat();ds=s.db.kv_get("day_start") or {}
        if ds.get("day")!=day:
            if ds:s.n.send(term("report --daily "+ds['day'],s.summary_rows(),("OK","daily summary")))
            s.db.kv_set("day_start",{"day":day,"eq":eq})
        if eq<peak*(1-s.g["max_drawdown_pct"]) and not s.db.kv_get("shutdown"):
            s.db.kv_set("shutdown",True);s.n.send(term("safeguard --drawdown",[kv("mode",PAPER),kv("drawdown",f"{1-eq/peak:.0%}"),kv("peak",fusd(peak)),kv("equity",fusd(eq)),SEP,"new copies STOPPED"],("ERR","🔴 SAFEGUARD tripped")))
        return eq
    def summary_rows(s):
        eq=s.db.kv_get("last_equity",s.cash);tr=s.db.q("select count(*),coalesce(sum(pnl),0) from trades where side='sell'")[0];x=s.exec_stats()
        ms=lambda v:"–" if v is None else f"{v/1000:.1f}s"
        return [kv("mode",PAPER),SEP,kv("equity",fusd(eq)),kv("start",fusd(s.p['start_equity_usd'])),kv("cash",fusd(s.cash)),
            kv("open",len(s.open_positions())),kv("sells",tr[0]),kv("realized",fusd(tr[1],True)),SEP,
            kv("detect",ms(x['det_med_ms'])+" med"),kv("fill",ms(x['total_med_ms'])+" med"),
            kv("slip","–" if x['slip_avg_bps'] is None else f"{x['slip_avg_bps']:+.0f} bps avg"),
            kv("fails","–" if x['fail_rate'] is None else f"{x['fail_rate']:.0%}")]
    def summary(s,title="Status"):
        eq=s.db.kv_get("last_equity",s.cash);tr=s.db.q("select count(*),coalesce(sum(pnl),0) from trades where side='sell'")[0];x=s.exec_stats()
        f=lambda v,u="":"–" if v is None else f"{v:.0f}{u}"
        ms=lambda v:"–" if v is None else f"{v/1000:.1f}s"
        bp=lambda v:"–" if v is None else f"{v:+.0f} bps"
        fr="–" if x["fail_rate"] is None else f"{x['fail_rate']:.0%}"
        return (f"{title}\nequity ${eq:.2f} (start ${s.p['start_equity_usd']}) cash ${s.cash:.2f}\nopen positions {len(s.open_positions())}, sells {tr[0]}, realized ${tr[1]:+.2f}"
                f"\nexec: detect median {ms(x['det_med_ms'])} · fill median {ms(x['total_med_ms'])} · slip vs trader avg {bp(x['slip_avg_bps'])} · fill failures {fr}")

def exec_stats(db):
    db.c.execute(FILLS)
    F=db.q("select status,detect_ms,total_ms,slip_bps from fills where status in ('filled','failed')")
    det=[r[0] for r in db.q("select detect_ms from fills where detect_ms is not null")]
    fl=[r for r in F if r[0]=="filled"];sl=[r[3] for r in fl if r[3] is not None];tot=[r[2] for r in fl if r[2] is not None]
    return dict(n_fills=len(fl),n_failed=len(F)-len(fl),fail_rate=(len(F)-len(fl))/len(F) if F else None,
        det_avg_ms=st.mean(det) if det else None,det_med_ms=st.median(det) if det else None,
        total_med_ms=st.median(tot) if tot else None,slip_avg_bps=st.mean(sl) if sl else None,slip_med_bps=st.median(sl) if sl else None)
