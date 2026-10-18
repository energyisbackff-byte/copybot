"""Risk charter shared by every book (written down in Trading_Context.md). Small functions over the database, so the
copy/consensus books, the Narrative Rider and the perps book all apply exactly the same rules:
  per-trade risk   per_trade of the book's tradable balance (fees included). Once a book has kelly_min_trades closed
                   trades, 0.25x Kelly (from its own results) becomes a cap that can only LOWER the risk, never raise it.
  fee-edge gate    the planned move to the first target must be at least fee_edge_mult x the round-trip costs.
  cooldowns        a coin stopped out with a loss is blocked coin_cooldown_h in that book; a narrative with
                   narrative_fail_block losing trades is blocked until the engine sees it accelerate again.
  stand-down       stopouts_per_day losing stop-outs in one UTC day -> that book takes no new entries until 00:00 UTC.
  global kill      all paper books together global_kill below their combined peak -> no new entries anywhere (/risk clear).
  profit sweep     once a day (00:05 UTC) each listed book's profit above its baseline moves to a 'swept' pot that is
                   never traded again, so a good day can't be handed back.
  green days       share of UTC days with a positive realized result.
Nothing here can block a sell: stops and exits always run."""
import time,json,logging,statistics as st
log=logging.getLogger("bot")
DEF=dict(per_trade=0.0025,per_trade_max=0.01,kelly_fraction=0.25,kelly_min_trades=30,daily_loss=0.01,max_drawdown=0.05,
         global_kill=0.06,stopouts_per_day=2,coin_cooldown_h=24,narrative_fail_block=2,fee_edge_mult=3.0,
         sweep_books=["copy","consensus","narrative","perps"],sweep_minute=5,books={})
STOP_REASONS=("stop_loss","breakeven_stop","trail_stop","liquidation","time_stop")
def charter(cfg,book=None):
    """The charter, with per-book overrides from risk.books.<book> (for example the older copy book at 1%)."""
    c=dict(DEF,**{k:v for k,v in ((cfg or {}).get("risk") or {}).items() if k!="books"})
    if book:c.update((((cfg or {}).get("risk") or {}).get("books") or {}).get(book) or {})
    return c
def day(ts=None):return time.strftime("%Y-%m-%d",time.gmtime(ts if ts is not None else time.time()))
# ---- sizing -------------------------------------------------------------------------------------------------
def kelly(pnls):
    """Full Kelly fraction from a list of trade results (W - (1-W)/b, b = average win / average loss). None if it
    can't be measured (no wins or no losses)."""
    w=[p for p in pnls if p>0];l=[-p for p in pnls if p<=0]
    if not w or not l:return None
    W=len(w)/len(pnls);b=st.mean(w)/st.mean(l)
    return W-(1-W)/b
def closed_r(db,book,n=200):
    """The book's last n closed trades in R (pnl divided by the risk planned at entry); falls back to raw pnl."""
    out=[]
    for pnl,meta in db.q("select realized,meta from b_positions where book=? and status='closed' order by closed desc limit ?",(book,n)):
        try:r=(json.loads(meta or "{}").get("x") or {}).get("risk_usd")
        except ValueError:r=None
        out.append(pnl/r if r else pnl)
    return out
def risk_fraction(db,cfg,book,live=False):
    """-> (fraction of the tradable balance to risk on the next trade, plain-words note)."""
    c=charter(cfg,book);base=min(c["per_trade"],c["per_trade_max"]);rs=closed_r(db,book)
    if len(rs)<c["kelly_min_trades"]:return base,f"risk {base:.2%} per trade ({len(rs)}/{c['kelly_min_trades']} trades before Kelly applies)"
    k=kelly(rs)
    if k is None:return base,f"risk {base:.2%} (Kelly not measurable yet)"
    if k<=0:
        if live:return 0.0,"no measurable edge: Kelly <= 0, no real-money trades"
        return base/4,f"Kelly <= 0 (no edge yet): paper keeps collecting evidence at a quarter size ({base/4:.3%})"
    f=min(base,c["kelly_fraction"]*k);return f,f"risk {f:.2%} = min(charter {base:.2%}, 0.25x Kelly {c['kelly_fraction']*k:.2%})"
def size_for_risk(balance,stop_pct,cost_side,frac,cap_pct=None):
    """USD size so that hitting the stop (plus costs on both sides) loses `frac` of the balance."""
    if balance<=0 or stop_pct<=0 or frac<=0:return 0.0
    s=balance*frac/(stop_pct+2*cost_side)
    return min(s,balance*cap_pct) if cap_pct else s
def fee_edge(move_pct,roundtrip_cost,mult=3.0):
    """-> (ok, text). The planned move must pay the round-trip costs `mult` times over."""
    need=roundtrip_cost*mult
    return move_pct>=need,f"target +{move_pct:.1%} vs {mult:g}x costs {need:.1%}"
# ---- blocks for NEW entries (never for exits) -----------------------------------------------------------
EXEMPT=("trend","trend-live")   # Trend Rider v2 keeps its tested rules untouched: only the global kill switch applies to it
def entry_block(db,cfg,book,coin=None,narrative=None,now=None):
    now=now or time.time();c=charter(cfg,book)
    g=db.kv_get("risk:global_kill")
    if g:return f"global kill switch: all books {c['global_kill']:.0%} below their peak (/risk clear)"
    if book in EXEMPT:return None
    so=db.kv_get(f"{book}:stopouts") or {}
    if so.get("day")==day(now) and so.get("n",0)>=c["stopouts_per_day"]:return f"stand-down: {so['n']} stop-outs today, no new entries until 00:00 UTC"
    if coin:
        cd=(db.kv_get(f"{book}:cooldown") or {}).get(coin)
        if cd and now<cd:return f"cooldown: stopped out on this coin, blocked {int((cd-now)/3600)+1}h more"
    if narrative:
        nf=(db.kv_get("narr:fails") or {}).get(str(narrative)) or {}
        if nf.get("blocked"):return f"narrative blocked: {nf.get('n',0)} failed trades, waiting for it to accelerate again"
    return None
def on_exit(db,cfg,book,coin,reason,pnl,narrative=None,now=None):
    """Called after every closed position (and after a stop on a partly closed one)."""
    now=now or time.time();c=charter(cfg,book);d=day(now)
    if book in EXEMPT:return
    if reason in STOP_REASONS and pnl<0:
        so=db.kv_get(f"{book}:stopouts") or {}
        so={"day":d,"n":(so.get("n",0) if so.get("day")==d else 0)+1};db.kv_set(f"{book}:stopouts",so)
        cd=db.kv_get(f"{book}:cooldown") or {};cd={k:v for k,v in cd.items() if v>now};cd[coin]=now+c["coin_cooldown_h"]*3600
        db.kv_set(f"{book}:cooldown",cd)
    if narrative and pnl<0:
        nf=db.kv_get("narr:fails") or {};e=nf.get(str(narrative)) or {"n":0}
        e["n"]=e.get("n",0)+1;e["blocked"]=e["n"]>=c["narrative_fail_block"];e["ts"]=now;nf[str(narrative)]=e;db.kv_set("narr:fails",nf)
def narrative_reaccelerated(db,narrative):
    nf=db.kv_get("narr:fails") or {}
    if str(narrative) in nf and nf[str(narrative)].get("blocked"):nf.pop(str(narrative));db.kv_set("narr:fails",nf);return True
    return False
def stopouts_today(db,book):
    so=db.kv_get(f"{book}:stopouts") or {};return so.get("n",0) if so.get("day")==day() else 0
# ---- global kill switch ---------------------------------------------------------------------------------
def global_check(db,cfg,books,notify=None,now=None):
    """books: names of the paper books to add up. Trips once; /risk clear resets the peak."""
    c=charter(cfg);eq=sum((db.kv_get(f"{b}:last_equity",db.kv_get(f"{b}:cash",0)) or 0) for b in books)
    if eq<=0:return None
    pk=max(db.kv_get("risk:global_peak") or eq,eq);db.kv_set("risk:global_peak",pk)
    if eq<pk*(1-c["global_kill"]) and not db.kv_get("risk:global_kill"):
        db.kv_set("risk:global_kill",{"ts":now or time.time(),"eq":eq,"peak":pk})
        if notify:
            from tgfmt import term,kv,usd,SEP
            notify.send(term("risk --global-kill",[kv("all books",usd(eq)),kv("peak",usd(pk)),kv("drop",f"{1-eq/pk:.1%}"),SEP,"no new entries anywhere.","exits keep running.","reset: /risk clear"],("ERR","🔴 GLOBAL KILL SWITCH")),tier="critical")
        return True
    return False
def global_clear(db,books):
    db.kv_set("risk:global_kill",None)
    db.kv_set("risk:global_peak",sum((db.kv_get(f"{b}:last_equity",db.kv_get(f"{b}:cash",0)) or 0) for b in books))
# ---- profit sweep ---------------------------------------------------------------------------------------
def sweep(db,cfg,papers,notify=None,now=None):
    """papers: {book: Paper}. Once per UTC day after 00:05: profit above the baseline moves from cash to the book's
    'swept' pot (USDC that is never traded again). Returns {book: amount} for the books that swept."""
    now=now or time.time();c=charter(cfg);d=day(now)
    if now%86400<c["sweep_minute"]*60 or db.kv_get("risk:sweep_day")==d:return {}
    db.kv_set("risk:sweep_day",d);out={}
    for b in c["sweep_books"]:
        p=papers.get(b)
        if not p:continue
        try:
            base=p.kget("sweep_base")
            if base is None:base=p.p["start_equity_usd"];p.kset("sweep_base",base)
            tradable=p.equity()-(p.kget("swept") or 0);amt=round(min(tradable-base,p.cash),2)
            if amt>=0.01:
                p.kset("cash",p.cash-amt);p.kset("swept",round((p.kget("swept") or 0)+amt,2));out[b]=amt
                lg=p.kget("sweep_log") or [];lg.append([now,amt]);p.kset("sweep_log",lg[-60:])
        except Exception:log.exception("sweep %s",b)
    if out and notify:
        from tgfmt import term,kv,usd,SEP
        notify.send(term("risk --sweep",[kv(b,usd(a)) for b,a in out.items()]+[SEP,"profit above each book's","baseline is set aside","(never traded again)"],("OK","💰 profit sweep")),tier="digest")
    return out
# ---- green days -----------------------------------------------------------------------------------------
def green_days(db,book=None,since=None):
    """Realized result per UTC day -> dict(green, red, days, pct, by_day). Days without a closed trade don't count."""
    q="select ts,pnl from b_trades where side='sell'"+(" and book=?" if book else "")+(" and ts>=?" if since else "")
    a=tuple(x for x in(book,since) if x)
    by={}
    for ts,pnl in db.q(q,a):by[day(ts)]=by.get(day(ts),0)+(pnl or 0)
    g=sum(1 for v in by.values() if v>0);r=sum(1 for v in by.values() if v<0);n=g+r
    return dict(green=g,red=r,days=n,pct=(g/n if n else None),by_day=dict(sorted(by.items())))
