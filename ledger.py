"""Memory: Trade_Ledger.md (one line per exit with every field), Learnings.md (rewritten at every 25-trade review) and
the reviews after every 10 and every 25 closed trades of a book. The files live in the memory folder next to bot.db,
outside the code snapshots, so /update and /rollback never touch them. The `ledger` table is the source of truth; the
Markdown files are readable copies of it. Reviews only SUGGEST changes from accumulated evidence: nothing here
changes a strategy, and suggestions go to paper first."""
import os,time,json,logging,statistics as st
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot")
COLS=("ts","book","asset","dir","narrative","score","entry","exit","size","stop","target","trigger","market","result","pnl","fees","notes","source")
DDL="create table if not exists ledger(id integer primary key,"+",".join(c+(" real" if c in("ts","score","entry","exit","size","stop","target","pnl","fees") else " text") for c in COLS)+")"
HEAD=("# Zetrax Trade Ledger\n\nOne line per exit (a 50% take-profit and the final exit are two lines). Prices in USD, times UTC.\n"
      "Paper books are simulated with real quotes; nothing here is a promise about future results.\n\n"
      "| time (UTC) | book | asset | dir | narrative · score | entry | exit | size | stop | target | trigger | market | result | P&L | fees | notes |\n"
      "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
def folder():
    d=os.environ.get("COPYBOT_MEMORY") or os.path.join(os.path.dirname(os.path.abspath(os.environ.get("COPYBOT_DB","bot.db"))),"memory")
    os.makedirs(d,exist_ok=True);return d
def _px(v):
    if v is None:return "–"
    v=float(v);return f"{v:.2f}" if v>=100 else f"{v:.4g}"
def _cell(s):return str(s if s not in(None,"") else "–").replace("|","/").replace("\n"," ")[:80]
def record(db,**row):
    """Write one exit to the ledger table and Trade_Ledger.md. Never raises (an exit must not fail because of a file)."""
    try:
        db.c.execute(DDL);row.setdefault("ts",time.time())
        r={c:row.get(c) for c in COLS};db.ins("ledger",**r)
        p=os.path.join(folder(),"Trade_Ledger.md");new=not os.path.exists(p)
        line="| "+" | ".join(_cell(x) for x in(time.strftime("%Y-%m-%d %H:%M",time.gmtime(r["ts"])),r["book"],r["asset"],r["dir"],
            (r["narrative"] or "–")+(f" · {r['score']:.0f}" if r["score"] is not None else ""),_px(r["entry"]),_px(r["exit"]),usd(r["size"] or 0),
            _px(r["stop"]),_px(r["target"]),r["trigger"],r["market"],r["result"],usd(r["pnl"] or 0,True),usd(r["fees"] or 0),r["notes"]))+" |\n"
        with open(p,"a") as f:
            if new:f.write(HEAD)
            f.write(line)
        return True
    except Exception:log.exception("ledger record");return False
def rows(db,book=None,n=25,final=True):
    try:db.c.execute(DDL)
    except Exception:pass
    q="select "+",".join(COLS)+" from ledger"+(" where 1=1")+(" and book=?" if book else "")+(" and result not like 'partial%'" if final else "")+" order by id desc limit ?"
    return [dict(zip(COLS,r)) for r in db.q(q,((book,) if book else ())+(n,))]
def last_text(db):
    r=rows(db,n=1,final=False)
    if not r:return term("lasttrade",["no exit recorded yet"],("OK","last trade"))
    x=r[0]
    return term("lasttrade",[kv("time",time.strftime("%d %b %H:%M UTC",time.gmtime(x["ts"]))),kv("book",x["book"]),kv("asset",x["asset"]),kv("dir",x["dir"]),
        kv("why in",(x["narrative"] or x["source"] or "–")+(f" · score {x['score']:.0f}" if x["score"] is not None else "")),kv("entry",_px(x["entry"])),kv("exit",_px(x["exit"])),
        kv("size",usd(x["size"] or 0)),kv("stop",_px(x["stop"])),kv("target",_px(x["target"])),kv("trigger",x["trigger"]),kv("result",x["result"]),
        kv("P&L",usd(x["pnl"] or 0,True)+" net"),kv("fees",usd(x["fees"] or 0)),SEP]+wrap(x["notes"] or ""),
        ("OK",("🟢 " if (x["pnl"] or 0)>=0 else "🔴 ")+"last trade"))
def ledger_text(db,n=8):
    R=rows(db,n=n,final=False)
    if not R:return term("ledger",["no exit recorded yet"],("OK","ledger"))
    out=[]
    for x in R:out+=[f"{time.strftime('%d%b %H:%M',time.gmtime(x['ts']))} {x['book'][:9]:<9} {usd(x['pnl'] or 0,True):>8}"]+wrap(f"{x['asset']} {x['dir']} · {x['trigger']}","  ","  ")
    return term("ledger --last",out+[SEP,"full file: Trade_Ledger.md","(server memory folder) and","/report for the CSV"],("OK",f"last {len(R)} exits"))
# ---- reviews --------------------------------------------------------------------------------------------
def _dd(pnls):
    pk=cum=dd=0.0
    for p in pnls:cum+=p;pk=max(pk,cum);dd=max(dd,pk-cum)
    return dd
def stats(R):
    p=[x["pnl"] or 0 for x in R];w=[v for v in p if v>0];l=[v for v in p if v<=0];fees=sum(x["fees"] or 0 for x in R)
    gl=-sum(l)
    return dict(n=len(R),wins=len(w),losses=len(l),win_rate=len(w)/len(R) if R else None,net=sum(p),avg_win=st.mean(w) if w else 0,
        avg_loss=st.mean(l) if l else 0,rr=(st.mean(w)/-st.mean(l)) if w and l and st.mean(l)<0 else None,max_dd=_dd(list(reversed(p))),fees=fees,
        fee_share=(fees/gl) if gl>0 else None,pf=(sum(w)/gl) if gl>0 else None)
def mistakes(R):
    """Repeated patterns in the trades, with how often they happened. Evidence only, no guesses."""
    out=[];by={}
    for x in R:by.setdefault(x["asset"],[]).append(x)
    rep=[a for a,v in by.items() if sum(1 for y in v if (y["pnl"] or 0)<0 and "stop" in str(y["trigger"]))>=2]
    if rep:out.append(f"stopped out 2+ times on the same coin: {', '.join(rep[:4])} (the 24h cooldown now blocks this)")
    s=stats(R)
    if s["fee_share"] and s["fee_share"]>0.4:out.append(f"fees were {s['fee_share']:.0%} of the losses: the fee-edge gate must stay strict")
    te=sum(1 for x in R if str(x["trigger"]) in("max_hold","time_stop"));
    if te>=max(3,len(R)//4):out.append(f"{te} of {len(R)} trades ended on the time limit: entries are early or targets too far")
    big=[x for x in R if (x["pnl"] or 0)<0 and s["avg_loss"] and (x["pnl"] or 0)<2*s["avg_loss"]]
    if big:out.append(f"{len(big)} loss(es) over 2x the average loss: check slippage on exits")
    return out
def breakdown(R,key):
    d={}
    for x in R:k=x.get(key) or "–";e=d.setdefault(k,[0,0.0]);e[0]+=1;e[1]+=x["pnl"] or 0
    return sorted(d.items(),key=lambda kv_:-kv_[1][0])[:6]
def maybe_review(db,book,notify=None):
    """After every closed trade: a short review every 10 trades, a deep review (and Learnings.md) every 25."""
    try:
        n=db.q("select count(*) from ledger where book=? and result not like 'partial%'",(book,))[0][0]
    except Exception:return None
    done=db.kv_get(f"{book}:reviewed",0) or 0
    if n<10 or n==done or n%10 and n%25:return None
    db.kv_set(f"{book}:reviewed",n);deep=n%25==0
    R=rows(db,book,25 if deep else 10);s=stats(R)
    lines=[kv("book",book),kv("trades",f"last {s['n']} (total {n})"),kv("wins",f"{s['wins']} ({s['win_rate']:.0%})" if s["win_rate"] is not None else "–"),
        kv("net",usd(s["net"],True)),kv("avg win",usd(s["avg_win"])),kv("avg loss",usd(s["avg_loss"])),kv("win/loss",f"{s['rr']:.2f}" if s["rr"] else "–"),
        kv("PF",f"{s['pf']:.2f}" if s["pf"] else "–"),kv("worst run",usd(-s["max_dd"])),kv("fees",usd(s["fees"]))]
    M=mistakes(R)
    if deep:
        lines+=[SEP,"by narrative / source:"]+[f"  {k[:16]:<16}{v[0]:>3} {usd(v[1],True):>9}" for k,v in breakdown(R,"narrative" if any(x["narrative"] for x in R) else "source")]
    lines+=[SEP,"repeated patterns:"]+(sum((wrap(m,"- ") for m in M),[]) or ["  none found"])+[SEP]+wrap("nothing changes by itself; any change goes to paper first",)
    if deep:write_learnings(db)
    if notify:notify.send(term(f"review --{'deep' if deep else 'every-10'} {book}",lines,("OK",f"📋 {'deep ' if deep else ''}review: {n} trades")),tier="instant")
    return dict(n=n,deep=deep,stats=s,mistakes=M)
def write_learnings(db):
    """Learnings.md: repeated mistakes, strengths, weaknesses and candidate improvements, per book, from the ledger."""
    db.c.execute(DDL);books=[b for (b,) in db.q("select distinct book from ledger")]
    out=["# Zetrax Learnings","",f"Rewritten {time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime())} from the last 25 closed trades of each book.",
         "Rules: nothing here changes a strategy by itself; a candidate improvement is tested on paper first; one trade is never evidence.",""]
    for b in books:
        R=rows(db,b,25);s=stats(R)
        if not R:continue
        out+=[f"## {b}",f"- trades: {s['n']}, win rate {s['win_rate']:.0%}, net {usd(s['net'],True)}, profit factor {s['pf']:.2f}" if s["pf"] else f"- trades: {s['n']}, net {usd(s['net'],True)}"]
        st_=[];wk=[]
        if s["rr"] and s["rr"]>=1.5:st_.append(f"winners are {s['rr']:.1f}x the size of losers")
        if s["win_rate"] and s["win_rate"]>=0.55:st_.append(f"win rate {s['win_rate']:.0%}")
        if s["fee_share"] is not None and s["fee_share"]<0.15:st_.append("fees are a small part of the losses")
        if s["rr"] is not None and s["rr"]<1:wk.append(f"average loss is bigger than the average win (ratio {s['rr']:.2f})")
        if s["pf"] is not None and s["pf"]<1.3:wk.append(f"profit factor {s['pf']:.2f} is below the 1.3 bar")
        out+=["### Repeated mistakes"]+(["- "+m for m in mistakes(R)] or ["- none found"])
        out+=["### Strengths"]+(["- "+x for x in st_] or ["- not enough evidence yet"])
        out+=["### Weaknesses"]+(["- "+x for x in wk] or ["- none measured"])
        cand=[]
        if any("same coin" in m for m in mistakes(R)):cand.append("keep the 24h coin cooldown (already on)")
        if s["fee_share"] and s["fee_share"]>0.4:cand.append("raise the fee-edge multiple from 3x to 4x on paper and compare 25 trades")
        if s["rr"] is not None and s["rr"]<1:cand.append("test a wider first target (paper) and compare the next 25 trades")
        out+=["### Candidate improvements (paper first)"]+(["- "+x for x in cand] or ["- none: keep collecting evidence"])+[""]
    try:
        import scorecard;out+=scorecard.learnings_section(db)        # the weekly forward check of the trader picks
    except Exception:log.exception("learnings: forward-check section")
    try:open(os.path.join(folder(),"Learnings.md"),"w").write("\n".join(out)+"\n")
    except Exception:log.exception("learnings")
    return "\n".join(out)
