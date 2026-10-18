"""Perpetual futures, long AND short, on XRP, DOGE, LINK, UNI, ATOM and SOL, priced from Hyperliquid's public data
(Jupiter Perps and Drift/Velocity do not list these coins; Hyperliquid lists all of them). PAPER books: nothing is
sent to an exchange from here. Two books run side by side on the same signals:
  perps        the risk charter: 0.25% of the book at risk per trade; leverage only as much as the margin cap needs
  perps-grok   the screenshot spec, as research in simulation: dynamic 8x-19x leverage (higher when the target is
               closer), up to half the book as margin on an A+ signal, a smaller share on an A. Liquidations, fees
               and funding are all simulated, so it shows what that spec really does.
Fixed rules (both books):
  1 decide once per hour, right after the 1h candle closes; only CLOSED candles are used
  2 regime: EMA20 and EMA50 must be more than regime_min_gap apart (trend), else chop = no trade; ATR above
    vol_spike x its recent average (up to 30 days) = abnormal market = no trade
  3 long: close > EMA50, EMA20 > EMA50, a GREEN signal candle closing above the highest close of the previous 20
    short: the mirror image (red candle closing below the lowest close of the previous 20)
  4 crowding: no long when funding is above +funding_max_h per hour, no short when it is below -funding_max_h
  5 rule zero: a corroborated CT/news narrative for that coin right now (narrative.Engine.major), and its lean must
    not contradict the direction. No narrative, no trade.
  6 before the order: stop = close -/+ atr_mult x ATR14 (the invalidation); TP1 = 1R (close 50%, stop to breakeven);
    TP2 = tp2_r x R (the rest). Targets are intended prices: a real fill can be worse (slippage, gaps).
  7 leverage never puts the liquidation price inside the stop: liquidation must sit at least (1+liq_buffer) x the
    stop distance away; capped at the coin's own maximum on Hyperliquid
  8 costs: taker fee per side, slippage from walking the real order book, funding accrued at the live hourly rate
  9 exits: stop, TP1, TP2, the opposite signal, max_hold_h, or liquidation (paper models it)
 10 risk (risk.py): coin cooldown, 2-stop-out stand-down, daily loss and drawdown switches, global kill switch, and a
    correlation cap: coins moving together (correlation > corr_max) in the same direction count as one bet."""
import time,json,math,logging,threading,statistics as st
import feeds as FD,risk,ledger
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot")
DEF=dict(enabled=True,network="main",coins=["XRP","DOGE","LINK","UNI","ATOM","SOL"],interval="1h",ema_fast=20,ema_slow=50,breakout=20,atr_n=14,
         atr_mult=1.5,tp1_r=1.0,tp1_portion=0.5,tp2_r=2.0,regime_min_gap=0.004,vol_spike=3.0,funding_max_h=0.0001,require_narrative=True,
         taker_fee=0.00045,max_hold_h=24,liq_buffer=0.5,corr_max=0.8,max_same_direction=2,check_seconds=30,exec_latency_s=1.5,a_plus=80)
BOOK_DEF={"perps":dict(enabled=True,start_equity_usd=100,sizing="charter",max_open_positions=3,max_margin_pct=0.25,daily_loss_limit_pct=0.01,max_drawdown_pct=0.05),
          "perps-grok":dict(enabled=True,start_equity_usd=100,sizing="grok",min_leverage=8,max_leverage=19,lev_k=0.12,margin_a_plus=0.5,margin_a=0.25,
                            max_open_positions=2,daily_loss_limit_pct=0.25,max_drawdown_pct=0.6)}
H=3600
# ---- indicators (pure) ----------------------------------------------------------------------------------
def ema(xs,n):
    out=[None]*len(xs);k=2/(n+1);e=None
    for i,x in enumerate(xs):
        if i==n-1:e=sum(xs[:n])/n
        elif i>=n:e=x*k+e*(1-k)
        out[i]=e
    return out
def atr(rows,n=14):
    tr=[None]+[max(rows[i][2]-rows[i][3],abs(rows[i][2]-rows[i-1][4]),abs(rows[i][3]-rows[i-1][4])) for i in range(1,len(rows))];out=[None]*len(rows);a=None
    for i in range(1,len(rows)):
        if i==n:a=sum(tr[1:n+1])/n
        elif i>n:a=(a*(n-1)+tr[i])/n
        out[i]=a
    return out
def corr(a,b):
    n=min(len(a),len(b))
    if n<24:return 0.0
    a,b=a[-n:],b[-n:];ma,mb=st.mean(a),st.mean(b);sa=math.sqrt(sum((x-ma)**2 for x in a));sb=math.sqrt(sum((x-mb)**2 for x in b))
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/(sa*sb) if sa and sb else 0.0
def rets(rows):return [rows[i][4]/rows[i-1][4]-1 for i in range(1,len(rows)) if rows[i-1][4]>0]
def signal(rows,p,funding=0.0,oi_change=None,narr=None):
    """rows: closed candles [(t,o,h,l,c,v)] oldest first. -> dict(direction, entry_ref, stop, tp1, tp2, score, why) or
    dict(direction=None, why=reason)."""
    n=max(p["ema_slow"],p["breakout"]+1,p["atr_n"]+1)+30
    if len(rows)<n:return dict(direction=None,why=f"only {len(rows)} candles (need {n})")
    c=[r[4] for r in rows];ef=ema(c,p["ema_fast"]);es=ema(c,p["ema_slow"]);a=atr(rows,p["atr_n"]);i=len(rows)-1
    o,hh,ll,cl,v=rows[i][1],rows[i][2],rows[i][3],rows[i][4],rows[i][5]
    gap=abs(ef[i]-es[i])/cl;A=[x for x in a[-30*24:] if x];med=st.mean(A) if len(A)>10 else a[i]     # recent average ATR (up to 30 days)
    prev_hi=max(c[i-p["breakout"]:i]);prev_lo=min(c[i-p["breakout"]:i])
    F=dict(close=cl,ema_fast=ef[i],ema_slow=es[i],atr=a[i],atr_pct=a[i]/cl,gap=gap,prev_hi=prev_hi,prev_lo=prev_lo,funding=funding,oi_change=oi_change,
           vol_ratio=v/max(st.mean([r[5] for r in rows[i-20:i]]),1e-12))
    if gap<p["regime_min_gap"]:return dict(direction=None,why=f"chop: averages only {gap:.2%} apart",features=F)
    if med and a[i]>p["vol_spike"]*med:return dict(direction=None,why=f"abnormal volatility: ATR {a[i]/med:.1f}x normal",features=F)
    d=None
    if cl>es[i] and ef[i]>es[i] and cl>o and cl>prev_hi:d=1
    elif cl<es[i] and ef[i]<es[i] and cl<o and cl<prev_lo:d=-1
    if not d:return dict(direction=None,why="no breakout on a closed candle in the trend's direction",features=F)
    if d==1 and funding>p["funding_max_h"]:return dict(direction=None,why=f"crowded longs: funding {funding:+.4%}/h",features=F)
    if d==-1 and funding<-p["funding_max_h"]:return dict(direction=None,why=f"crowded shorts: funding {funding:+.4%}/h",features=F)
    if p["require_narrative"]:
        if not narr or not narr.get("ok"):return dict(direction=None,why="no narrative: "+((narr or {}).get("why") or "no CT/news talk"),features=F)
        if d*(narr.get("sentiment") or 0)<-0.2:return dict(direction=None,why=f"narrative leans the other way ({narr['sentiment']:+.2f})",features=F)
    R=p["atr_mult"]*a[i];stop=cl-d*R
    sc=25*min(1,gap/0.02)+20*min(1,abs(cl-(prev_hi if d==1 else prev_lo))/a[i])+15*min(1,max(0,F["vol_ratio"]-1)/2)+(10 if (oi_change or 0)>0 else 0)+\
        (10 if d*funding<=0 else 5)+(20*min(1,math.log2(max(narr["ratio"],1))/4) if narr else 0)
    return dict(direction=d,entry_ref=cl,stop=stop,tp1=cl+d*R*p["tp1_r"],tp2=cl+d*R*p["tp2_r"],stop_dist=R/cl,score=round(sc,1),features=F,t=rows[i][0],
                why=f"{'long' if d==1 else 'short'}: closed {'above' if d==1 else 'below'} the 20-candle {'high' if d==1 else 'low'} in a {gap:.1%} trend")
def liq_dist(lev,max_lev):
    """Distance from entry to the liquidation price (isolated margin), as a share of price. Hyperliquid's maintenance
    margin is half the initial margin at the coin's max leverage."""
    return 1/lev-1/(2*max_lev)
def choose_leverage(stop_dist,target_dist,max_lev,b,p):
    """-> (leverage, note) or (None, reason)."""
    need=stop_dist*(1+p["liq_buffer"])
    if b.get("sizing")=="grok":
        lev=max(b["min_leverage"],min(b["max_leverage"],round(b["lev_k"]/max(target_dist,1e-9))))
        lev=min(lev,max_lev)
        while lev>=b["min_leverage"] and liq_dist(lev,max_lev)<need:lev-=1
        if lev<b["min_leverage"]:return None,(f"the coin allows only {max_lev}x" if max_lev<b["min_leverage"] else f"even {b['min_leverage']}x puts liquidation inside the stop")
        return lev,f"{lev}x (target {target_dist:.2%} away; liquidation {liq_dist(lev,max_lev):.1%} away vs stop {stop_dist:.2%})"
    return 1,"charter"
class HLData:
    """Hyperliquid public info API (read-only). Cached so the 30 s checks stay light."""
    def __init__(s,feeds,net="main"):s.F=feeds;s.net=net;s.c={}
    def _c(s,k,ttl,fn):
        v=s.c.get(k)
        if v and time.time()-v[0]<ttl:return v[1]
        x=fn()
        if x is not None:s.c[k]=(time.time(),x)
        return x if x is not None else (v[1] if v else None)
    def meta(s):
        def f():
            j=s.F.hl({"type":"meta"},s.net)
            return {u["name"]:dict(max_lev=int(u.get("maxLeverage") or 1),sz_dec=int(u.get("szDecimals") or 0),delisted=bool(u.get("isDelisted"))) for u in (j or {}).get("universe",[])} if j else None
        return s._c("meta",3600,f)
    def ctxs(s):
        def f():
            j=s.F.hl({"type":"metaAndAssetCtxs"},s.net)
            if not j or len(j)<2:return None
            return {u["name"]:dict(funding=float(c.get("funding") or 0),oi=float(c.get("openInterest") or 0),mark=float(c.get("markPx") or 0),vol=float(c.get("dayNtlVlm") or 0))
                    for u,c in zip(j[0].get("universe",[]),j[1])}
        return s._c("ctx",60,f)
    def mids(s):
        def f():
            j=s.F.hl({"type":"allMids"},s.net)
            return {k:float(v) for k,v in j.items()} if isinstance(j,dict) else None
        return s._c("mids",5,f)
    def candles(s,coin,interval="1h",n=300):
        sec={"1h":H,"4h":4*H,"15m":900,"1d":86400}[interval];now=int(time.time()*1000)
        def f():
            j=s.F.hl({"type":"candleSnapshot","req":{"coin":coin,"interval":interval,"startTime":now-(n+2)*sec*1000,"endTime":now}},s.net)
            if not isinstance(j,list):return None
            rows=sorted((int(x["t"])//1000,float(x["o"]),float(x["h"]),float(x["l"]),float(x["c"]),float(x["v"])) for x in j)
            return [r for r in rows if r[0]+sec<=time.time()]
        return s._c(f"c:{coin}:{interval}",60,f)
    def book(s,coin):
        j=s.F.hl({"type":"l2Book","coin":coin},s.net)
        if not j or not j.get("levels"):return None
        L=j["levels"];return dict(bids=[(float(x["px"]),float(x["sz"])) for x in L[0]],asks=[(float(x["px"]),float(x["sz"])) for x in L[1]])
def walk(book,side,notional):
    """Average fill price for a market order of `notional` USD (side +1 buys the asks, -1 sells the bids)."""
    lv=book["asks"] if side>0 else book["bids"];left=notional;cost=0.0;qty=0.0
    for px,sz in lv:
        take=min(left,px*sz);cost+=take;qty+=take/px;left-=take
        if left<=1e-9:break
    if left>1e-6 or qty<=0:return None
    return cost/qty
class PerpBook:
    """One paper perps book. Positions live in b_positions (mint = coin, tokens = size in coins, cost = margin)."""
    def __init__(s,db,cfg,notify,name):
        s.db,s.cfg,s.n,s.book=db,cfg,notify,name
        s.p=dict(DEF,**(cfg.get("perps") or {}));s.b=dict(BOOK_DEF.get(name,BOOK_DEF["perps"]),**((cfg.get("books") or {}).get(name) or {}))
        if s.k("cash") is None:s.ks("cash",s.b["start_equity_usd"]);s.ks("peak",s.b["start_equity_usd"]);s.ks("started",time.time())
        s.ks("limits",dict(start=s.b["start_equity_usd"],max_pos=s.b["max_open_positions"],daily=s.b["daily_loss_limit_pct"],max_dd=s.b["max_drawdown_pct"],
              enabled=s.b["enabled"],sizing=s.b["sizing"]))
    def k(s,n,d=None):return s.db.kv_get(f"{s.book}:{n}",d)
    def ks(s,n,v):s.db.kv_set(f"{s.book}:{n}",v)
    cash=property(lambda s:s.k("cash"))
    def label(s):return "PAPER · "+("perps (charter)" if s.b["sizing"]!="grok" else "perps-grok (research: 8-19x)")
    def positions(s):
        return [dict(id=i,coin=m,size=t,margin=c,opened=o,meta=json.loads(mt or "{}")) for i,m,t,c,o,mt in
                s.db.q("select id,mint,tokens,cost,opened,meta from b_positions where book=? and status='open'",(s.book,))]
    def upnl(s,pos,px):return pos["meta"]["side"]*(px-pos["meta"]["entry"])*pos["size"]-pos["meta"].get("funding",0)
    def equity(s,mids=None):
        v=0.0
        for p in s.positions():
            px=(mids or {}).get(p["coin"]);v+=p["margin"]+(s.upnl(p,px) if px else 0)
        return s.cash+v+(s.k("swept") or 0)
    def tradable(s,mids=None):return s.equity(mids)-(s.k("swept") or 0)
    def signal_log(s,coin,decision,why,size=None,feat=None,source="perps"):
        try:s.db.ins("b_signals",ts=time.time(),book=s.book,source=source,mint=coin,side="buy",decision=decision,reason=why,size_usd=size,features=json.dumps(feat or {},default=str))
        except Exception as e:log.warning("perps signal log: %s",e)
    def blocked(s):
        if s.db.kv_get("panic"):return "panic stop active (/panic)"
        if not s.b["enabled"]:return "book switched off in config"
        if s.k("paused") or s.db.kv_get("all:paused"):return "book paused (/book or /pause)"
        if s.k("shutdown"):return "drawdown switch tripped (/risk reset "+s.book+")"
        ds=s.k("day_start") or {}
        if ds.get("day")==risk.day() and s.k("last_equity",ds.get("eq",0))<ds.get("eq",0)*(1-s.b["daily_loss_limit_pct"]):return "daily loss limit hit"
        return risk.entry_block(s.db,s.cfg,s.book)
    # ---- entries -------------------------------------------------------------------------------------------
    def open(s,coin,sg,H_,meta_info,ret_series,mids):
        """Run every check, then simulate the market order on the real order book. Returns the position id or None."""
        why=s.blocked()
        if why:s.signal_log(coin,"refused",why,None,sg["features"]);return None
        P=s.positions()
        if any(p["coin"]==coin for p in P):return None
        if len(P)>=s.b["max_open_positions"]:s.signal_log(coin,"refused","max open positions",None,sg["features"]);return None
        why=risk.entry_block(s.db,s.cfg,s.book,coin=coin)
        if why:s.signal_log(coin,"refused",why,None,sg["features"]);return None
        same=[p for p in P if p["meta"]["side"]==sg["direction"]];linked=[p["coin"] for p in same if corr(ret_series.get(coin,[]),ret_series.get(p["coin"],[]))>s.p["corr_max"]]
        if len(linked)>=s.p["max_same_direction"]:
            s.signal_log(coin,"refused",f"correlation cap: moves with {', '.join(linked)} (same direction)",None,sg["features"]);return None
        mt=(H_.meta() or {}).get(coin)
        if not mt or mt["delisted"]:s.signal_log(coin,"refused","not listed on Hyperliquid",None,sg["features"]);return None
        eq=s.tradable(mids);d=sg["direction"];sd=sg["stop_dist"];td=abs(sg["tp1"]-sg["entry_ref"])/sg["entry_ref"]
        fee=s.p["taker_fee"];rt=2*fee+0.0004             # both fees + a slippage allowance
        ok,etxt=risk.fee_edge(td,rt,risk.charter(s.cfg,s.book)["fee_edge_mult"])
        if not ok:s.signal_log(coin,"refused","fee edge: "+etxt,None,sg["features"]);return None
        if s.b["sizing"]=="grok":
            lev,lnote=choose_leverage(sd,td,mt["max_lev"],s.b,s.p)
            if not lev:s.signal_log(coin,"refused","leverage: "+lnote,None,sg["features"]);return None
            share=s.b["margin_a_plus"] if sg["score"]>=s.p["a_plus"] else s.b["margin_a"];margin=eq*share;notional=margin*lev
        else:
            rf,rnote=risk.risk_fraction(s.db,s.cfg,s.book);notional=risk.size_for_risk(eq,sd,fee+0.0002,rf)
            margin=min(notional,eq*s.b["max_margin_pct"]);lev=max(1,math.ceil(notional/max(margin,1e-9)-1e-9))
            if lev>mt["max_lev"]:notional=margin*mt["max_lev"];lev=mt["max_lev"]
            if liq_dist(lev,mt["max_lev"])<sd*(1+s.p["liq_buffer"]):s.signal_log(coin,"refused",f"liquidation at {lev}x would sit inside the stop",None,sg["features"]);return None
            lnote=f"{lev}x, {rnote}"
        if notional<10:s.signal_log(coin,"refused",f"order ${notional:.2f} below Hyperliquid's $10 minimum",notional,sg["features"]);return None
        if margin>s.cash:s.signal_log(coin,"refused","not enough free margin",notional,sg["features"]);return None
        b1=H_.book(coin)
        if not b1:s.signal_log(coin,"refused","no order book (data gap)",notional,sg["features"]);return None
        time.sleep(max(0,s.p["exec_latency_s"]));b2=H_.book(coin) or b1
        px=walk(b2,d,notional)
        if not px:s.signal_log(coin,"refused","order book too thin for the size",notional,sg["features"]);return None
        mid=(b2["bids"][0][0]+b2["asks"][0][0])/2;slip=d*(px/mid-1)
        if d*(px-sg["stop"])<=0 or d*(sg["tp1"]-px)<=0:s.signal_log(coin,"refused","price already past the stop or the first target",notional,sg["features"]);return None
        size=notional/px;fees=notional*fee;R=abs(px-sg["stop"])
        ld=liq_dist(lev,mt["max_lev"]);liq=px*(1-d*ld)
        meta=dict(side=d,entry=px,lev=lev,notional=notional,liq=liq,stop=sg["stop"],stop0=sg["stop"],tp1=sg["tp1"],tp2=sg["tp2"],tp1_done=False,
                  funding=0.0,fees=fees,last_fund=time.time(),score=sg["score"],signal_t=sg["t"],slip=slip,risk_usd=notional*(R/px+2*fee),
                  narrative=(meta_info or {}).get("narrative"),narrative_name=(meta_info or {}).get("name"),why=sg["why"],lev_note=lnote)
        s.ks("cash",s.cash-margin-fees)
        pid=s.db.ins("b_positions",book=s.book,trader="perps:"+("long" if d>0 else "short"),mint=coin,tokens=size,cost=margin,opened=time.time(),status="open",
                     open_sig=f"perp:{s.book}:{coin}:{sg['t']}",meta=json.dumps(meta))
        s.db.ins("b_trades",ts=time.time(),book=s.book,trader="perps",mint=coin,side="buy",usd=margin+fees,tokens=size,price=px,pnl=0,sig=f"perp-open:{pid}",
                 note=f"{'LONG' if d>0 else 'SHORT'} {lev}x notional ${notional:.2f} slip {slip:+.3%}")
        s.signal_log(coin,"filled","passed every check",notional,dict(sg["features"],lev=lev,score=sg["score"]))
        s.n.send(term(f"perps --open {s.book}",[kv("mode",s.label()),SEP,kv("coin",coin),kv("side","LONG" if d>0 else "SHORT"),kv("score",f"{sg['score']:.0f}/100"),
            kv("entry",f"${px:.6g}"),kv("lev",f"{lev}x"),kv("notional",usd(notional)),kv("margin",usd(margin)),kv("liq",f"${liq:.6g}"),SEP,"orders installed (paper):",
            kv(" stop",f"${sg['stop']:.6g}"),kv(" TP1 50%",f"${sg['tp1']:.6g}"),kv(" TP2",f"${sg['tp2']:.6g}")]+wrap("targets are intended prices; fills can differ")+wrap(sg["why"],"why: ","  "),
            ("OK",("🟢 LONG" if d>0 else "🔴 SHORT")+f" {coin} entry confirmed")),tier="instant")
        return pid
    # ---- exits --------------------------------------------------------------------------------------------
    def close(s,pos,frac,reason,px,note):
        m=pos["meta"];sz=pos["size"]*frac;d=m["side"];fee=px*sz*s.p["taker_fee"];fund=m.get("funding",0)*frac
        gross=d*(px-m["entry"])*sz;margin=pos["margin"]*frac
        if reason=="liquidation":pnl=-margin;back=0.0
        else:pnl=gross-fee-fund;back=max(0.0,margin+pnl)
        pnl_total=pnl-(m.get("fees",0)*frac)                         # opening fee share counts too
        s.ks("cash",s.cash+back)
        if frac>=0.999:s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(time.time(),pnl_total,pos["id"]))
        else:
            m2=dict(m,funding=m.get("funding",0)-fund,fees=m.get("fees",0)*(1-frac));s.db.x("update b_positions set tokens=?,cost=?,realized=realized+?,meta=? where id=?",(pos["size"]-sz,pos["margin"]-margin,pnl_total,json.dumps(m2),pos["id"]))
        s.db.ins("b_trades",ts=time.time(),book=s.book,trader="perps",mint=pos["coin"],side="sell",usd=back,tokens=sz,price=px,pnl=pnl_total,sig=f"perp-close:{pos['id']}:{int(time.time())}",note=note)
        closed=frac>=0.999
        ledger.record(s.db,book=s.book,asset=pos["coin"]+"-PERP",dir=("long" if d>0 else "short")+f" {m['lev']}x",narrative=m.get("narrative_name"),score=m.get("score"),entry=m["entry"],exit=px,
            size=m["notional"]*frac,stop=m["stop0"],target=m["tp1"] if reason=="take_profit_1" else m["tp2"],trigger=reason,market=f"funding paid ${fund:.4f}",
            result=("partial " if not closed else "")+("win" if pnl_total>0 else "loss"),pnl=pnl_total,fees=fee+m.get("fees",0)*frac,notes=note,source=m.get("why"))
        if closed or reason in risk.STOP_REASONS:risk.on_exit(s.db,s.cfg,s.book,pos["coin"],reason,pnl_total,narrative=m.get("narrative"))
        if closed:ledger.maybe_review(s.db,s.book,s.n)
        s.n.send(term(f"perps --close {s.book}",[kv("mode",s.label()),SEP,kv("coin",pos["coin"]),kv("side",("LONG" if d>0 else "SHORT")+f" {m['lev']}x"),kv("why",note),
            kv("portion",f"{frac:.0%}"),kv("exit",f"${px:.6g}"),kv("entry",f"${m['entry']:.6g}"),kv("P&L",usd(pnl_total,True)+" net"),kv("cash",usd(s.cash))],
            ("OK" if pnl_total>=0 else "ERR",("🟢 " if pnl_total>=0 else "🔴 ")+{"liquidation":"LIQUIDATED","take_profit_1":"TP1 hit","take_profit":"TP2 hit"}.get(reason,"closed")+" "+pos["coin"])),
            tier="critical" if reason=="liquidation" else "instant")
        return pnl_total
    def check(s,mids,ctx,sig_dir=None):
        """Every check (30 s): funding, liquidation, stop, TP1 (+ breakeven), TP2, opposite signal, time limit."""
        now=time.time()
        for pos in s.positions():
            px=mids.get(pos["coin"]);m=pos["meta"]
            if not px:continue
            f=(ctx or {}).get(pos["coin"],{}).get("funding")
            if f is not None:
                hrs=(now-m.get("last_fund",now))/H;m["funding"]=m.get("funding",0)+m["side"]*m["notional"]*f*hrs;m["last_fund"]=now
                s.db.x("update b_positions set meta=? where id=?",(json.dumps(m),pos["id"]))
            d=m["side"]
            if d*(px-m["liq"])<=0:s.close(pos,1.0,"liquidation",m["liq"],f"liquidated at ${m['liq']:.6g} (margin lost)");continue
            if d*(px-m["stop"])<=0:
                r="breakeven_stop" if m["tp1_done"] else "stop_loss";fill=px if d*(px-m["stop"])<-0.002*px else m["stop"]
                s.close(pos,1.0,r,fill,f"{r.replace('_',' ')} ${m['stop']:.6g} (filled ${fill:.6g})");continue
            if not m["tp1_done"] and d*(px-m["tp1"])>=0:
                m["tp1_done"]=True;m["stop"]=m["entry"]*(1+d*2*s.p["taker_fee"]);s.db.x("update b_positions set meta=? where id=?",(json.dumps(m),pos["id"]))
                pos["meta"]=m;s.close(pos,s.p["tp1_portion"],"take_profit_1",px,f"TP1 ${m['tp1']:.6g}: closed {s.p['tp1_portion']:.0%}, stop to breakeven");continue
            if d*(px-m["tp2"])>=0:s.close(pos,1.0,"take_profit",px,f"TP2 ${m['tp2']:.6g} reached");continue
            if sig_dir and sig_dir.get(pos["coin"])==-d:s.close(pos,1.0,"signal_reversed",px,"the opposite signal closed");continue
            if now-pos["opened"]>=s.p["max_hold_h"]*H:s.close(pos,1.0,"max_hold",px,f"held {s.p['max_hold_h']}h max")
    def mark(s,mids):
        eq=s.equity(mids);s.ks("last_equity",eq);s.db.ins("b_equity",ts=time.time(),book=s.book,equity=eq,cash=s.cash)
        pk=max(s.k("peak") or eq,eq);s.ks("peak",pk);ds=s.k("day_start") or {}
        if ds.get("day")!=risk.day():s.ks("day_start",{"day":risk.day(),"eq":eq})
        if eq<pk*(1-s.b["max_drawdown_pct"]) and not s.k("shutdown"):
            s.ks("shutdown",True);s.n.send(term("safeguard --drawdown",[kv("book",s.book),kv("drawdown",f"{1-eq/pk:.0%}"),SEP,"new perps entries STOPPED","reset: /risk reset "+s.book],("ERR","🔴 SAFEGUARD tripped")),tier="critical")
        return eq
class PerpsRunner:
    """Owns both perps books (one thread): data every check, signals once per closed hour, exits every check."""
    def __init__(s,db,cfg,notify,engine=None,feeds=None):
        s.db,s.cfg,s.n=db,cfg,notify;s.p=dict(DEF,**(cfg.get("perps") or {}));s.F=feeds or FD.Feeds(db,cfg);s.H=HLData(s.F,s.p["network"])
        s.books=[PerpBook(db,cfg,notify,b) for b in("perps","perps-grok") if dict(BOOK_DEF[b],**((cfg.get("books") or {}).get(b) or {})).get("enabled",True)]
        s.E=engine;s.last_hour=db.kv_get("perps:last_hour",0);s.last_mark=0;s.dirs={}
    def narr(s,coin):
        if not s.E:return None
        try:return s.E.major(coin)
        except Exception as e:log.warning("perps narrative %s: %s",coin,e);return None
    def hourly(s,now):
        """Signals on the candle that just closed, ranked, then each book takes what its rules allow."""
        ctx=s.H.ctxs() or {};mids=s.H.mids() or {};C={};S=[];R={}
        oi=s.db.kv_get("perps:oi_hist") or {}
        for coin in s.p["coins"]:
            rows=s.H.candles(coin,s.p["interval"],800)
            if not rows:s.F.gap("hyperliquid",f"no candles for {coin}");continue
            C[coin]=rows;R[coin]=rets(rows[-169:])
            o=ctx.get(coin,{}).get("oi");h=[x for x in oi.get(coin,[]) if now-x[0]<6*H]+([[now,o]] if o else []);oi[coin]=h
            ch=(h[-1][1]/h[0][1]-1) if len(h)>=2 and h[0][1] else None
            sg=signal(rows,s.p,ctx.get(coin,{}).get("funding",0.0),ch,s.narr(coin));sg["coin"]=coin;S.append(sg)
            s.dirs[coin]=sg.get("direction")
            if not sg["direction"]:
                for b in s.books:
                    if sg.get("features") and sg["why"].startswith(("no narrative","crowded","narrative leans","abnormal")):b.signal_log(coin,"refused",sg["why"],None,sg["features"])
        s.db.kv_set("perps:oi_hist",oi);ALL=S[:]
        S=sorted([x for x in S if x["direction"]],key=lambda x:-x["score"])
        s.db.kv_set("perps:signals",dict(ts=now,signals=[{k:v for k,v in x.items() if k!="features"} for x in S],
            all=[dict(coin=x["coin"],direction=x.get("direction"),why=x["why"],score=x.get("score")) for x in sorted(ALL,key=lambda x:x["coin"])]))
        for sg in S:
            for b in s.books:
                try:b.open(sg["coin"],sg,s.H,(s.narr(sg["coin"]) or {}) and {"narrative":"major:"+sg["coin"],"name":sg["coin"]+" news/CT"},R,mids)
                except Exception:log.exception("perps open %s %s",b.book,sg["coin"])
        return S
    def tick(s,now=None):
        now=now or time.time();mids=s.H.mids()
        if not mids:return
        ctx=s.H.ctxs()
        for b in s.books:
            try:b.check(mids,ctx,s.dirs)
            except Exception:log.exception("perps check %s",b.book)
        hr=int(now//H)
        if hr!=s.last_hour and now%H>=30:
            s.last_hour=hr;s.db.kv_set("perps:last_hour",hr)
            try:s.hourly(now)
            except Exception:log.exception("perps hourly")
        if now-s.last_mark>=300:
            s.last_mark=now
            for b in s.books:
                try:b.mark(mids)
                except Exception:log.exception("perps mark %s",b.book)
            risk.sweep(s.db,s.cfg,{b.book:_SweepAdapter(b,mids) for b in s.books},s.n,now=now,tag="perps")
class _SweepAdapter:
    """Lets risk.sweep treat a perps book like a paper book (baseline, cash, swept pot)."""
    def __init__(s,b,mids):s.b=b;s.mids=mids;s.p={"start_equity_usd":b.b["start_equity_usd"]}
    cash=property(lambda s:s.b.cash)
    def kget(s,n,d=None):return s.b.k(n,d)
    def kset(s,n,v):s.b.ks(n,v)
    def equity(s):return s.b.equity(s.mids)
def runner_thread(db_path,cfg,notify):
    p=dict(DEF,**(cfg.get("perps") or {}))
    if not p.get("enabled",True):log.info("perps off (perps.enabled)");return None
    def job():
        from db import DB
        import narrative as NR
        db=DB(db_path);E=NR.Engine(db,cfg) if p["require_narrative"] else None;r=PerpsRunner(db,cfg,notify,E)
        time.sleep(15)
        while True:
            try:r.tick();db.kv_set("perps:alive",time.time())
            except Exception as e:
                log.exception("perps tick (continuing)")
                try:db.kv_set("perps:error",{"ts":time.time(),"msg":f"{type(e).__name__}: {str(e)[:160]}"})
                except Exception:pass
            time.sleep(p["check_seconds"])
    t=threading.Thread(target=job,daemon=True,name="perps");t.start();return t
def text(db):
    """/perps: both books, open positions, the last hour's signals."""
    rows=[]
    for b in("perps","perps-grok"):
        eq=db.kv_get(b+":last_equity",db.kv_get(b+":cash"));lim=db.kv_get(b+":limits") or {}
        if eq is None:continue
        rows+=[kv(b,usd(eq)+(" (start "+usd(lim.get("start",0))+")" if lim else ""))]
        for i,m,t,c,mt in db.q("select id,mint,tokens,cost,meta from b_positions where book=? and status='open'",(b,)):
            x=json.loads(mt or "{}");rows+=wrap(f"{'LONG' if x.get('side')==1 else 'SHORT'} {m} {x.get('lev')}x @ ${x.get('entry',0):.5g} stop ${x.get('stop',0):.5g} TP ${x.get('tp2',0):.5g}","  ","   ")
    sg=db.kv_get("perps:signals") or {}
    rows+=[SEP,"last hour:"+(" "+time.strftime("%H:%M UTC",time.gmtime(sg["ts"])) if sg.get("ts") else " not run yet")]
    for x in sg.get("all") or []:rows+=wrap(f"{x['coin']}: {x['why']}","  ","    ")
    rows+=[SEP]+wrap("paper only. perps-grok is the 8-19x screenshot spec as research.")
    return term("perps --status",rows or ["perps not started yet"],("OK","📉📈 perps (paper)"))
