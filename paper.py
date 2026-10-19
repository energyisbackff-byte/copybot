"""Paper broker, one instance per book (copy, consensus, trend...). Every buy is a proposal that goes through the risk gate
(gate checks below, in order, first failure wins); every decision is logged to b_signals. Sells are never blocked.
State lives in the b_* tables and kv keys '<book>:<name>', so the v1 tables stay untouched for a rollback."""
from tgfmt import term,kv,short,usd as fusd,esc,SEP,PAPER
import time,json,logging,statistics as st,datetime as dt
from chain import jup_price,jup_prices,jup_quote,decimals,USDC,sol_usd
import veto,risk,ledger
from activity import act
log=logging.getLogger("bot")
PAPER_BOOKS=("copy","consensus","trend","narrative","perps","perps-grok")
EXIT_DEF=dict(mode="flat",atr_mult=1.5,atr_tf_minutes=15,min_stop_pct=0.06,max_stop_pct=0.25,tp1_r=1.0,tp1_portion=0.5,tp2_r=3.0,trail_r=1.0,latency_max_share=0.5)
WEEKLY="weekly_loss_limit_pct"
def book_params(cfg,book):
    """Book settings = the global paper:/safeguards: values, overridden by books.<book>."""
    b=dict((cfg.get("books") or {}).get(book) or {});p=dict(cfg["paper"]);g=dict(cfg["safeguards"])
    for k,v in b.items():
        if k in g or k==WEEKLY:g[k]=v
        else:p[k]=v
    return p,g,b
def gate_cfg(cfg):
    return dict(dict(max_signal_age_s=120,veto_enabled=True,max_top10_share=0.30,max_roundtrip_loss_pct=0.06,
        max_coin_exposure_pct=0.15,storm_drop_pct=0.05,storm_pause_minutes=60),**(cfg.get("gate") or {}))
def total_equity(db,cfg):
    return sum(db.kv_get(f"{b}:last_equity",db.kv_get(f"{b}:cash",0)) or 0 for b in PAPER_BOOKS if ((cfg.get("books") or {}).get(b) or {}).get("enabled",b=="copy"))
def storm(db,cfg):
    """SOL fell more than storm_drop_pct within an hour -> no new buys anywhere for storm_pause_minutes."""
    g=gate_cfg(cfg);now=time.time();until=db.kv_get("storm_until",0)
    if now<until:return f"market storm: SOL fell {g['storm_drop_pct']:.0%}+ in 1h, paused {int((until-now)/60)}m more"
    try:px=sol_usd()
    except Exception:return None
    h=[x for x in (db.kv_get("sol_hist") or []) if now-x[0]<3600]
    if not h or now-h[-1][0]>=60:h.append([now,px]);db.kv_set("sol_hist",h)
    top=max(p for _,p in h)
    if px<top*(1-g["storm_drop_pct"]):
        db.kv_set("storm_until",now+g["storm_pause_minutes"]*60);return f"market storm: SOL -{1-px/top:.1%} in 1h"
    return None
class Paper:
    def __init__(s,db,hel,cfg,notify,book="copy"):
        s.db,s.hel,s.cfg,s.n,s.book=db,hel,cfg,notify,book;s.p,s.g,s.b=book_params(cfg,book);s.gc=gate_cfg(cfg)
        if s.kget("cash") is None:
            s.kset("cash",s.p["start_equity_usd"]);s.kset("peak",s.p["start_equity_usd"]);s.kset("started",time.time())
        # limits the dashboard shows for this book (it reads the db, not the config)
        s.kset("limits",dict(start=s.p["start_equity_usd"],max_pos=s.p["max_open_positions"],daily=s.g["daily_loss_limit_pct"],
            max_dd=s.g["max_drawdown_pct"],max_position_pct=s.p["max_position_pct"],enabled=s.enabled))
    # ---- per-book state ----------------------------------------------------------------
    def K(s,name):return f"{s.book}:{name}"
    def kget(s,name,d=None):return s.db.kv_get(s.K(name),d)
    def kset(s,name,v):s.db.kv_set(s.K(name),v)
    cash=property(lambda s:s.kget("cash"))
    @property
    def enabled(s):return s.b.get("enabled",s.book=="copy")
    def label(s):return PAPER if s.book=="copy" else f"PAPER · {s.book}"
    def open_positions(s):return s.db.q("select id,trader,mint,tokens,cost,opened from b_positions where book=? and status='open'",(s.book,))
    def equity(s,mark=True):
        v=0;pos=s.open_positions();px=jup_prices([m for _,_,m,_,_,_ in pos]) if mark and pos else {}
        for pid,tr,m,tok,cost,_ in pos:
            p=px.get(m);v+=tok*p if p else cost
        return s.cash+v+(s.kget("swept") or 0)
    def tradable(s,mark=True):
        """Balance the book trades with: equity minus the swept profit pot (never traded again)."""
        return s.equity(mark)-(s.kget("swept") or 0)
    def blocked(s):
        if s.db.kv_get("panic"):return "panic stop active (/panic)"
        if not s.enabled:return "book switched off in config"
        if s.kget("paused"):return "book paused (/book)"
        if s.kget("shutdown"):return "drawdown switch tripped"
        day=dt.datetime.utcnow().date().isoformat();ds=s.kget("day_start") or {}
        if ds.get("day")==day and s.kget("last_equity",ds["eq"])<ds["eq"]*(1-s.g["daily_loss_limit_pct"]):return "daily loss limit hit"
        wk=s.g.get("weekly_loss_limit_pct");ws=s.kget("week_start") or {}
        if wk and ws.get("week")==dt.datetime.utcnow().strftime("%G-W%V") and s.kget("last_equity",ws["eq"])<ws["eq"]*(1-wk):return "weekly loss limit hit"
        if s.db.kv_get("all:paused"):return "all books paused (/pause)"
        return risk.entry_block(s.db,s.cfg,s.book)
    def signal(s,source,mint,side,decision,reason,size=None,features=None):
        try:s.db.ins("b_signals",ts=time.time(),book=s.book,source=source,mint=mint,side=side,decision=decision,reason=reason,size_usd=size,features=json.dumps(features or {}))
        except Exception as e:log.warning("signal log: %s",e)
    # ---- execution simulation ----------------------------------------------------------
    def _sim(s,inm,outm,raw):
        """Quote now (decision), wait simulated sign+send+land latency, re-quote: the second quote is our fill."""
        bps=s.p.get("quote_slippage_bps",300);q1=jup_quote(inm,outm,raw,bps,True)
        time.sleep(max(0,s.p.get("exec_latency_s",1.5)))
        q2=jup_quote(inm,outm,raw,bps,True);return q1,q2,time.time()
    def _fill(s,**k):
        k.setdefault("ts",time.time());k["book"]=s.book
        bt,dtc,ft=k.get("trader_block_ts"),k.get("detect_ts"),k.get("fill_ts")
        if bt and dtc:k["detect_ms"]=(dtc-bt)*1000
        if bt and ft:k["total_ms"]=(ft-bt)*1000
        s.db.ins("b_fills",**k);return k
    def _trade(s,**k):s.db.ins("b_trades",book=s.book,**k)
    def _latrows(s,f):
        d=f.get("detect_ms");t=f.get("total_ms");sl=f.get("slip_bps")
        return [kv("detect",f"{d/1000:.1f}s") if d is not None else None,kv("total",f"{t/1000:.1f}s") if t is not None else None,
                kv("slip",f"{sl:+.0f} bps vs trader") if sl is not None else None]
    # ---- buys: the risk gate ---------------------------------------------------------------
    def on_buy(s,trader,mint,usd,amount,sig,tm,meta=None,size_usd=None):
        """Proposal to buy `mint` because `trader` did (usd/amount = their trade, usd=None = size from the book only).
        Returns the position id when filled, else None."""
        tm=tm or {};now=time.time();g=s.gc;ck=[]   # ck = checklist shown by the dashboard scene: [name, value, ok]
        def refuse(why,quiet=False,logged=False,size=None,feat=None):
            if s.enabled:act(s.db,"skip",book=s.book,trader=trader,mint=mint,reason=why,checks=ck,size=size)
            s.signal(trader,mint,"buy","refused",why,size,feat);return s.skip(trader,mint,sig,why,quiet=quiet,tm=tm,logged=logged)
        why=s.blocked()
        if why:return refuse(why,quiet=not s.enabled)
        bt=tm.get("block_ts")
        if bt and now-bt>g["max_signal_age_s"]:return refuse(f"stale signal: seen {now-bt:.0f}s after the trade")
        if s.db.q("select 1 from b_positions where book=? and status='open' and mint=?",(s.book,mint)):
            return refuse("already holding (add-on ignored)",quiet=True)
        why=risk.entry_block(s.db,s.cfg,s.book,coin=mint,narrative=(meta or {}).get("narrative"))
        if why:return refuse(why)
        if len(s.open_positions())>=s.p["max_open_positions"]:return refuse("max open positions")
        eq=s.tradable(mark=False);cap=s.p["max_position_pct"]*eq
        size=min(size_usd,cap) if size_usd is not None else cap if usd is None else min(s.p["size_multiplier"]*usd,cap)
        E=s.exit_plan_cfg();pre=None
        size=min(size,s.cash/(1+s.p["fee_pct"])-s.p["gas_usd"])
        held=s.db.q("select coalesce(sum(cost),0) from b_positions where status='open' and mint=?",(mint,))[0][0]
        room=float("inf") if mint in veto.ALLOW else g["max_coin_exposure_pct"]*max(total_equity(s.db,s.cfg),eq)-held   # cap is for memecoins
        if room<size:size=room
        if size<s.p["min_order_usd"]:
            return refuse(f"size ${max(size,0):.2f} below minimum"+(" (coin exposure cap)" if room<s.p["min_order_usd"] else " / no cash"),size=size)
        why=storm(s.db,s.cfg)
        if why:return refuse(why)
        px,liq=jup_price(mint)
        ck.append(["liquidity",f"${liq:,.0f}",liq>=s.g["min_liquidity_usd"]])
        if liq<s.g["min_liquidity_usd"]:return refuse(f"liquidity ${liq:,.0f} < ${s.g['min_liquidity_usd']:,}",size=size)
        feat={"liq":liq}
        if g["veto_enabled"]:
            ok,why,info=veto.check(s.hel,mint,g["max_top10_share"]);feat.update(info)
            if "mint_auth" in info:
                ck.append(["mint auth","renounced" if not info.get("mint_auth") else "ACTIVE",not info.get("mint_auth")])
                ck.append(["freeze auth","renounced" if not info.get("freeze_auth") else "ACTIVE",not info.get("freeze_auth")])
            t10=info.get("top10")
            ck.append(["top10 hold",f"{t10:.0%}" if isinstance(t10,(int,float)) else "n/a",(t10<=g["max_top10_share"]) if isinstance(t10,(int,float)) else None])
            if not ok and not any(c[2] is False for c in ck):ck.append(["safety",why.replace("safety veto: ","")[:28],False])
            if not ok:return refuse(why,size=size,feat=feat)
        if E:   # the volatility stop decides how much may be at risk (looked up only for coins that passed the checks above)
            pre=s._stop_pct(mint,E);rf,rnote=risk.risk_fraction(s.db,s.cfg,s.book)
            rs=risk.size_for_risk(eq,pre["stop_pct"],s.p["fee_pct"],rf)
            if rs<size:size=rs
            if size<s.p["min_order_usd"]:return refuse(f"size ${max(size,0):.2f} below minimum (risk {rf:.2%} at a {pre['stop_pct']:.0%} stop)",size=size,feat=feat)
        dec=decimals(s.hel,mint);q1,q2,ft=s._sim(USDC,mint,size*1e6);trader_px=usd/amount if usd and amount else None
        base=dict(sig=sig,trader=trader,mint=mint,side="buy",trader_block_ts=bt,detect_ts=tm.get("detect_ts"),fill_ts=ft,trader_px=trader_px,size_usd=size)
        if q2[0] is None:
            s._fill(status="failed",reason=f"quote failed ({q2[2]})",**base);return refuse(f"FILL FAILED: no Jupiter route/quote ({q2[2]})",logged=True,size=size,feat=feat)
        tokens=q2[0]/10**dec*(1-s.p.get("slippage_pct",0));our_px=size/tokens;imp=q2[1]
        qpx=(size/(q1[0]/10**dec)) if q1[0] else None;slip=(our_px/trader_px-1)*1e4 if trader_px else None
        ck.append(["price impact",f"{imp:.2%}",imp<=s.g["max_price_impact_pct"]])
        if imp>s.g["max_price_impact_pct"]:
            s._fill(status="failed",reason=f"impact {imp:.1%}",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,**base)
            return refuse(f"FILL FAILED: price impact {imp:.1%} > {s.g['max_price_impact_pct']:.0%}",logged=True,size=size,feat=feat)
        back=jup_quote(mint,USDC,q2[0],s.p.get("quote_slippage_bps",300),True)   # can we get out? buy -> instant sell
        rt=1-(back[0]/1e6)/size if back[0] else None;feat["roundtrip"]=rt
        ck.append(["sell route","none" if rt is None else f"-{rt:.1%}",rt is not None and rt<=g["max_roundtrip_loss_pct"]])
        if rt is None or rt>g["max_roundtrip_loss_pct"]:
            s._fill(status="failed",reason="sell check",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,**base)
            return refuse("sell check: no sell route" if rt is None else f"sell check: buy then sell loses {rt:.1%}",logged=True,size=size,feat=feat)
        fee=size*s.p["fee_pct"]+s.p["gas_usd"];cost=size+fee
        if E:                                     # exit plan BEFORE paying: stop, first target, final target, fee edge
            x=s._plan(cost/tokens,pre,E);costs_rt=(rt or 0)+2*s.p["fee_pct"]+2*s.p["gas_usd"]/size
            ok,etxt=risk.fee_edge(x["tp1_pct"],costs_rt,risk.charter(s.cfg,s.book)["fee_edge_mult"]);feat.update(stop_pct=x["stop_pct"],tp1_pct=x["tp1_pct"],costs_rt=costs_rt,stop_src=x["src"])
            ck.append(["fee edge",etxt,ok])
            if not ok:
                s._fill(status="failed",reason="fee edge",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,**base)
                return refuse(f"fee edge: {etxt}",logged=True,size=size,feat=feat)
            if slip is not None and slip/1e4>E["latency_max_share"]*x["tp1_pct"]:
                s._fill(status="failed",reason="latency",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,**base)
                return refuse(f"late: we pay {slip/100:.1f}% more than the trader, over half the first target (+{x['tp1_pct']:.1%})",logged=True,size=size,feat=feat)
            x.update(risk_usd=size*(x["stop_pct"]+2*s.p["fee_pct"]),buy_fee=fee,risk_note=rnote);meta=dict(meta or {},x=x)
        s.kset("cash",s.cash-cost)
        pid=s.db.ins("b_positions",book=s.book,trader=trader,mint=mint,tokens=tokens,cost=cost,opened=ft,status="open",open_sig=sig,meta=json.dumps(meta or {}))
        f=s._fill(status="filled",our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,fee_usd=fee,**base)
        s._trade(ts=ft,trader=trader,mint=mint,side="buy",usd=cost,tokens=tokens,price=our_px,pnl=0,sig=sig,note=f"impact {imp:.2%}"+(f" slip {slip:+.0f}bps" if slip is not None else "")+(f" lat {f['total_ms']/1000:.1f}s" if f.get('total_ms') else ""))
        s.signal(trader,mint,"buy","filled","passed every check",size,feat)
        act(s.db,"buy",book=s.book,trader=trader,mint=mint,size=size,price=our_px,impact=imp,latency_ms=f.get("total_ms"),detect_ms=f.get("detect_ms"),checks=ck)
        s.n.send(term("exec buy --paper",[kv("mode",s.label()),SEP,kv("trader",trader),kv("token",short(mint)),kv("size",fusd(size)),kv("fees",fusd(fee)),
            kv("our px",f"${our_px:.6g}"),kv("trd px",f"${trader_px:.6g}") if trader_px else None,kv("impact",f"{imp:.2%}"),kv("exit",f"-{rt:.1%} round trip"),kv("liq",f"${liq:,.0f}")]+s._latrows(f)+[SEP,kv("cash",fusd(s.cash))],
            ("OK","🟢 PAPER BUY filled"),f"<code>{esc(mint)}</code>")+(s._plan_html(meta["x"]) if E else ""))
        return pid
    # ---- exit plan: volatility stop, 50% at the first target, breakeven, trailing, fixed final target --------
    def exit_plan_cfg(s):
        e=dict(EXIT_DEF,**s.exits_cfg())
        return e if e.get("enabled") and e.get("mode")=="atr" else None
    def _stop_pct(s,mint,E):
        a=None
        if mint not in veto.ALLOW:
            try:
                import feeds;F=getattr(s,"_feeds",None) or feeds.Feeds(s.db,s.cfg);s._feeds=F;a=F.atr_pct(mint,E["atr_tf_minutes"])
            except Exception as ex:log.info("atr %s: %s",mint[:8],ex)
        if a:return dict(stop_pct=min(E["max_stop_pct"],max(E["min_stop_pct"],E["atr_mult"]*a)),atr_pct=a,src=f"{E['atr_mult']:g}x ATR{E['atr_tf_minutes']}m")
        return dict(stop_pct=E.get("stop_loss_pct") or 0.15,atr_pct=None,src="flat (no candles)")
    @staticmethod
    def _plan(entry_px,pre,E):
        sp=pre["stop_pct"]
        return dict(v=1,src=pre["src"],atr_pct=pre["atr_pct"],entry_px=entry_px,stop_pct=sp,stop0=entry_px*(1-sp),stop_px=entry_px*(1-sp),
            tp1_pct=sp*E["tp1_r"],tp1_px=entry_px*(1+sp*E["tp1_r"]),tp1_portion=E["tp1_portion"],tp2_px=entry_px*(1+sp*E["tp2_r"]),
            trail_pct=sp*E["trail_r"],hi=entry_px,tp1_done=False,opened=time.time())
    @staticmethod
    def _plan_html(x):
        return esc(f"plan: stop ${x['stop_px']:.6g} (-{x['stop_pct']:.1%}, {x['src']}) · sell {x['tp1_portion']:.0%} at ${x['tp1_px']:.6g} (+{x['tp1_pct']:.1%}), "
            f"then stop to breakeven and trail · final target ${x['tp2_px']:.6g}. Targets are intended prices: fills can differ.")
    # ---- sells: trader copies, our own exits, retries ------------------------------------
    EXIT_TAG={"trader_sold":"SELL","stop_loss":"STOP-LOSS","take_profit":"TAKE-PROFIT","max_hold":"TIME EXIT","trend_break":"TREND EXIT","panic":"PANIC SELL"}
    def on_sell(s,trader,mint,frac,usd,amount,sig,tm):
        """A watched trader sold. Matches positions opened by that trader, including consensus positions ('a+b')."""
        trader_px=usd/amount if amount and usd else None
        for pid,tr in s.db.q("select id,trader from b_positions where book=? and status='open' and mint=?",(s.book,mint)):
            if tr==trader or trader in tr.split("+"):s._sell(pid,frac,"trader_sold","trader sold",sig=sig,tm=tm,trader_px=trader_px)
    def _sell(s,pid,frac,reason,note,sig=None,tm=None,trader_px=None,attempt=0):
        """Sell `frac` of position `pid` at a real Jupiter quote taken after the simulated latency.
        On a failed quote the sell is queued for automatic retry. Returns True when filled."""
        row=s.db.q("select trader,mint,tokens,cost from b_positions where id=? and book=? and status='open'",(pid,s.book))
        if not row: s._pending_drop(pid);return False
        trader,mint,tok,cost=row[0];tm=tm or {}
        frac=1.0 if frac>0.97 else max(0,min(1,frac));sell=tok*frac;dec=decimals(s.hel,mint)
        q1,q2,ft=s._sim(mint,USDC,sell*10**dec)
        sig=sig or f"{reason}:{s.book}:{pid}:{int(ft)}"
        base=dict(sig=sig,trader=trader,mint=mint,side="sell",trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"),fill_ts=ft,trader_px=trader_px)
        if q2[0] is None:
            s._fill(status="failed",reason=f"{reason}: no route ({q2[2]})",**base)
            if s._pending_add(pid,frac,reason,note):
                s.n.send(term("exec sell --paper",[kv("mode",s.label()),SEP,kv("trader",trader),kv("token",short(mint)),kv("why",note),kv("error",f"no Jupiter route ({q2[2]})"),SEP,
                    "position kept; retrying","automatically with backoff"],("ERR","🔴 PAPER SELL failed"),f"<code>{esc(mint)}</code>"))
            return False
        gross=q2[0]/1e6;imp=q2[1];our_px=gross/sell if sell else 0
        fee=gross*s.p["fee_pct"]+s.p["gas_usd"];proceeds=gross*(1-s.p.get("slippage_pct",0))-fee;basis=cost*frac;pnl=proceeds-basis
        slip=(1-our_px/trader_px)*1e4 if trader_px else None;qpx=(q1[0]/1e6/sell) if q1[0] and sell else None
        s.kset("cash",s.cash+proceeds)
        if frac>=1:s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(ft,pnl,pid))
        else:s.db.x("update b_positions set tokens=?,cost=?,realized=realized+? where id=?",(tok-sell,cost-basis,pnl,pid))
        f=s._fill(status="filled",reason=reason,our_px=our_px,quote_px=qpx,slip_bps=slip,impact=imp,size_usd=gross,fee_usd=fee,**base)
        s._trade(ts=ft,trader=trader,mint=mint,side="sell",usd=proceeds,tokens=sell,price=our_px,pnl=pnl,sig=sig,note=(note+" · " if reason!="trader_sold" else "")+f"impact {imp:.2%}"+(f" slip {slip:+.0f}bps" if slip is not None else "")+(f" lat {f['total_ms']/1000:.1f}s" if f.get('total_ms') else ""))
        s._pending_drop(pid)
        s._after_sell(pid,trader,mint,reason,note,pnl,frac,our_px,fee,basis,closed=frac>=1)
        act(s.db,"sell",book=s.book,trader=trader,mint=mint,pnl=pnl,pct=(pnl/basis if basis else None),why=note,portion=frac,price=our_px,latency_ms=f.get("total_ms"))
        tag=s.EXIT_TAG.get(reason,"SELL");title=f"PAPER {tag} {frac:.0%}" if reason=="trader_sold" else f"PAPER {tag}"
        s.n.send(term("exec sell --paper",[kv("mode",s.label()),SEP,kv("trader",trader),kv("token",short(mint)),kv("why",note+(f" (retry {attempt})" if attempt else "")),kv("portion",f"{frac:.0%}"),
            kv("our px",f"${our_px:.6g}"),kv("trd px",f"${trader_px:.6g}") if trader_px else None,kv("pnl",fusd(pnl,True)+" net"),kv("fees",fusd(fee))]+s._latrows(f)+[SEP,kv("cash",fusd(s.cash))],
            ("OK",("🟢 " if pnl>=0 else "🔴 ")+title),f"<code>{esc(mint)}</code>"))
        return True
    def _after_sell(s,pid,trader,mint,reason,note,pnl,frac,px,fee,basis,closed):
        """Ledger line, risk bookkeeping (stop-outs, cooldowns, narrative failures) and reviews. Never breaks a sell."""
        try:
            m=json.loads((s.db.q("select meta from b_positions where id=?",(pid,)) or [("{}",)])[0][0] or "{}");x=m.get("x") or {}
            try:solp=sol_usd()
            except Exception:solp=None
            res=("partial " if not closed else "")+("win" if pnl>0 else "loss" if pnl<0 else "flat")
            ledger.record(s.db,book=s.book,asset=short(mint),dir="long",narrative=m.get("narrative_name"),score=m.get("score"),entry=x.get("entry_px") or (m.get("entry")),
                exit=px,size=basis,stop=x.get("stop0") or m.get("stop"),target=x.get("tp1_px") if not x.get("tp1_done") or not closed else x.get("tp2_px"),trigger=reason,
                market=f"SOL ${solp:.2f}" if solp else "–",result=res,pnl=pnl,fees=fee+(x.get("buy_fee") or 0)*frac,notes=note,source=str(trader)[:40])
            if closed or reason in risk.STOP_REASONS:risk.on_exit(s.db,s.cfg,s.book,mint,reason,pnl+(0 if closed else 0),narrative=m.get("narrative"))
            if closed:ledger.maybe_review(s.db,s.book,s.n)
        except Exception:log.exception("after-sell hooks (sell itself is done)")
    def _pending(s): return s.kget("pending_sells") or {}
    def _pending_add(s,pid,frac,reason,note):
        """Queue a failed sell for retry. Returns True if it was not queued before (alert once)."""
        P=s._pending();k=str(pid);new=k not in P;now=time.time()
        e=P.get(k) or {"first":now,"tries":0}
        e.update(frac=max(frac,e.get("frac",0)),reason=reason,note=note,next=now+s._backoff(e["tries"]))
        P[k]=e;s.kset("pending_sells",P);return new
    def _pending_drop(s,pid):
        P=s._pending()
        if str(pid) in P:P.pop(str(pid));s.kset("pending_sells",P)
    @staticmethod
    def _backoff(tries): return min(600,30*2**min(tries,5))
    def exits_cfg(s): return dict(s.cfg.get("exits") or {},**(s.b.get("exits") or {}))
    def retry_sells(s):
        e=s.exits_cfg();now=time.time()
        for k,p in list(s._pending().items()):
            if now<p.get("next",0):continue
            if now-p.get("first",now)>=e.get("sell_retry_give_up_hours",24)*3600:s._write_off(int(k),p);continue
            P=s._pending();P[k]=dict(p,tries=p.get("tries",0)+1,next=now+s._backoff(p.get("tries",0)+1));s.kset("pending_sells",P)
            try:s._sell(int(k),p.get("frac",1.0),p.get("reason","retry"),p.get("note","retry"),attempt=p.get("tries",0)+1)
            except Exception as ex:log.warning("retry sell %s: %s",k,ex)
    def check_exits(s):
        """Retry failed sells, then apply stop-loss / take-profit / max-hold to open positions."""
        s.retry_sells();e=s.exits_cfg();now=time.time()
        if not e.get("enabled"):return
        pos=s.open_positions()
        if not pos:return
        px=jup_prices([m for _,_,m,_,_,_ in pos]);P=s._pending()
        sl,tp,mh=e.get("stop_loss_pct") or 0,e.get("take_profit_pct") or 0,e.get("max_hold_hours") or 0
        metas={pid:json.loads(mt or "{}") for pid,mt in s.db.q("select id,meta from b_positions where book=? and status='open'",(s.book,))}
        for pid,tr,m,tok,cost,op in pos:
            p=px.get(m);x=(metas.get(pid) or {}).get("x")
            if x and x.get("v")==1:
                if str(pid) in P:continue                 # a retry is already selling it
                s._plan_exit(pid,m,p,x,metas[pid],e,op,now);continue
            ch=(tok*p/cost-1) if p and cost>0 else None;why=None
            if ch is not None and sl and ch<=-sl:why=("stop_loss",f"stop-loss {ch:+.1%}")
            elif ch is not None and tp and ch>=tp:why=("take_profit",f"take-profit {ch:+.1%}")
            elif mh and op and now-op>=mh*3600:why=("max_hold",f"held {mh:g}h max")
            if not why:continue
            s.exit_position(pid,*why,pending=P)
    def _plan_exit(s,pid,mint,p,x,meta,e,op,now):
        """Planned exits on the live price: stop (or breakeven / trailing stop), 50% at the first target, the fixed final
        target, and the time limit. The stop only ever moves up; the targets never move."""
        if p is None:return
        mh=e.get("max_hold_hours") or 0
        if p<=x["stop_px"]:
            r="trail_stop" if x["tp1_done"] and x["stop_px"]>x["entry_px"]*1.001 else "breakeven_stop" if x["tp1_done"] else "stop_loss"
            return s.exit_position(pid,r,f"{r.replace('_',' ')} ${x['stop_px']:.6g} (price ${p:.6g}, {p/x['entry_px']-1:+.1%})")
        if p>=x["tp2_px"]:return s.exit_position(pid,"take_profit",f"final target ${x['tp2_px']:.6g} reached (price ${p:.6g}, {p/x['entry_px']-1:+.1%})")
        changed=False
        if p>x["hi"]:x["hi"]=p;changed=True
        if not x["tp1_done"] and p>=x["tp1_px"]:
            x["tp1_done"]=True;x["stop_px"]=max(x["stop_px"],x["entry_px"]*(1+2*s.p["fee_pct"]));changed=True
            s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,x=x)),pid))
            log.info("TP1 %s #%s",s.book,pid)
            try:s._sell(pid,x["tp1_portion"],"take_profit_1",f"first target ${x['tp1_px']:.6g} (+{x['tp1_pct']:.1%}): sold {x['tp1_portion']:.0%}, stop to breakeven")
            except Exception as ex:log.warning("tp1 sell %s: %s",pid,ex)
            return
        if x["tp1_done"]:
            t=x["hi"]*(1-x["trail_pct"])
            if t>x["stop_px"]:x["stop_px"]=t;changed=True
        if changed:s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,x=x)),pid))
        if mh and op and now-op>=mh*3600:s.exit_position(pid,"max_hold",f"held {mh:g}h max")
    def exit_position(s,pid,reason,note,pending=None):
        P=s._pending() if pending is None else pending
        if str(pid) in P:   # already retrying: make sure the retry sells everything for this reason
            if P[str(pid)].get("frac",0)<1 or P[str(pid)].get("reason")=="trader_sold":s._pending_add(pid,1.0,reason,note)
            return
        log.info("EXIT %s #%s %s",s.book,pid,note)
        try:s._sell(pid,1.0,reason,note)
        except Exception as ex:log.warning("exit sell %s: %s",pid,ex)
    def _write_off(s,pid,p):
        """A sell has failed for too long (no route = usually rugged or dead). Close at $0 so equity stays honest."""
        row=s.db.q("select trader,mint,tokens,cost from b_positions where id=? and book=? and status='open'",(pid,s.book))
        s._pending_drop(pid)
        if not row:return
        trader,mint,tok,cost=row[0];now=time.time();sig=f"write_off:{s.book}:{pid}:{int(now)}"
        s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(now,-cost,pid))
        s._fill(sig=sig,trader=trader,mint=mint,side="sell",status="writeoff",reason="write_off",fill_ts=now,size_usd=0,fee_usd=0)
        s._trade(ts=now,trader=trader,mint=mint,side="sell",usd=0,tokens=tok,price=0,pnl=-cost,sig=sig,note=f"WRITTEN OFF: no sell route for {(now-p.get('first',now))/3600:.0f}h")
        s.n.send(term("exec sell --paper",[kv("mode",s.label()),SEP,kv("trader",trader),kv("token",short(mint)),kv("tries",p.get("tries",0)),kv("loss",fusd(-cost,True)),SEP,
            "no route to sell for "+f"{(now-p.get('first',now))/3600:.0f}h","counted as a total loss"],("ERR","🔴 PAPER WRITE-OFF"),f"<code>{esc(mint)}</code>"))
    def skip(s,trader,mint,sig,why,quiet=False,tm=None,logged=False):
        if not logged and tm is not None: s._fill(sig=sig,trader=trader,mint=mint,side="buy",status="skipped",reason=why,trader_block_ts=tm.get("block_ts"),detect_ts=tm.get("detect_ts"))
        s._trade(ts=time.time(),trader=trader,mint=mint,side="skip",usd=0,tokens=0,price=0,pnl=0,sig=sig,note=why)
        if quiet: log.info("skipped %s buy %s (%s): %s",trader,mint[:8],s.book,why)
        else: s.n.send(term("exec buy --paper",[kv("book",s.book),kv("trader",trader),kv("token",short(mint)),kv("reason",why)],("WARN","copy skipped"),f"<code>{esc(mint)}</code>"),
                        tier="digest",digest=f"{s.book}: skipped {short(mint)} ({str(trader)[:14]}): {why}")
        return None
    def exec_stats(s): return exec_stats(s.db,s.book)
    def mark(s):
        eq=s.equity();s.kset("last_equity",eq);s.db.ins("b_equity",ts=time.time(),book=s.book,equity=eq,cash=s.cash)
        peak=max(s.kget("peak") or eq,eq);s.kset("peak",peak)
        day=dt.datetime.utcnow().date().isoformat();ds=s.kget("day_start") or {}
        if ds.get("day")!=day:
            if ds and s.enabled:s.n.send(term(f"report --daily {s.book} "+ds['day'],s.summary_rows(),("OK","daily summary")))
            s.kset("day_start",{"day":day,"eq":eq})
        wk=dt.datetime.utcnow().strftime("%G-W%V")
        if (s.kget("week_start") or {}).get("week")!=wk:s.kset("week_start",{"week":wk,"eq":eq})
        if eq<peak*(1-s.g["max_drawdown_pct"]) and not s.kget("shutdown"):
            s.kset("shutdown",True);s.n.send(term("safeguard --drawdown",[kv("mode",s.label()),kv("drawdown",f"{1-eq/peak:.0%}"),kv("peak",fusd(peak)),kv("equity",fusd(eq)),SEP,"new buys STOPPED"],("ERR","🔴 SAFEGUARD tripped")))
        return eq
    def stats(s):
        tr=s.db.q("select count(*),coalesce(sum(pnl),0) from b_trades where book=? and side='sell'",(s.book,))[0]
        return dict(equity=s.kget("last_equity",s.cash),cash=s.cash,start=s.p["start_equity_usd"],open=len(s.open_positions()),sells=tr[0],realized=tr[1])
    def summary_rows(s):
        t=s.stats();x=s.exec_stats();ms=lambda v:"–" if v is None else f"{v/1000:.1f}s"
        return [kv("mode",s.label()),SEP,kv("equity",fusd(t["equity"])),kv("start",fusd(t["start"])),kv("cash",fusd(t["cash"])),
            kv("open",t["open"]),kv("sells",t["sells"]),kv("realized",fusd(t["realized"],True)),SEP,
            kv("detect",ms(x['det_med_ms'])+" med"),kv("fill",ms(x['total_med_ms'])+" med"),
            kv("slip","–" if x['slip_avg_bps'] is None else f"{x['slip_avg_bps']:+.0f} bps avg"),
            kv("fails","–" if x['fail_rate'] is None else f"{x['fail_rate']:.0%}")]
    def summary(s,title="Status"):
        t=s.stats();x=s.exec_stats()
        ms=lambda v:"–" if v is None else f"{v/1000:.1f}s";bp=lambda v:"–" if v is None else f"{v:+.0f} bps"
        fr="–" if x["fail_rate"] is None else f"{x['fail_rate']:.0%}"
        return (f"{title} [{s.book}]\nequity ${t['equity']:.2f} (start ${t['start']}) cash ${t['cash']:.2f}\nopen positions {t['open']}, sells {t['sells']}, realized ${t['realized']:+.2f}"
                f"\nexec: detect median {ms(x['det_med_ms'])} · fill median {ms(x['total_med_ms'])} · slip vs trader avg {bp(x['slip_avg_bps'])} · fill failures {fr}")

def exec_stats(db,book="copy"):
    F=db.q("select status,detect_ms,total_ms,slip_bps from b_fills where book=? and status in ('filled','failed')",(book,))
    det=[r[0] for r in db.q("select detect_ms from b_fills where book=? and detect_ms is not null",(book,))]
    fl=[r for r in F if r[0]=="filled"];sl=[r[3] for r in fl if r[3] is not None];tot=[r[2] for r in fl if r[2] is not None]
    return dict(n_fills=len(fl),n_failed=len(F)-len(fl),fail_rate=(len(F)-len(fl))/len(F) if F else None,
        det_avg_ms=st.mean(det) if det else None,det_med_ms=st.median(det) if det else None,
        total_med_ms=st.median(tot) if tot else None,slip_avg_bps=st.mean(sl) if sl else None,slip_med_bps=st.median(sl) if sl else None)
