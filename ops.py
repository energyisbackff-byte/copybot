"""Daily operations, run from the main loop at every exit check (about every 30 s). Each job remembers when it last ran,
so a restart never repeats it and a missed time is caught up once:
  - hourly digest of routine messages (refusals, sweeps): fills, errors and approvals are always instant
  - global kill switch: all paper books together global_kill below their combined peak -> no new entries anywhere
    (the research book perps-grok is left out: it runs the 8-19x spec on purpose and has its own limits)
  - profit sweep for the books owned by the main loop (copy, consensus); the narrative and perps threads sweep theirs
  - the morning report (ops.morning_hour_utc, default 06:00 UTC = 07:00 in Lagos), also on demand with /reportdaily
  - stalled-thread alarm: the narrative engine, the Narrative Rider book and the perps books each write a heartbeat
  - the source scorecard once a day (scorecard.py): which trader source's wallets make money when copied
  - every Monday: the forward check of the trader picks (in its own thread: it reads Helius)
  - on the 1st of every month: a reminder to test /panic, and the walk-forward re-check of Trend Rider and the perps rules
Nothing here can block a sell."""
import os,time,json,logging
import risk,ledger
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot")
ALL=("copy","consensus","trend","trend-basket","narrative","perps","perps-grok")
KILL_BOOKS=("copy","consensus","trend","trend-basket","narrative","perps")
DEF=dict(morning_hour_utc=6,digest_minutes=60,stall_minutes={"narrative":20,"narrbook":5,"perps":5,"spot-live":5},monthly_day=1)
NAMES={"copy":"copy","consensus":"consensus","trend":"Trend Rider","trend-basket":"Trend basket","narrative":"Narrative Rider","perps":"perps","perps-grok":"perps-grok (8-19x)"}
def conf(cfg):return dict(DEF,**((cfg or {}).get("ops") or {}))
def enabled(cfg,b):return ((cfg.get("books") or {}).get(b) or {}).get("enabled",b=="copy")
def books(db,cfg):return [b for b in ALL if enabled(cfg,b) or db.kv_get(b+":cash") is not None]
def equity(db,b):return db.kv_get(b+":last_equity",db.kv_get(b+":cash"))
def equity_at(db,b,ts):
    r=db.q("select equity from b_equity where book=? and ts<=? order by ts desc limit 1",(b,ts))
    return r[0][0] if r else None
def book_state(db,b):
    if db.kv_get("panic"):return "PANIC"
    if db.kv_get(b+":shutdown"):return "SHUTDOWN"
    if db.kv_get(b+":paused") or db.kv_get("all:paused"):return "PAUSED"
    if risk.stopouts_today(db,b)>=2:return "stand-down"
    return "ok"
BAR=dict(trades=30,pf=1.3,fee_mult=3)   # Master Build Prompt: copy/consensus need this before ANY real-money talk
def bar(db,book,c=BAR):
    """The paper bar for copy/consensus: 30+ closed trades and profit factor >= 1.3 with fees counted 3 times
    (the ledger's P&L already paid the fees once, so 2 more are taken off each trade)."""
    try:r=db.q("select pnl,fees from ledger where book=?",(book,))
    except Exception:r=[]
    adj=[(p or 0)-(c["fee_mult"]-1)*(f or 0) for p,f in r];gw=sum(x for x in adj if x>0);gl=-sum(x for x in adj if x<=0)
    pf=gw/gl if gl else (99.0 if gw else None)
    return dict(n=len(r),pf=pf,ok=len(r)>=c["trades"] and (pf or 0)>=c["pf"])
def bar_text(b):
    pf="–" if b["pf"] is None else ("∞" if b["pf"]>=99 else f"{b['pf']:.2f}")
    return f"{b['n']}/{BAR['trades']} trades, PF {pf} at 3x fees"+(" ✓" if b["ok"] else "")
# ---- the daily report ------------------------------------------------------------------------------------------
def daily_rows(db,cfg,now=None,hours=24):
    now=now or time.time();since=now-hours*3600;rows=[];T=dict(n=0,w=0,l=0,net=0.0,fees=0.0)
    for b in books(db,cfg):
        eq=equity(db,b);e0=equity_at(db,b,since) or db.kv_get(b+":cash")
        if eq is None:continue
        cl=db.q("select realized from b_positions where book=? and status='closed' and closed>=?",(b,since))
        w=sum(1 for (r,) in cl if (r or 0)>0);l=len(cl)-w;net=sum((r or 0) for (r,) in cl)
        try:fees=db.q("select coalesce(sum(fees),0) from ledger where book=? and ts>=?",(b,since))[0][0]
        except Exception:fees=0.0
        op=db.q("select coalesce(sum(cost),0),count(*) from b_positions where book=? and status='open'",(b,))[0]
        ch=(eq/e0-1) if e0 else 0
        rows+=[f"{NAMES.get(b,b)}",kv("  money",f"{usd(eq)} ({ch:+.1%} 24h)",9)]
        if cl:rows.append(kv("  closed",f"{len(cl)}: {w} green, {l} red, net {usd(net,True)}",9))
        if op[1]:rows.append(kv("  open",f"{op[1]} position(s), {usd(op[0])} in",9))
        st=book_state(db,b)
        if st!="ok":rows.append(kv("  state",st,9))
        if b in("copy","consensus"):rows.append(kv("  bar",bar_text(bar(db,b)),9))
        T["n"]+=len(cl);T["w"]+=w;T["l"]+=l;T["net"]+=net;T["fees"]+=fees or 0
    gd=risk.green_days(db,since=now-30*86400)
    cnt=(db.kv_get("notify:count") or {})
    yday=time.strftime("%Y-%m-%d",time.gmtime(now-86400));tday=time.strftime("%Y-%m-%d",time.gmtime(now))
    rows+=[SEP,kv("trades",f"{T['n']} closed: {T['w']} green, {T['l']} red",9),kv("win rate",f"{T['w']/T['n']:.0%}" if T["n"] else "–",9),
           kv("net",usd(T["net"],True)+f" (fees {usd(T['fees'])})",9),
           kv("green d",f"{gd['pct']:.0%} of {gd['days']} trading days (30d)" if gd["pct"] is not None else "no closed trades yet",9),
           kv("alerts",f"{cnt.get(tday,0)} today, {cnt.get(yday,0)} yesterday",9)]
    L=db.kv_get("narr:last") or {}
    if L.get("top"):
        rows+=[SEP,"narratives now:"]+sum((wrap(f"{r['score']:.0f} {r['grade']} {r['name']} ({r['state']}) -> {r['ticker'] or '–'}","  ","    ") for r in L["top"][:3]),[])
    if db.kv_get("risk:global_kill"):rows+=[SEP,"🔴 global kill switch is ON","(/risk clear after a review)"]
    rows+=[SEP]+wrap("zero trades is a valid day: the bot only trades A+ setups. Nothing here is a promise.")
    return rows
def daily_text(db,cfg,now=None):
    return term("report --daily",daily_rows(db,cfg,now),("OK","☀️ daily report (last 24h)"))
# ---- the tick ------------------------------------------------------------------------------------------------
def once(db,key,stamp):
    """True the first time `stamp` is seen for `key` (so a job runs once per day / month, even across restarts)."""
    if db.kv_get("ops:"+key)==stamp:return False
    db.kv_set("ops:"+key,stamp);return True
def tick(db,cfg,notify,papers,now=None,backtests=None,weekly=None):
    now=now or time.time();c=conf(cfg)
    try:notify.flush_digest(c["digest_minutes"]*60)
    except Exception:log.exception("digest")
    try:risk.global_check(db,cfg,[b for b in KILL_BOOKS if b in books(db,cfg)],notify,now)
    except Exception:log.exception("global kill check")
    try:risk.sweep(db,cfg,papers,notify,now=now,tag="main")
    except Exception:log.exception("sweep")
    g=time.gmtime(now)
    if g.tm_hour>=c["morning_hour_utc"] and once(db,"morning",time.strftime("%Y-%m-%d",g)):
        try:notify.send(daily_text(db,cfg,now),tier="instant")
        except Exception:log.exception("morning report")
    stalls(db,cfg,notify,now,c)
    if g.tm_hour>=c["morning_hour_utc"] and once(db,"prune",time.strftime("%Y-%m-%d",g)):prune(db,now,c)   # keep the database small
    if g.tm_hour>=c["morning_hour_utc"] and once(db,"scorecard",time.strftime("%Y-%m-%d",g)):   # which trader source makes money
        try:
            import scorecard;scorecard.update(db,cfg,notify,now)
        except Exception:log.exception("source scorecard")
    if g.tm_wday==0 and g.tm_hour>=c["morning_hour_utc"] and once(db,"fwdcheck",time.strftime("%G-W%V",g)):   # Mondays
        try:
            if weekly:weekly()
        except Exception:log.exception("weekly forward check")
    if g.tm_mday>=c["monthly_day"] and once(db,"monthly",time.strftime("%Y-%m",g)):
        try:
            notify.send(term("monthly --checks",wrap("1) test the kill switch: send /panic, check that every book says PANIC (/books), then /panic off. (if real money is on, /panic also sells it)")+
                wrap("2) the monthly walk-forward re-check of Trend Rider and the perps rules starts now; the reports arrive in a few minutes."),("OK","🗓 monthly checks")),tier="instant")
            if backtests:backtests()
        except Exception:log.exception("monthly")
def prune(db,now,c):
    """Once a day: drop old raw rows the bot no longer needs (trader history pulled for selection, the signature dedupe
    list, gate decisions). Trades, positions, fills, equity and the ledger are never pruned."""
    keep=c.get("keep_days") or {}
    for sql,days in(("delete from events where t<?",keep.get("events",60)),("delete from seen where ts<?",keep.get("seen",30)),
                    ("delete from b_signals where ts<?",keep.get("signals",120)),("delete from spot_intents where status not in('new','retry','approval') and ts<?",keep.get("intents",120))):
        try:db.x(sql,(now-days*86400,))
        except Exception as e:log.warning("prune: %s",e)
def stalls(db,cfg,notify,now,c):
    """A thread that stopped writing its heartbeat -> one critical alert (and one 'recovered' when it is back)."""
    for name,key,on in(("narrative engine","narr:alive",(cfg.get("narrative") or {}).get("enabled",True)),
                       ("Narrative Rider book","narrbook:alive",enabled(cfg,"narrative")),
                       ("perps books","perps:alive",(cfg.get("perps") or {}).get("enabled",True)),
                       ("real-money worker (coin wallet)","spot-live:alive",True)):
        if not on:continue
        t=db.kv_get(key);lim=c["stall_minutes"].get(key.split(":")[0],10)*60;flag="ops:stall:"+key
        if t is None:continue                    # not started yet
        if now-t>lim and not db.kv_get(flag):
            db.kv_set(flag,now);notify.send(term("watchdog --thread",[kv("part",name),kv("silent",f"{int((now-t)/60)} min"),SEP]+wrap("it restarts with the bot; if this repeats: /update or /health"),("ERR","🔴 a bot part stopped")),tier="critical")
        elif now-t<=lim and db.kv_get(flag):
            db.kv_set(flag,None);notify.send(term("watchdog --thread",[kv("part",name),"running again"],("OK","🟢 recovered")),tier="instant")
