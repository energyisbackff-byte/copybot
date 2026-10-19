"""Which trader SOURCE actually makes money when we copy its wallets, and do the traders we pick keep winning after we
pick them? Paper only; nothing here trades.
  scorecard (daily, ops.tick): realized paper P&L after costs of the copy and consensus books over a rolling window
      (sources.scorecard.window_days, default 30), credited to the source(s) that suggested each copied trader
      (decisions.metrics.source of the selection that picked them; a consensus exit is split between its traders).
      A source whose copied wallets LOSE after costs (with at least min_exits exits) gets its candidate cap halved
      (floor 10); a second bad check at least cycle_days later switches it off (digest note). A profitable check
      doubles the cap back (up to the base). The last source that is still on is never switched off.
      /sources shows the table, /sources reset NAME switches a source back on.
  agreement (selection.py): a wallet suggested by 2+ independent source families ranks above single-source passers
      of similar P&L: its P&L counts agreement_boost (10%) more per extra family when picks are ranked.
  forward check (weekly, background thread): every daily pick is judged on what the wallet did AFTER we picked it:
      its round trips closed within 7 and 30 days, minus our copy costs (2 x fee + our measured slip). The verdict is
      kept in kv fwd:history and written into Learnings.md. This is the survivorship-bias check: a trader who looked
      good on the past 30 days may not keep winning."""
import time,json,logging,threading,statistics as st
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot");KEY="source_score";DAY=86400
FAMILIES=("fomo","dexscreener_buyers","birdeye","nansen","gmgn")
DEF=dict(enabled=True,window_days=30,min_exits=5,cycle_days=7,base_cap=300,floor_cap=10,agreement_boost=0.10)
def conf(cfg):
    s=((cfg or {}).get("sources") or {}).get("scorecard") or {}
    return dict(DEF,**s)
def family(src):
    """'fomo:7d' -> fomo, 'dexscreener-buyers(3 tok)' -> dexscreener_buyers, 'birdeye:1W' -> birdeye, 'gmgn:kol' -> gmgn."""
    s=str(src or "").strip().lower()
    if s.startswith("dexscreener"):return "dexscreener_buyers"
    for f in FAMILIES:
        if s.startswith(f):return f
    return s.split(":")[0].split("(")[0] or "unknown"
def families(source_field):return sorted({family(x) for x in str(source_field or "").split(",") if x.strip()})
# ---- which sources suggested a trader ------------------------------------------------------------------------------
def sources_of(db,handle,before):
    r=db.q("select metrics from decisions where trader=? and selected=1 and ts<=? order by ts desc limit 1",(handle,before)) or \
      db.q("select metrics from decisions where trader=? order by ts desc limit 1",(handle,))
    try:return families(json.loads(r[0][0] or "{}").get("source")) if r else []
    except Exception:return []
def compute(db,cfg,now=None):
    c=conf(cfg);now=now or time.time();since=now-c["window_days"]*DAY;out={}
    for ts,book,trader,pnl in db.q("select ts,book,trader,pnl from b_trades where side='sell' and book in('copy','consensus') and ts>=?",(since,)):
        hs=[h for h in str(trader or "").split("+") if h];share=(pnl or 0)/max(1,len(hs))
        for h in hs:
            fams=sources_of(db,h,ts) or ["unknown"]
            for f in fams:
                r=out.setdefault(f,dict(pnl=0.0,exits=0,wins=0,wallets=set()))
                r["pnl"]+=share;r["exits"]+=1;r["wins"]+=1 if share>0 else 0;r["wallets"].add(h)
    for r in out.values():r["wallets"]=len(r["wallets"])
    return out
def state(db):return db.kv_get(KEY) or {"rows":{}}
def mult(db,name):return ((state(db).get("rows") or {}).get(name) or {}).get("mult",1.0)
def is_off(db,name):return ((state(db).get("rows") or {}).get(name) or {}).get("status")=="off"
def cap(db,cfg,name):
    c=conf(cfg);base=int((((cfg.get("sources") or {}).get(name) or {}).get("max_candidates")) or c["base_cap"])
    return max(c["floor_cap"],int(base*mult(db,name)))
def update(db,cfg,notify=None,now=None):
    """The daily scorecard: compute, judge, adjust caps, persist. Returns the state."""
    c=conf(cfg);now=now or time.time();S=state(db);rows=S.get("rows") or {};got=compute(db,cfg,now);notes=[]
    enabled=[n for n in FAMILIES if (((cfg.get("sources") or {}).get(n) or {}).get("enabled",n=="fomo")) and (rows.get(n) or {}).get("status")!="off"]
    for name in sorted(set(got)|set(rows)):
        if name=="unknown":continue
        r=dict(rows.get(name) or {"mult":1.0,"bad":0,"status":"on"});g=got.get(name) or dict(pnl=0.0,exits=0,wins=0,wallets=0)
        r.update(pnl=round(g["pnl"],2),exits=g["exits"],wins=g["wins"],wallets=g["wallets"],ts=now)
        if c["enabled"] and r["status"]!="off" and g["exits"]>=c["min_exits"]:
            if g["pnl"]<0 and now-r.get("last_bad",0)>=c["cycle_days"]*DAY:
                r["bad"]=r.get("bad",0)+1;r["last_bad"]=now
                if r["bad"]>=2 and len([n for n in enabled if n!=name])>=1:
                    r["status"]="off";enabled=[n for n in enabled if n!=name]
                    notes.append(f"{name}: OFF (its copied traders lost {usd(g['pnl'],True)} after costs in {c['window_days']}d, 2 checks). /sources reset {name}")
                else:
                    r["mult"]=max(r.get("mult",1.0)/2,c["floor_cap"]/max(1,c["base_cap"]))
                    notes.append(f"{name}: candidate cap halved (its copied traders lost {usd(g['pnl'],True)} after costs in {c['window_days']}d)")
            elif g["pnl"]>0 and (r.get("bad") or r.get("mult",1.0)<1):
                r["bad"]=0;r["mult"]=min(1.0,r.get("mult",1.0)*2);notes.append(f"{name}: back in profit, cap raised")
        rows[name]=r
    S=dict(ts=now,window_days=c["window_days"],rows=rows);db.kv_set(KEY,S)
    if notes and notify:
        for x in notes:notify.send(term("sources --scorecard",wrap(x)),tier="digest",digest="sources: "+x)
    return S
def reset(db,name):
    S=state(db);rows=S.get("rows") or {}
    if name not in rows:return False
    rows[name].update(status="on",bad=0,mult=1.0);S["rows"]=rows;db.kv_set(KEY,S);return True
def table_rows(db,cfg=None):
    S=state(db);rows=S.get("rows") or {}
    if not rows:return ["no copied exits yet: the","scorecard fills as the copy","and consensus books trade"]
    out=[f"last {S.get('window_days',30)} days, after costs:","source     exits     P&L  cap"]
    for n,r in sorted(rows.items(),key=lambda kv_:-kv_[1].get("pnl",0)):
        st_="OFF" if r.get("status")=="off" else f"x{r.get('mult',1.0):g}"
        out.append(f"{n.replace('_buyers','')[:11]:<11}{r.get('exits',0):>4} {usd(r.get('pnl',0),True):>8} {st_:>4}")
    return out
def text(db,cfg):
    return term("sources",table_rows(db,cfg)+[SEP]+wrap("a source whose copied traders lose after costs gets fewer candidates, then is switched off. /sources reset NAME"),("OK","🧾 source scorecard"))
# ---- agreement boost (used by selection.py) ------------------------------------------------------------------------
def rank_pnl(pnl,source_field,cfg):
    n=len(families(source_field));b=conf(cfg)["agreement_boost"]
    return (pnl or 0)*(1+b*max(0,n-1)) if (pnl or 0)>0 else (pnl or 0),n
# ---- forward check of the picks ----------------------------------------------------------------------------------------
def picks(db,since):
    """One pick per wallet per UTC day: [(day_ts, handle, wallet, source, backward_pnl)]."""
    seen={};out=[]
    for ts,h,w,m in db.q("select ts,trader,wallet,metrics from decisions where selected=1 and ts>=? order by ts",(since,)):
        d=int(ts//DAY*DAY)
        if (w,d) in seen:continue
        seen[(w,d)]=1
        try:mm=json.loads(m or "{}")
        except Exception:mm={}
        out.append((ts,h,w,mm.get("source",""),mm.get("pnl")))
    return out
def copy_cost(db,cfg):
    """Our extra cost per copied round trip: 2 x paper fee + our average measured slip vs the trader (if positive)."""
    fee=float((cfg.get("paper") or {}).get("fee_pct",0.02))
    r=db.q("select avg(slip_bps) from b_fills where book='copy' and status='filled' and side='buy' and slip_bps is not null")
    slip=max(0.0,(r[0][0] or 0)/1e4) if r else 0.0
    return 2*fee+slip
def judge(trips,cost):
    """Round trips closed in the forward window -> dict(n, mean shadow return, share positive)."""
    rets=[t["pnl"]/t["cost"]-cost for t in trips if t.get("cost")]
    if not rets:return dict(n=0,mean=None,pos=None)
    return dict(n=len(rets),mean=st.mean(rets),pos=sum(1 for x in rets if x>0)/len(rets))
def forward(db,cfg,hel=None,now=None,pages=5,max_wallets=30):
    """Evaluate every pick whose 7d / 30d window has ended and was not judged yet. Returns the new verdict (or None)."""
    from chain import roundtrips,sol_usd
    now=now or time.time();done=set(db.kv_get("fwd:done") or []);cost=copy_cost(db,cfg)
    try:solp=sol_usd()
    except Exception:solp=150.0
    res={7:[],30:[]};pulled=set();min_cost=float((cfg.get("selection") or {}).get("min_trip_cost_usd",5))
    for ts,h,w,src,bpnl in picks(db,now-40*DAY):
        for win in(7,30):
            k=f"{w}|{int(ts//DAY)}|{win}"
            if k in done or ts+win*DAY>now:continue
            if hel and w not in pulled and len(pulled)<max_wallets:
                pulled.add(w)
                last=db.q("select max(t) from events where wallet=?",(w,))[0][0] or ts
                try:
                    ev,_=hel.history(w,max(last,ts),pages)
                    for e in ev:db.c.execute("insert or ignore into events values(?,?,?,?)",(e["sig"],w,e["t"],json.dumps(e)))
                    db.c.commit()
                except Exception as e:log.warning("forward history %s: %s",h,e)
            evs=[json.loads(p[0]) for p in db.q("select payload from events where wallet=? and t>=? and t<?",(w,ts-30*DAY,ts+win*DAY))]
            trips=[t for t in roundtrips(evs,solp,min_cost) if t["open"]>=ts and t["close"]<ts+win*DAY]
            j=judge(trips,cost);res[win].append(dict(handle=h,wallet=w,source=src,day=int(ts),back_pnl=bpnl,**j));done.add(k)
    db.kv_set("fwd:done",sorted(done)[-5000:])
    if not res[7] and not res[30]:return None
    def summ(L):
        J=[x for x in L if x["n"]]
        return dict(picks=len(L),judged=len(J),kept_winning=sum(1 for x in J if x["mean"]>0),mean=(st.mean(x["mean"] for x in J) if J else None),
                    by_source={f:round(st.mean(x["mean"] for x in J if f in families(x["source"])),4) for f in {f for x in J for f in families(x["source"])}})
    v=dict(ts=now,cost=round(cost,4),d7=summ(res[7]),d30=summ(res[30]))
    H=db.kv_get("fwd:history") or [];H.append(v);db.kv_set("fwd:history",H[-26:])
    try:
        import ledger;ledger.write_learnings(db)
    except Exception:log.exception("learnings after forward check")
    return v
def verdict_lines(v):
    out=[]
    for k,lab in(("d7","7 days"),("d30","30 days")):
        s=v.get(k) or {}
        if not s.get("picks"):continue
        if not s.get("judged"):out.append(f"- {lab} after the pick: {s['picks']} pick(s), no closed trades yet to judge");continue
        out.append(f"- {lab} after the pick: {s['kept_winning']} of {s['judged']} picked traders kept winning after our costs "
                   f"({v['cost']:.1%} a trade); average {s['mean']:+.1%} a trade"+
                   ("; by source: "+", ".join(f"{f} {m:+.1%}" for f,m in sorted(s["by_source"].items())) if s.get("by_source") else ""))
    return out
def learnings_section(db):
    H=db.kv_get("fwd:history") or []
    if not H:return []
    out=["## Trader selection: forward check (survivorship bias)",
         "Each pick is judged on what the trader did AFTER we picked them, minus our copy costs. A pick that only looked good on the past is a selection problem, not bad luck."]
    for v in H[-4:]:out+=[f"### {time.strftime('%Y-%m-%d',time.gmtime(v['ts']))}"]+verdict_lines(v)
    return out+[""]
_lock=threading.Lock()
def forward_async(db_path,cfg,notify):
    """Weekly, from ops.tick: in its own thread with its own connection (Helius calls must never delay exits)."""
    if not _lock.acquire(blocking=False):return False
    def job():
        from db import DB
        try:
            from chain import Helius
            d=DB(db_path);v=forward(d,cfg,Helius())
            if v and notify:
                notify.send(term("selection --forward-check",sum((wrap(x,"","  ") for x in verdict_lines(v)),[])+[SEP]+wrap("written to Learnings.md. nothing changes by itself."),("OK","🔁 weekly pick check")),tier="instant")
        except Exception:log.exception("forward check")
        finally:_lock.release()
    threading.Thread(target=job,daemon=True,name="forward-check").start();return True
