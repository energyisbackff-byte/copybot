import time,json,logging
from chain import roundtrips,sol_usd
log=logging.getLogger("bot");SOLNET=1399811149
def pf(x):
    w=sum(t["pnl"] for t in x if t["pnl"]>0);l=-sum(t["pnl"] for t in x if t["pnl"]<0);return w/l if l else (99.0 if w else 0.0)
def mdd(x):
    e=pk=dd=0
    for t in sorted(x,key=lambda t:t["close"]):e+=t["pnl"];pk=max(pk,e);dd=max(dd,pk-e)
    return dd
def evaluate(trips,sol_share,c):
    m={"trades":len(trips),"pnl":round(sum(t["pnl"] for t in trips),2),"sol_share":round(sol_share,2)}
    if not trips:return False,"no on-chain round trips in window",m
    W=[t["pnl"] for t in trips if t["pnl"]>0];gw=sum(W)
    m.update(pf=round(pf(trips),2),win=round(len(W)/len(trips),2),maxdd=round(mdd(trips),2),top3=round(sum(sorted(W,reverse=True)[:3])/gw,2) if gw else 1)
    if sol_share<c["min_solana_share"]:return False,f"Solana share {sol_share:.0%} < {c['min_solana_share']:.0%}",m
    if len(trips)<c["min_trades"]:return False,f"{len(trips)} trades < min {c['min_trades']}",m
    if m["pnl"]<=0:return False,f"30d realized PnL ${m['pnl']:,.0f} <= 0",m
    if m["pf"]<c["min_profit_factor"]:return False,f"profit factor {m['pf']} < {c['min_profit_factor']}",m
    if m["win"]<c["min_win_rate"]:return False,f"win rate {m['win']:.0%} < {c['min_win_rate']:.0%}",m
    if m["maxdd"]>c["max_dd_frac_of_gross_wins"]*gw:return False,f"drawdown ${m['maxdd']:,.0f} > {c['max_dd_frac_of_gross_wins']:.0%} of gross wins",m
    if m["top3"]>c["max_top3_share"]:return False,f"top-3 wins = {m['top3']:.0%} of gross wins (concentrated)",m
    return True,f"PASS: {len(trips)} trades, PnL ${m['pnl']:,.0f}, PF {m['pf']}, win {m['win']:.0%}, DD ${m['maxdd']:,.0f}, top3 {m['top3']:.0%}",m
def select(db,hel,fomo,cfg):
    from sources import collect
    c=cfg["selection"];now=time.time();since=now-c["lookback_days"]*86400
    allc,stats=collect(db,hel,fomo,cfg)
    ok_share=[x for x in allc if x["sol_share"]>=c["min_solana_share"]]
    cands=ok_share[:c.get("max_candidates",300)]          # FOMO first (collect order), then other sources
    solp=sol_usd();res=[];budget=c.get("max_helius_credits_per_run",20000);start_calls=hel.calls
    for x in allc:
        if x not in cands:
            why=(f"Solana share {x['sol_share']:.0%} < {c['min_solana_share']:.0%}" if x["sol_share"]<c["min_solana_share"] else "over candidate cap")
            res.append((x,False,why+" (skipped history pull)",{"sol_share":x["sol_share"]}))
    for x in cands:
        wal=x["wallet"];spent=(hel.calls-start_calls)*10
        if spent>budget:
            res.append((x,False,f"Helius budget for this run reached ({budget:,} credits), not evaluated",{}));continue
        last=db.q("select max(t) from events where wallet=?",(wal,))[0][0] or since
        pages=c["max_history_pages"] if x["source"].startswith("fomo") else c.get("max_history_pages_external",8)
        try:
            ev,_=hel.history(wal,max(last,since),pages)
            for e in ev: db.c.execute("insert or ignore into events values(?,?,?,?)",(e["sig"],wal,e["t"],json.dumps(e)))
            db.c.commit()
        except Exception as e: log.warning("history %s: %s",x["handle"],e)
        evs=[json.loads(p[0]) for p in db.q("select payload from events where wallet=? and t>=?",(wal,since))]
        trips=[t for t in roundtrips(evs,solp,c["min_trip_cost_usd"]) if t["close"]>=since]
        ok,why,m=evaluate(trips,x["sol_share"],c)
        tpd=len(trips)/c["lookback_days"]
        if ok and tpd>c.get("max_trades_per_day",20):
            ok,why=False,f"looks like a bot: {tpd:.0f} closed trades/day > {c.get('max_trades_per_day',20)}"
        m["source"]=x["source"];res.append((x,ok,why,m))
    passed=sorted([r for r in res if r[1]],key=lambda r:-r[3]["pnl"])[:c["top_n"]]
    picked={r[0]["wallet"] for r in passed}
    for x,ok,why,m in res:
        m=dict(m,source=x["source"]);sel=x["wallet"] in picked
        reason=why if (sel or not ok) else why+" (but not in top %d by PnL)"%c["top_n"]
        db.x("insert into decisions values(?,?,?,?,?,?)",(now,x["handle"],x["wallet"],int(sel),reason,json.dumps(m)))
        if sel or ok: log.info("SELECT %-18s %s [%s] %s","%s"%x["handle"],"PICK" if sel else "----",x["source"],reason)
    log.info("selection: %d candidates (%d evaluated), %d qualified, Helius ~%d credits this run",len(allc),len(cands),sum(1 for r in res if r[1]),(hel.calls-start_calls)*10)
    return [{"handle":r[0]["handle"],"wallet":r[0]["wallet"],"source":r[0]["source"],"reason":r[2]} for r in passed]
