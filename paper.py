from tgfmt import term,kv,short,usd as fusd,esc,SEP,PAPER
import time,logging,statistics as st,datetime as dt
from chain import jup_price,jup_prices,jup_quote,decimals,USDC
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
    # ---- sells: trader copies, our own exits, retries ---------------------------
    EXIT_TAG={"trader_sold":"SELL","stop_loss":"STOP-LOSS","take_profit":"TAKE-PROFIT","max_hold":"TIME EXIT"}
    def on_sell(s,trader,mint,frac,usd,amount,sig,tm):
        r=s.db.q("select id from positions where status='open' and trader=? and mint=?",(trader,mint))
        if not r:return
        trader_px=usd/amount if amount and usd else None
        s._sell(r[0][0],frac,"trader_sold","trader sold",sig=sig,tm=tm,trader_px=trader_px)
    def _sell(s,pid,frac,reason,note,sig=None,tm=None,trader_px=None,attempt=0):
        """Sell `frac` of position `pid` at a real Jupiter quote taken after the simulated latency.
        On a failed quote the sell is queued for automatic retry. Returns True when filled."""
        row=s.db.q("select trader,mint,tokens,cost from positions where id=? and status='open'",(pid,))
        if not row: s._pending_drop(pid);return False
        trader,mint,tok,cost=row[0];tm=tm or {}
        frac=1.0 if frac>0.97 else max(0,min(1,frac));sell=tok*frac;dec=decimals(s.hel,mint)
        q1,q2,ft=s._sim(mint,USDC,sell*10**dec)
        sig=sig or f"{reason}:{pid}:{int(ft)}"
        base=dict(sig=sig,trader=trader,mint=mint,side="sell",trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"),fill_ts=ft,trader_px=trader_px)
        if q2[0] is None:
            s._fill(status="failed",reason=f"{reason}: no route ({q2[2]})",**base)
            if s._pending_add(pid,frac,reason,note):
                s.n.send(term("exec sell --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("why",note),kv("error",f"no Jupiter route ({q2[2]})"),SEP,
                    "position kept; retrying","automatically with backoff"],("ERR","🔴 PAPER SELL failed"),f"<code>{esc(mint)}</code>"))
            return False
        gross=q2[0]/1e6;imp=q2[1];our_px=gross/sell if sell else 0
        fee=gross*s.p["fee_pct"]+s.p["gas_usd"];proceeds=gross*(1-s.p.get("slippage_pct",0))-fee;basis=cost*frac;pnl=proceeds-basis
        slip=(1-our_px/trader_px)*1e4 if trader_px else None;qpx=(q1[0]/1e6/sell) if q1[0] and sell else None
        s.db.kv_set("cash",s.cash+proceeds)
        if frac>=1:s.db.x("update positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(ft,pnl,pid))
        else:s.db.x("update positions set tokens=?,cost=?,realized=realized+? where id=?",(tok-sell,cost-basis,pnl,pid))
        f=s._fill(status="filled",reason=reason,our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,size_usd=gross,fee_usd=fee,**base)
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(ft,trader,mint,"sell",proceeds,sell,our_px,pnl,sig,(note+" · " if reason!="trader_sold" else "")+f"impact {imp:.2%}"+(f" slip {slip:+.0f}bps" if slip is not None else "")+(f" lat {f['total_ms']/1000:.1f}s" if f.get('total_ms') else "")))
        s._pending_drop(pid)
        tag=s.EXIT_TAG.get(reason,"SELL");title=f"PAPER {tag} {frac:.0%}" if reason=="trader_sold" else f"PAPER {tag}"
        s.n.send(term("exec sell --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("why",note+(f" (retry {attempt})" if attempt else "")),kv("portion",f"{frac:.0%}"),
            kv("our px",f"${our_px:.6g}"),kv("trd px",f"${trader_px:.6g}") if trader_px else None,kv("pnl",fusd(pnl,True)+" net"),kv("fees",fusd(fee))]+s._latrows(f)+[SEP,kv("cash",fusd(s.cash))],
            ("OK",("🟢 " if pnl>=0 else "🔴 ")+title),f"<code>{esc(mint)}</code>"))
        return True
    def _pending(s): return s.db.kv_get("pending_sells") or {}
    def _pending_add(s,pid,frac,reason,note):
        """Queue a failed sell for retry. Returns True if it was not queued before (alert once)."""
        P=s._pending();k=str(pid);new=k not in P;now=time.time()
        e=P.get(k) or {"first":now,"tries":0}
        e.update(frac=max(frac,e.get("frac",0)),reason=reason,note=note,next=now+s._backoff(e["tries"]))
        P[k]=e;s.db.kv_set("pending_sells",P);return new
    def _pending_drop(s,pid):
        P=s._pending()
        if str(pid) in P:P.pop(str(pid));s.db.kv_set("pending_sells",P)
    @staticmethod
    def _backoff(tries): return min(600,30*2**min(tries,5))
    def exits_cfg(s): return s.cfg.get("exits") or {}
    def check_exits(s):
        """Retry failed sells, then apply stop-loss / take-profit / max-hold to open positions."""
        e=s.exits_cfg();now=time.time()
        for k,p in list(s._pending().items()):
            if now<p.get("next",0):continue
            if now-p.get("first",now)>=e.get("sell_retry_give_up_hours",24)*3600:s._write_off(int(k),p);continue
            P=s._pending();P[k]=dict(p,tries=p.get("tries",0)+1,next=now+s._backoff(p.get("tries",0)+1));s.db.kv_set("pending_sells",P)
            try:s._sell(int(k),p.get("frac",1.0),p.get("reason","retry"),p.get("note","retry"),attempt=p.get("tries",0)+1)
            except Exception as ex:log.warning("retry sell %s: %s",k,ex)
        if not e.get("enabled"):return
        pos=s.open_positions()
        if not pos:return
        px=jup_prices([m for _,_,m,_,_,_ in pos]);P=s._pending()
        sl,tp,mh=e.get("stop_loss_pct") or 0,e.get("take_profit_pct") or 0,e.get("max_hold_hours") or 0
        for pid,tr,m,tok,cost,op in pos:
            p=px.get(m);ch=(tok*p/cost-1) if p and cost>0 else None;why=None
            if ch is not None and sl and ch<=-sl:why=("stop_loss",f"stop-loss {ch:+.1%}")
            elif ch is not None and tp and ch>=tp:why=("take_profit",f"take-profit {ch:+.1%}")
            elif mh and op and now-op>=mh*3600:why=("max_hold",f"held {mh:g}h max")
            if not why:continue
            if str(pid) in P:   # already retrying: make sure the retry sells everything for this reason
                if P[str(pid)].get("frac",0)<1 or P[str(pid)].get("reason")=="trader_sold":s._pending_add(pid,1.0,*why)
                continue
            log.info("EXIT %s %s %s",tr,m[:8],why[1])
            try:s._sell(pid,1.0,why[0],why[1])
            except Exception as ex:log.warning("exit sell %s: %s",m[:8],ex)
    def _write_off(s,pid,p):
        """A sell has failed for too long (no route = usually rugged or dead). Close at $0 so equity stays honest."""
        row=s.db.q("select trader,mint,tokens,cost from positions where id=? and status='open'",(pid,))
        s._pending_drop(pid)
        if not row:return
        trader,mint,tok,cost=row[0];now=time.time();sig=f"write_off:{pid}:{int(now)}"
        s.db.x("update positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(now,-cost,pid))
        s._fill(sig=sig,trader=trader,mint=mint,side="sell",status="writeoff",reason="write_off",fill_ts=now,size_usd=0,fee_usd=0)
        s.db.x("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(now,trader,mint,"sell",0,tok,0,-cost,sig,f"WRITTEN OFF: no sell route for {(now-p.get('first',now))/3600:.0f}h"))
        s.n.send(term("exec sell --paper",[kv("mode",PAPER),SEP,kv("trader",trader),kv("token",short(mint)),kv("tries",p.get("tries",0)),kv("loss",fusd(-cost,True)),SEP,
            "no route to sell for "+f"{(now-p.get('first',now))/3600:.0f}h","counted as a total loss"],("ERR","🔴 PAPER WRITE-OFF"),f"<code>{esc(mint)}</code>"))
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
