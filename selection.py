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
    c=cfg["selection"];now=time.time();since=now-c["lookback_days"]*86400
    cands={}
    for w in c.get("leaderboards",["7d","30d"]):
        try:
            rows=fomo.leaderboard(w,c.get("leaderboard_limit",50) if w!="all" else 100);db.kv_set(f"lb_{w}",{"t":now,"rows":rows})
        except Exception as e:
            log.warning("leaderboard %s failed (%s), using cache",w,e);rows=(db.kv_get(f"lb_{w}") or {}).get("rows",[])
        for r in rows:
            s=(r.get("wallets") or {}).get("solana")
            if s and r["handle"] not in cands:
                tt=r.get("topTokens") or [];share=sum(1 for x in tt if x.get("networkId")==SOLNET)/len(tt) if tt else 0
                cands[r["handle"]]=(s,share)
    cands=dict(sorted([kv for kv in cands.items() if kv[1][1]>=c["min_solana_share"]],key=lambda kv:-kv[1][1])[:c["max_candidates"]]);solp=sol_usd();res=[]
    for h,(wal,share) in cands.items():
        if share<c["min_solana_share"]:
            ok,why,m=False,f"Solana share {share:.0%} < {c['min_solana_share']:.0%} (skipped history pull)",{"sol_share":share}
        else:
            last=db.q("select max(t) from events where wallet=?",(wal,))[0][0] or since
            try:
                ev,_=hel.history(wal,max(last,since),c["max_history_pages"])
                for e in ev: db.c.execute("insert or ignore into events values(?,?,?,?)",(e["sig"],wal,e["t"],json.dumps(e)))
                db.c.commit()
            except Exception as e: log.warning("history %s: %s",h,e)
            evs=[json.loads(p[0]) for p in db.q("select payload from events where wallet=? and t>=?",(wal,since))]
            trips=[t for t in roundtrips(evs,solp,c["min_trip_cost_usd"]) if t["close"]>=since]
            ok,why,m=evaluate(trips,share,c)
        res.append((h,wal,ok,why,m))
    passed=sorted([r for r in res if r[2]],key=lambda r:-r[4]["pnl"])[:c["top_n"]]
    picked={r[0] for r in passed}
    for h,wal,ok,why,m in res:
        reason=why if (h in picked or not ok) else why+" (but not in top %d by PnL)"%c["top_n"]
        db.x("insert into decisions values(?,?,?,?,?,?)",(now,h,wal,int(h in picked),reason,json.dumps(m)))
        log.info("SELECT %-18s %s %s",h,"PICK" if h in picked else "----",reason)
    return [{"handle":r[0],"wallet":r[1],"reason":r[3]} for r in passed]
