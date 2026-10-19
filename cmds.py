"""Telegram commands added with the narrative engine, the perps books and the risk charter. handle() returns the reply,
or None when the command is not one of these (main.py then answers it). Read-only except /pause, /resume and /risk
(which need a 6-digit code for anything that turns trading back on). Never shows keys, codes from other commands or
chat ids."""
import time,json,secrets,logging
import risk,ledger,ops
from tgfmt import term,kv,usd,SEP,wrap,short
log=logging.getLogger("bot")
WSOL="So11111111111111111111111111111111111111112"
def coin(m):return "SOL" if m==WSOL else short(m)
def _code():return f"{secrets.randbelow(10**6):06d}"
def age(sec):
    sec=max(0,int(sec or 0));return f"{sec//60}m" if sec<7200 else f"{sec//3600}h{sec%3600//60:02d}m" if sec<172800 else f"{sec//86400}d"
def positions_text(db,cfg,prices=None,mids=None):
    rows=[];spot=[];perp=[]
    for i,b,tr,m,tok,cost,op,meta in db.q("select id,book,trader,mint,tokens,cost,opened,meta from b_positions where status='open' order by opened"):
        x=json.loads(meta or "{}");(perp if b.startswith("perps") else spot).append((i,b,tr,m,tok,cost,op,x))
    if spot and prices is None:
        try:
            from chain import jup_prices;prices=jup_prices([p[3] for p in spot])
        except Exception:prices={}
    if perp and mids is None:
        try:
            import feeds;mids={k:float(v) for k,v in feeds.http(feeds.HL["main"]+"/info",body={"type":"allMids"},timeout=10).items()}
        except Exception:mids={}
    now=time.time()
    for i,b,tr,m,tok,cost,op,x in spot:
        px=(prices or {}).get(m);val=tok*px if px else None;ch=(val/cost-1) if val and cost else None;pl=x.get("x") or {}
        rows+=[f"{b} · {coin(m)} · {age(now-(op or now))}",f"  in {usd(cost)} -> "+(f"now {usd(val)} {ch:+.1%}" if val else "no price")]
        if pl:rows+=[f"  stop ${pl['stop_px']:.6g}"+(" (breakeven+)" if pl.get("tp1_done") else ""),
                     f"  final target ${pl['tp2_px']:.6g}" if pl.get("tp1_done") else f"  50% at ${pl['tp1_px']:.6g}, rest ${pl['tp2_px']:.6g}"]
        elif x.get("stop"):rows.append(f"  stop ${x['stop']:.4f} (trailing)")
    for i,b,tr,m,tok,cost,op,x in perp:
        px=(mids or {}).get(m);d=x.get("side",1)
        pnl=(d*(px-x["entry"])*tok-x.get("funding",0)) if px else None
        rows+=[f"{b} · {'LONG' if d>0 else 'SHORT'} {m} {x.get('lev')}x · {age(now-(op or now))}",f"  entry ${x['entry']:.6g} -> "+(f"now ${px:.6g}" if px else "no price")]
        if pnl is not None:rows.append(f"  P&L {usd(pnl,True)} (fees not yet)")
        rows+=[f"  stop ${x['stop']:.6g} · liq ${x['liq']:.6g}",f"  final target ${x['tp2']:.6g}" if x.get("tp1_done") else f"  50% at ${x['tp1']:.6g}, rest ${x['tp2']:.6g}"]
    if not rows:rows=["no open positions"]
    rows+=[SEP]+wrap("stops and targets are intended prices; a real fill can differ.")
    return term("positions --all",rows,("OK",f"📂 {len(spot)+len(perp)} open position(s)"))
def balance_text(db,cfg):
    rows=[]
    for b in ops.books(db,cfg):
        eq=ops.equity(db,b);cash=db.kv_get(b+":cash")
        if eq is None:continue
        sw=db.kv_get(b+":swept") or 0;ds=db.kv_get(b+":day_start") or {};lim=db.kv_get(b+":limits") or {}
        day=(eq-ds["eq"]) if ds.get("day")==risk.day() and ds.get("eq") else None
        op=db.q("select count(*) from b_positions where book=? and status='open'",(b,))[0][0]
        rows+=[f"{ops.NAMES.get(b,b)} (paper)",kv("  value",usd(eq)+(f"  start {usd(lim['start'])}" if lim.get("start") else ""),7),
               kv("  cash",usd(cash or 0)+(f"  saved {usd(sw)}" if sw else ""),7),kv("  today",(usd(day,True) if day is not None else "–")+f"  open {op}  {ops.book_state(db,b)}",7)]
    tot=sum(ops.equity(db,b) or 0 for b in ops.books(db,cfg))
    rows+=[SEP,kv("all paper",usd(tot),9),kv("real $","/wallet (Trend Rider)",9),SEP]+wrap("'saved' = profit set aside by the daily sweep; it is never traded again.")
    return term("balance --all",rows,("OK","💰 balances"))
def risk_text(db,cfg):
    c=risk.charter(cfg);rows=[kv("per trade",f"{c['per_trade']:.2%} of the book (copy/consensus {risk.charter(cfg,'copy')['per_trade']:.0%})",9),
        kv("Kelly",f"0.25x after {c['kelly_min_trades']} trades (only lowers)",9),kv("fee edge",f"target >= {c['fee_edge_mult']:g}x costs",9),
        kv("day stop",f"{c['stopouts_per_day']} stop-outs -> stand down",9),kv("cooldown",f"{c['coin_cooldown_h']}h per stopped coin",9),
        kv("kill",f"all books -{c['global_kill']:.0%} from peak",9),SEP]
    g=db.kv_get("risk:global_kill")
    rows.append(kv("kill sw",("🔴 ON since "+time.strftime("%d %b %H:%M",time.gmtime(g["ts"]))) if g else "🟢 off",9))
    for b in ops.books(db,cfg):
        so=risk.stopouts_today(db,b);cd={k:v for k,v in (db.kv_get(b+":cooldown") or {}).items() if v>time.time()}
        if so or cd or db.kv_get(b+":shutdown"):rows+=wrap(f"{b}: {so} stop-out(s) today, {len(cd)} coin(s) cooling down"+(", SHUT (drawdown)" if db.kv_get(b+":shutdown") else ""),"  ","    ")
    nf={k:v for k,v in (db.kv_get("narr:fails") or {}).items() if v.get("blocked")}
    if nf:rows+=wrap("blocked narratives: "+", ".join(nf),"  ","    ")
    rows+=[SEP,"/risk clear   kill switch off","/risk reset BOOK  restart a","   book after a drawdown stop"]
    return term("risk --charter",rows,("OK","🛡 risk"))
def gaps_text(db):
    try:G=db.q("select ts,source,what from data_gaps where ts>? order by ts desc limit 12",(time.time()-86400,))
    except Exception:G=[]
    n=0
    try:n=db.q("select count(*) from data_gaps where ts>?",(time.time()-86400,))[0][0]
    except Exception:pass
    rows=[]
    for t,s,w in G:rows+=wrap(f"{time.strftime('%H:%M',time.gmtime(t))} {s}: {w}","","  ")
    return term("gaps --24h",(rows or ["no data gaps in 24h"])+[SEP]+wrap("a gap = a source failed or sent nothing. it is logged, never filled in."),("OK",f"🕳 {n} data gap(s) in 24h"))
class _Quiet:
    token=""
    def send(s,*x,**k):pass
def handle(cmd,db,text,cfg,notify=None):
    a=text.split()[1:]
    if cmd=="/positions":return positions_text(db,cfg)
    if cmd=="/balance":return balance_text(db,cfg)
    if cmd=="/lasttrade":return ledger.last_text(db)
    if cmd=="/ledger":return ledger.ledger_text(db)
    if cmd=="/narratives":
        import narrative;return narrative.text(db)
    if cmd=="/perps":
        import perps
        if not a:return perps.text(db)
        import perps_live as PL
        L=PL.PerpsLive(db,cfg,notify or _Quiet())
        try:
            if a[0]=="agent":
                st=L.status(True)
                if st.get("down"):return term("perps agent",wrap(st.get("error","guard not running")),("WARN","guard not running"))
                return term("perps agent",["approve this agent address","in the Hyperliquid app:","API -> add API wallet ->","paste it, name it Zetrax",SEP,"it can trade, it can NOT","withdraw (Hyperliquid rule)"],
                    ("OK","🔑 agent "+st.get("network","")),f"<code>{st.get('agent','')}</code>")
            if a[0]=="setmaster":
                if len(a)==3 and a[1]=="confirm":
                    w=db.kv_get("perps-live:master_pending") or {}
                    if a[2]!=w.get("code") or time.time()>w.get("exp",0):return term("perps setmaster",["code wrong or expired"],("ERR","not set"))
                    db.kv_set("perps-live:master_pending",None);r=PL.guard("set_master",address=w["addr"])
                    return term("perps setmaster",[kv("master",r["master"][:10]+"…"),SEP,"next: approve the agent","(/perps agent), then","/perps live selftest"],("OK","🏠 master set"))
                if len(a)!=2 or not (len(a[1])==42 and a[1].startswith("0x")):return term("perps setmaster",["usage: /perps setmaster 0x...","your own wallet address on","Hyperliquid (can be set once)"],("WARN","bad syntax"))
                c=_code();db.kv_set("perps-live:master_pending",dict(addr=a[1],code=c,exp=time.time()+300))
                g=[a[1][i:i+6] for i in range(0,42,6)]
                return term("perps setmaster --check",["check EVERY character",SEP," ".join(g[:4])," ".join(g[4:]),SEP,f"/perps setmaster confirm {c}","(valid 5 minutes)"],("WARN","⚠ check the address"))
            if a[0]=="live":
                sub=a[1] if len(a)>1 else ""
                if not sub:return L.text()
                if sub in("trial","arm"):
                    sizing="grok" if "grok" in a[2:] else "charter";code=next((x for x in a[2:] if x.isdigit()),None)
                    return L.arm(code,mode=sub,sizing=sizing)
                if sub=="off":L.ks("armed",None);L.ks("approval",None);return term("perps live off",["real-money perps OFF","open positions keep their","stop + targets on the","exchange"],("OK","🔒 off"))
                if sub=="selftest":
                    r=PL.guard("selftest",timeout=90);return term("perps live selftest",sum((wrap("- "+x) for x in r.get("steps",[])),[]),("OK","✅ selftest passed") if r.get("ok") else ("WARN","selftest not passed"))
                if sub=="clear":L.clear();return term("perps live clear",["mismatch cleared;","switch on again yourself"],("OK","cleared"))
            return term("perps",["/perps  paper books + signals","/perps live  real money","/perps agent | setmaster"],("WARN","unknown"))
        except PL.GuardError as e:return term("perps "+" ".join(a[:2]),wrap(str(e)),("ERR","perps guard"))
    if cmd=="/reportdaily":return ops.daily_text(db,cfg)
    if cmd=="/sources":
        import scorecard
        if a[:1]==["reset"] and len(a)==2:
            return term("sources reset",[f"{a[1]}: on again, full cap"],("OK","🧾 source back on")) if scorecard.reset(db,a[1]) else term("sources reset",["unknown source: "+a[1][:20]],("WARN","not found"))
        return scorecard.text(db,cfg)
    if cmd=="/gaps":return gaps_text(db)
    if cmd=="/pause":
        db.kv_set("all:paused",True)
        return term("pause --all",["every book: no new entries","exits, stops and targets","keep running",SEP,"undo: /resume"],("WARN","⏸ all books paused"))
    if cmd=="/resume":
        db.kv_set("all:paused",None)
        rows=["new entries allowed again"]+(["(PANIC is still on: /panic off)"] if db.kv_get("panic") else [])+(["(kill switch is on: /risk clear)"] if db.kv_get("risk:global_kill") else [])
        return term("resume --all",rows,("OK","▶️ books resumed"))
    if cmd=="/risk":
        if not a:return risk_text(db,cfg)
        if a[0]=="clear":
            if len(a)==1:
                c=_code();db.kv_set("risk:clear_code",dict(code=c,exp=time.time()+120))
                return term("risk clear",["turns the global kill","switch off and restarts its","peak from today's value.",SEP,f"confirm: /risk clear {c}"],("WARN","confirm with code"))
            w=db.kv_get("risk:clear_code") or {}
            if a[1]!=w.get("code") or time.time()>w.get("exp",0):return term("risk clear",["code wrong or expired"],("ERR","not cleared"))
            db.kv_set("risk:clear_code",None);risk.global_clear(db,list(ops.KILL_BOOKS))
            return term("risk clear",["kill switch OFF","new entries allowed"],("OK","🟢 cleared"))
        if a[0]=="reset" and len(a)>=2:
            b=a[1]
            if b not in ops.ALL:return term("risk reset",["books: "+", ".join(ops.ALL)],("WARN","unknown book"))
            if len(a)==2:
                c=_code();db.kv_set("risk:reset_code",dict(code=c,book=b,exp=time.time()+120))
                return term("risk reset "+b,["restarts the book after its","drawdown switch: the peak","becomes today's value.",SEP,f"confirm: /risk reset {b} {c}"],("WARN","confirm with code"))
            w=db.kv_get("risk:reset_code") or {}
            if a[2]!=w.get("code") or w.get("book")!=b or time.time()>w.get("exp",0):return term("risk reset",["code wrong or expired"],("ERR","not reset"))
            db.kv_set("risk:reset_code",None);db.kv_set(b+":shutdown",None);db.kv_set(b+":peak",ops.equity(db,b))
            return term("risk reset "+b,["drawdown switch cleared"],("OK","🟢 "+b+" restarted"))
        return term("risk",["usage: /risk | /risk clear","| /risk reset BOOK"],("WARN","bad syntax"))
    if cmd=="/backtest" and a[:1]==["perps"]:
        import backtest_perps as BP,os
        if a[-1:]==["last"]:
            r=db.kv_get(BP.KEY)
            if not r:return term("backtest perps",["no result yet:","send /backtest perps"],("WARN","not run yet"))
            if notify and r.get("charter"):notify.send(BP.report(r["charter"]))
            return BP.report(r.get("grok") or r.get("charter"))
        if not BP.run_async(os.environ.get("COPYBOT_DB","bot.db"),notify,cfg):return term("backtest perps",["already running"],("WARN","busy"))
        return term("backtest --perps",["downloading years of hourly","prices of XRP, DOGE, LINK,","UNI, ATOM and SOL, then","testing long + short rules","(safe charter and 8-19x).","First run: 10-20 minutes.",SEP,"/backtest perps last"],("OK","⏳ perps backtest started"))
    return None
