"""Narrative Rider: the paper book 'narrative' (long spot Solana coins) that only buys what the narrative engine graded
A+. The engine runs in its own thread (it reads slow outside sources); it hands A+ candidates over through the queue
kv 'narr:queue'. The buy itself happens here, in the main loop, so one thread owns the book's money.
Every buy still passes the whole gate of paper.Paper.on_buy (rules, storm, liquidity, contract checks, real quote,
price impact, buy-then-sell test, volatility stop + targets, fee edge, risk size, cooldowns, stand-down, kill switch).
Exits: the planned stop / 50% at the first target / breakeven / trailing stop / final target (paper.py), plus the
TIME-STOP: first target not reached within time_stop_h AND the narrative is decaying -> sell at market."""
import time,json,logging,threading
import narrative as NR
from tgfmt import term,kv,SEP,wrap
log=logging.getLogger("bot")
DEF=dict(max_age_minutes=12,time_stop_h=4)
class Rider:
    def __init__(s,db,book,cfg):
        s.db,s.pp,s.cfg=db,book,cfg;s.c=dict(DEF,**((cfg.get("books") or {}).get("narrative") or {}).get("rider",{}) or {})
    def tick(s,now=None):
        now=now or time.time()
        if not s.pp.enabled:return
        q=s.db.kv_get("narr:queue") or []
        if q:s.db.kv_set("narr:queue",[])
        for c in q:
            if now-c["ts"]>s.c["max_age_minutes"]*60:
                s.pp.signal("narrative:"+c["narrative"],c["mint"],"buy","refused",f"stale: graded {int((now-c['ts'])/60)} min ago",None,{"score":c["score"]});continue
            st=s.db.q("select state from narratives where id=?",(c["narrative"],))
            if st and st[0][0] not in("emerging","accelerating"):
                s.pp.signal("narrative:"+c["narrative"],c["mint"],"buy","refused",f"narrative now {st[0][0]}",None,{"score":c["score"]});continue
            meta=dict(narrative=c["narrative"],narrative_name=c["name"],score=c["score"],evidence=c["evidence"],graded=c["ts"])
            pid=s.pp.on_buy("narrative:"+c["name"][:24],c["mint"],None,None,f"narr:{c['narrative']}:{c['mint'][:8]}:{int(c['ts'])}",{},meta=meta)
            if pid:
                s.db.x("update narrative_scores set traded=1 where narrative=? and mint=? and ts=?",(c["narrative"],c["mint"],c["ts"]))
        s.time_stops(now)
    def time_stops(s,now):
        for pid,meta,op in s.db.q("select id,meta,opened from b_positions where book=? and status='open'",(s.pp.book,)):
            m=json.loads(meta or "{}");x=m.get("x") or {}
            if x.get("tp1_done") or not m.get("narrative") or now-(op or now)<s.c["time_stop_h"]*3600:continue
            st=s.db.q("select state from narratives where id=?",(m["narrative"],))
            if st and st[0][0]=="decaying":
                s.pp.exit_position(pid,"time_stop",f"time-stop: first target not reached in {s.c['time_stop_h']}h and the narrative is decaying")
def engine_thread(db_path,cfg,notify):
    """The narrative engine loop, in its own thread with its own database connection. Never crashes the bot."""
    c=dict(NR.DEF,**(cfg.get("narrative") or {}))
    if not c.get("enabled",True):log.info("narrative engine off (narrative.enabled)");return None
    def job():
        from db import DB
        db=DB(db_path);E=NR.Engine(db,cfg,notify)
        time.sleep(20)
        while True:
            t0=time.time()
            try:
                out=E.cycle()
                ap=[r for r in out if r["grade"]=="A+" and r["mint"]]
                if ap:
                    q=db.kv_get("narr:queue") or []
                    q+=[dict(narrative=r["narrative"],name=r["name"],mint=r["mint"],score=r["score"],evidence=r["evidence"],ts=r_ts) for r in ap for r_ts in [db.kv_get("narr:last")["ts"]]]
                    db.kv_set("narr:queue",q[-20:])
                db.kv_set("narr:alive",time.time())
            except Exception as e:
                log.exception("narrative cycle failed (continuing)")
                try:db.kv_set("narr:error",{"ts":time.time(),"msg":f"{type(e).__name__}: {str(e)[:160]}"})
                except Exception:pass
            time.sleep(max(30,c["cycle_minutes"]*60-(time.time()-t0)))
    t=threading.Thread(target=job,daemon=True,name="narrative");t.start();return t
